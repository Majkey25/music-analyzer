import io
import stat
import struct
import tempfile
import unittest
import wave
import zipfile
import zlib
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import patch

from studio.archive import read_archive, write_archive

FIRST = "0ab1cdef000040008000000000000001"
SECOND = "0ab1cdef000040008000000000000002"


def stereo_wav() -> bytes:
    with io.BytesIO() as stream:
        with wave.open(stream, "wb") as writer:
            writer.setparams((2, 2, 44100, 0, "NONE", ""))
            writer.writeframes(bytes(range(256)) * 32)
        return stream.getvalue()


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "stereo.wav"
        self.source.write_bytes(stereo_wav())
        self.bundle = root / "project.zip"
        self.target = root / "import"
        self.target.mkdir()

    def pack(self, entries: Iterable[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
        with zipfile.ZipFile(self.bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in entries:
                archive.writestr(name, contents)

    def assert_rejected(self) -> None:
        with self.assertRaises(ValueError) as failure:
            read_archive(self.bundle, self.target)
        self.assertNotIn(str(self.bundle.parent), str(failure.exception))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_roundtrip(self) -> None:
        manifest = {"title": "Cvičení", "notes": [None, 2]}
        write_archive(self.bundle, manifest, {FIRST: self.source})
        with zipfile.ZipFile(self.bundle) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", f"media/{FIRST}.wav"})
        restored, media = read_archive(self.bundle, self.target)
        self.assertEqual(restored, manifest)
        self.assertEqual(set(media), {FIRST})
        self.assertEqual(media[FIRST].parent, self.target)
        self.assertEqual(media[FIRST].read_bytes(), self.source.read_bytes())

    def test_manifest_only(self) -> None:
        manifest = {"title": "a" * (3 * 1024**2)}
        write_archive(self.bundle, manifest, {})
        self.assertEqual(read_archive(self.bundle, self.target), (manifest, {}))

    def test_allowlist(self) -> None:
        for name in ("../outside", "notes.txt", "media/", f"media\\{FIRST}.wav",
                     f"media/{FIRST.upper()}.wav", "/manifest.json"):
            with self.subTest(name=name):
                self.pack([("manifest.json", b"{}"), (name, b"")])
                self.assert_rejected()
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR):
            member = zipfile.ZipInfo(f"media/{FIRST}.wav")
            member.external_attr = (mode | 0o644) << 16
            self.pack([("manifest.json", b"{}"), (member, stereo_wav())])
            self.assert_rejected()
        with self.assertWarns(UserWarning):
            self.pack([("manifest.json", b"{}"), ("manifest.json", b"{}")])
        self.assert_rejected()

    def test_invalid_manifest_and_limits(self) -> None:
        for manifest in (b"[]", b"{", b'{"bad": NaN}', b"\xff"):
            self.pack([("manifest.json", manifest)])
            self.assert_rejected()
        self.pack([])
        self.assert_rejected()
        self.pack([("manifest.json", b"{}")] + [(f"media/{i:032x}.wav", b"") for i in range(9)])
        self.assert_rejected()
        self.pack([("manifest.json", b"{}"), (f"media/{FIRST}.wav", stereo_wav())])
        with patch("studio.archive._MEDIA_LIMIT", len(stereo_wav()) - 1):
            self.assert_rejected()
        with patch("studio.archive._MANIFEST_LIMIT", 1):
            self.assert_rejected()

    def test_encrypted_flag_and_inconsistent_headers(self) -> None:
        self.pack([("manifest.json", b"{}")])
        original = self.bundle.read_bytes()
        central_flags = original.index(b"PK\x01\x02") + 8
        for offsets in ((6,), (central_flags,), (6, central_flags)):
            changed = bytearray(original)
            for offset in offsets:
                changed[offset] |= 1
            self.bundle.write_bytes(changed)
            self.assert_rejected()

    def test_unicode_path_alias_is_rejected(self) -> None:
        member = zipfile.ZipInfo("manifest.json")
        alias = struct.pack("<BI", 1, zlib.crc32(b"manifest.json")) + b"../alias"
        member.extra = struct.pack("<HH", 0x7075, len(alias)) + alias
        self.pack([(member, b"{}")])
        self.assert_rejected()

    def test_late_wav_error_removes_only_imported_files(self) -> None:
        audio = stereo_wav()
        for invalid in (audio[:-4], audio[:34] + b"\x0c\x00" + audio[36:],
                        audio[:22] + b"\x01\x00" + audio[24:],
                        audio[:24] + (48000).to_bytes(4, "little") + audio[28:]):
            self.pack([("manifest.json", b"{}"), (f"media/{FIRST}.wav", audio),
                       (f"media/{SECOND}.wav", invalid)])
            self.assert_rejected()
        keep = self.target / "existing"
        keep.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            read_archive(self.bundle, self.target)
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_failed_export_preserves_existing_files(self) -> None:
        self.bundle.write_bytes(b"previous")
        for media in ({"../bad": self.source}, {FIRST: self.bundle}, {FIRST: self.target}):
            with self.assertRaises(ValueError):
                write_archive(self.bundle, {}, media)
            self.assertEqual(self.bundle.read_bytes(), b"previous")
        self.source.write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            write_archive(self.bundle, {}, {FIRST: self.source})
        self.assertEqual(self.bundle.read_bytes(), b"previous")
        self.assertEqual(set(self.bundle.parent.iterdir()), {self.source, self.target, self.bundle})
