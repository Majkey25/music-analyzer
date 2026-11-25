import json
import os
import re
import stat
import struct
import wave
import zipfile
import zlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from io import BufferedReader, BytesIO, FileIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, BinaryIO, cast

_MANIFEST_LIMIT = 12 * 1024**2
_MEDIA_LIMIT = 13 * 1024**3 // 10
_TRACK_LIMIT = 8
_BLOCK = 1024**2
_ID = re.compile(r"[0-9a-f]{32}")
_MEMBER = re.compile(r"media/([0-9a-f]{32})\.wav")
_INVALID = "Archiv je poškozený nebo má nepodporovaný obsah."
_WAV_ERROR = "Zvuk musí být stereo WAV, 44,1 kHz, 16bitové PCM, nejvýš 15 minut."


class _ArchiveError(ValueError):
    pass


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _remove_created(path: Path, identity: os.stat_result) -> None:
    try:
        if os.path.samestat(path.lstat(), identity):
            path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        raise ValueError("Dočasný soubor se nepodařilo odstranit.") from None


@contextmanager
def _input(path: Path) -> Iterator[BinaryIO]:
    before = path.lstat()
    if not _regular(before):
        raise _ArchiveError("Vyber běžný soubor.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    with _ZipReader(FileIO(fd, "rb")) as stream:
        opened = os.fstat(stream.fileno())
        if not _regular(opened) or not os.path.samestat(before, opened):
            raise _ArchiveError("Soubor se během čtení změnil.")
        yield stream
        after = os.fstat(stream.fileno())
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise _ArchiveError("Soubor se během čtení změnil.")


def _check_wave(stream: BinaryIO, size: int) -> None:
    stream.seek(0)
    riff = stream.read(12)
    if (len(riff) != 12 or riff[:4] != b"RIFF" or riff[8:] != b"WAVE"
            or int.from_bytes(riff[4:8], "little") + 8 != size):
        raise _ArchiveError(_WAV_ERROR)
    # wave rounds bits per sample up to bytes and ignores partial final frames.
    seen_format = seen_data = False
    while stream.tell() < size:
        header = stream.read(8)
        if len(header) != 8:
            raise _ArchiveError(_WAV_ERROR)
        kind, length = struct.unpack("<4sI", header)
        end = stream.tell() + length + length % 2
        if end > size:
            raise _ArchiveError(_WAV_ERROR)
        if kind == b"fmt ":
            if seen_format or length < 16:
                raise _ArchiveError(_WAV_ERROR)
            fields = stream.read(16)
            if fields[12:16] != b"\x04\x00\x10\x00":
                raise _ArchiveError(_WAV_ERROR)
            if fields[:2] == b"\xfe\xff":
                extension = stream.read(4)
                if length < 40 or extension[2:4] != b"\x10\x00":
                    raise _ArchiveError(_WAV_ERROR)
            seen_format = True
        if kind == b"data":
            if seen_data or not seen_format or length % 4:
                raise _ArchiveError(_WAV_ERROR)
            seen_data = True
        stream.seek(end)
    stream.seek(0)
    with wave.open(stream, "rb") as audio:
        if (audio.getnchannels() != 2 or audio.getframerate() != 44100
                or audio.getsampwidth() != 2 or not 0 < audio.getnframes() <= 900 * 44100):
            raise _ArchiveError(_WAV_ERROR)
    stream.seek(0)


def _copy(source: IO[bytes], destination: IO[bytes], size: int) -> None:
    remaining = size
    while remaining:
        block = source.read(min(remaining, _BLOCK))
        if not block:
            raise _ArchiveError(_INVALID)
        destination.write(block)
        remaining -= len(block)
    if source.read(1):
        raise _ArchiveError(_INVALID)


def write_archive(destination: Path, manifest: Mapping[str, object], media: Mapping[str, Path]) -> None:
    temporary: Path | None = None
    identity: os.stat_result | None = None
    try:
        if len(media) > _TRACK_LIMIT or any(not _ID.fullmatch(key) for key in media):
            raise _ArchiveError("Archiv obsahuje neplatné ID stopy nebo více než osm stop.")
        encoded = json.dumps(dict(manifest), ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MANIFEST_LIMIT:
            raise _ArchiveError("Manifest přesahuje 12 MiB.")
        existing = destination.lstat() if os.path.lexists(destination) else None
        if existing and not _regular(existing):
            raise _ArchiveError("Vyber běžný cílový soubor.")
        with NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            identity = os.fstat(output.fileno())
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("manifest.json", encoded)
                total = 0
                for key, path in media.items():
                    with _input(path) as stream:
                        info = os.fstat(stream.fileno())
                        if existing and os.path.samestat(existing, info):
                            raise _ArchiveError("Archiv musí mít jiný cílový soubor než zvuk.")
                        total += info.st_size
                        if total > _MEDIA_LIMIT:
                            raise _ArchiveError("Zvuk v archivu přesahuje 1,3 GiB.")
                        _check_wave(stream, info.st_size)
                        with archive.open(f"media/{key}.wav", "w") as member:
                            _copy(stream, member, info.st_size)
        temporary.replace(destination)
        temporary = None
    except _ArchiveError:
        raise
    except (EOFError, OSError, ValueError, TypeError, RuntimeError, struct.error, wave.Error):
        raise ValueError("Archiv se nepodařilo uložit. Zkontroluj vstupní soubory a cíl.") from None
    finally:
        if temporary is not None and identity is not None:
            _remove_created(temporary, identity)


class _ZipReader(BufferedReader):
    # ZipFile reads the entire central directory before exposing member metadata.
    # Nine headers with maximum-length names, comments and extra fields fit in 2 MiB.
    def read(self, size: int = -1, /) -> bytes:
        if size > 2 * _BLOCK:
            raise _ArchiveError(_INVALID)
        return super().read(size if size >= 0 else 2 * _BLOCK)


def _members(archive: zipfile.ZipFile, stream: BinaryIO) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in archive.infolist():
        name = info.orig_filename
        if (name != info.filename or name in result or info.volume
                or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG)
                or info.external_attr & (0x10 | 0x08 | 0x40 | 0x400)
                or info.flag_bits & ~0x080e
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise _ArchiveError(_INVALID)
        # ZipFile uses the central flags and method even when the local header disagrees.
        stream.seek(info.header_offset + 6)
        if stream.read(4) != struct.pack("<HH", info.flag_bits, info.compress_type):
            raise _ArchiveError(_INVALID)
        if name == "manifest.json":
            if info.file_size > _MANIFEST_LIMIT:
                raise _ArchiveError("Manifest přesahuje 12 MiB.")
        elif _MEMBER.fullmatch(name):
            total += info.file_size
        else:
            raise _ArchiveError(_INVALID)
        result[name] = info
    if "manifest.json" not in result or len(result) > _TRACK_LIMIT + 1 or total > _MEDIA_LIMIT:
        raise _ArchiveError(_INVALID)
    return result


def _invalid_constant(_: str) -> None:
    raise _ArchiveError(_INVALID)


def read_archive(source: Path, target: Path) -> tuple[dict[str, object], dict[str, Path]]:
    written: list[tuple[Path, os.stat_result]] = []
    try:
        directory = target.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or target.is_junction() or any(target.iterdir())):
            raise _ArchiveError("Pro import je potřeba prázdná pracovní složka.")
        with _input(source) as stream, zipfile.ZipFile(stream) as archive:
            members = _members(archive, stream)
            metadata = members.pop("manifest.json")
            with archive.open(metadata) as entry, BytesIO() as data:
                _copy(entry, data, metadata.file_size)
                manifest = json.loads(data.getvalue().decode("utf-8"), parse_constant=_invalid_constant)
            if not isinstance(manifest, dict):
                raise _ArchiveError("Manifest musí být objekt JSON.")
            media: dict[str, Path] = {}
            for name, info in members.items():
                key = name[len("media/"):-len(".wav")]
                path = target / f"{key}.wav"
                with archive.open(info) as entry, path.open("x+b") as output:
                    written.append((path, os.fstat(output.fileno())))
                    _copy(entry, output, info.file_size)
                    _check_wave(output, info.file_size)
                media[key] = path
        written.clear()
        return cast(dict[str, object], manifest), media
    except _ArchiveError:
        raise
    except (EOFError, OSError, OverflowError, ValueError, RuntimeError, struct.error, wave.Error,
            zipfile.BadZipFile, zlib.error):
        raise ValueError(_INVALID) from None
    finally:
        for path, identity in written:
            _remove_created(path, identity)
