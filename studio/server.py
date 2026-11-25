import re
import shutil
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.resources import files as package_files
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock, local
from time import monotonic
from typing import Literal, TypedDict
from uuid import uuid4
from xml.etree import ElementTree as ET

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, HTTPException

from . import audio, bass
from .archive import read_archive, write_archive
from .exports import midi_file, score_file
from .musical import fingering, read_midi
from .notation import Meter, Note, Position, Tempo, musicxml

WEB = Path(__file__).resolve().parents[1] / "web"
MAX_NOTES = 20000
MODES = {"bass", "guitar", "piano", "drums", "violin", "melody"}
IDLE_LIMIT = 6 * 3600
MAX_PROJECTS = 4
ARCHIVE_LIMIT = 1400 * 1024 * 1024
MAX_TRACKS = 64


class Setup(TypedDict):
    tuning: list[int]
    frets: int
    preferred: list[int | None]


class PracticeSettings(TypedDict):
    monitor: str
    mix: list[str]
    speed: float
    position: float
    view: str
    loop: bool


@dataclass
class Track:
    title: str
    mode: str
    path: Path | None = None
    wave: audio.Wave | None = None
    notes: list[Note] | None = None
    method: str = ""
    setup: Setup | None = None
    id: str = field(default_factory=lambda: uuid4().hex)

    def summary(self) -> dict[str, str | bool | int | None]:
        return {
            "id": self.id, "title": self.title, "mode": self.mode,
            "audio": self.path is not None, "method": self.method,
            "count": None if self.notes is None else len(self.notes),
        }


@dataclass
class Project:
    title: str
    root: Path
    id: str
    tracks: list[Track] = field(default_factory=list)
    tempo: list[Tempo] = field(default_factory=lambda: [{"time": 0.0, "bpm": 120.0}])
    meter: list[Meter] = field(default_factory=lambda: [{"time": 0.0, "numerator": 4, "denominator": 4}])
    timing: str = "Výchozí"
    phase: float = 0.0
    last_used: float = field(default_factory=monotonic)
    status: Literal["busy", "ready", "error"] = "busy"
    message: str = "Připravuji soubory."
    separated: bool = False
    source: str | None = None
    active_track: str | None = None
    archive: Path | None = None
    settings: PracticeSettings | None = None
    practice: dict[str, tuple[Path, audio.Wave]] = field(default_factory=dict)
    mixing: bool = False


def download_name(title: str, suffix: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", title).strip(" .") or "relace"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", base.partition(".")[0].upper()):
        base = "_" + base
    return base + suffix


def number(value: object, low: float, high: float, label: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not low <= value <= high):
        raise ValueError(f"{label}: očekávám číslo od {low} do {high}.")
    return float(value)


def integer(value: object, low: int, high: int, label: str) -> int:
    result = number(value, low, high, label)
    if not result.is_integer():
        raise ValueError(f"{label}: očekávám celé číslo.")
    return int(result)


def instrument(value: object) -> str:
    if not isinstance(value, str) or value not in MODES:
        raise ValueError("Vyber podporovaný typ přepisu.")
    return value


def parse_tuning(value: object) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("Zadej ladění pro 1 až 16 strun.")
    return [integer(pitch, 0, 127, "Otevřená struna") for pitch in value]


def tab_positions(value: object, notes: list[Note], tuning: list[int]) -> list[Position | None]:
    if not isinstance(value, list) or len(value) != len(notes):
        raise ValueError("Polohy v tabulatuře musí odpovídat počtu not.")
    positions: list[Position | None] = []
    for note, row in zip(notes, value):
        if row is None:
            positions.append(None)
            continue
        if not isinstance(row, dict):
            raise BadRequest("Neplatná poloha v tabulatuře.")
        string = integer(row.get("string"), 0, len(tuning) - 1, "Struna")
        fret = integer(row.get("fret"), 0, 48, "Pražec")
        if tuning[string] + fret != note["pitch"]:
            raise ValueError("Struna a pražec se neshodují s výškou noty.")
        positions.append({"string": string, "fret": fret})
    return positions


def parse_setup(value: object, note_count: int) -> Setup:
    if not isinstance(value, dict):
        raise BadRequest("Neplatné nastavení strun.")
    preferred = value.get("preferred")
    if not isinstance(preferred, list) or len(preferred) != note_count:
        raise BadRequest("Ruční struny musí odpovídat počtu not.")
    return {
        "tuning": parse_tuning(value.get("tuning")),
        "frets": integer(value.get("frets"), 1, 48, "Počet pražců"),
        "preferred": [integer(item, 0, 15, "Ruční struna") if item is not None else None
                      for item in preferred],
    }


def identity(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Neplatný identifikátor stopy.")
    return value


def practice_settings(value: object, ids: set[str], stems: set[str]) -> PracticeSettings | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("mix"), list):
        raise BadRequest("Neplatné nastavení poslechu.")
    monitor = value.get("monitor")
    view = value.get("view")
    if not isinstance(monitor, str) or monitor not in ids | {"", "@all-midi", "@backing-midi", "@backing", "@custom"}:
        raise ValueError("Uložený zdroj poslechu není dostupný.")
    if not isinstance(view, str) or view not in {"tab", "roll", "score"}:
        raise ValueError("Neplatné uložené zobrazení.")
    mix = [identity(key) for key in value["mix"]]
    if len(mix) > 6 or not set(mix) <= stems or (monitor == "@custom" and not mix):
        raise ValueError("Uložený doprovod odkazuje na chybějící stopy.")
    speed = number(value.get("speed"), .5, 1, "Rychlost")
    if speed not in {.5, .75, .9, 1}:
        raise ValueError("Uložená rychlost není podporovaná.")
    return {"monitor": monitor, "view": view, "mix": mix, "speed": speed, "loop": value.get("loop") is True,
            "position": number(value.get("position"), 0, 3600, "Pozice přehrávání")}


def project_bytes(project: Project, data: dict[str, object]) -> tuple[dict[str, object], dict[str, Path]]:
    tempo, meter = parse_grid(data)
    phase = number(data.get("phase"), 0, 3600, "Začátek první doby")
    active = identity(data.get("active"))
    ids = {track.id for track in project.tracks}
    if active not in ids:
        raise ValueError("Vybraná stopa v relaci neexistuje.")
    edits = data.get("edits")
    if not isinstance(edits, dict) or not edits.keys() <= ids:
        raise BadRequest("Neplatné úpravy stop.")
    tracks = []
    media: dict[str, Path] = {}
    for track in project.tracks:
        changed = edits.get(track.id, {})
        if not isinstance(changed, dict):
            raise BadRequest("Neplatné úpravy stopy.")
        mode = instrument(changed.get("mode", track.mode))
        notes = parse_notes(changed["notes"]) if "notes" in changed else track.notes
        if "setup" in changed:
            setup = parse_setup(changed["setup"], len(notes or []))
        else:
            setup = None if "notes" in changed else track.setup
        tracks.append({
            "id": track.id, "title": track.title, "mode": mode, "method": track.method,
            "notes": notes, "setup": setup,
        })
        if track.path is not None:
            media[track.id] = track.path
    return {
        "version": 1, "title": project.title, "tempo": tempo, "meter": meter, "phase": phase,
        "source": project.source, "separated": project.separated, "active": active,
        "tracks": tracks,
        "practice": practice_settings(data.get("practice"), set(media), set(media) - {project.source}),
    }, media


def hydrate(project: Project, manifest: dict[str, object], media: dict[str, Path]) -> None:
    if manifest.get("version") != 1:
        raise ValueError("Tato verze uložené relace není podporována.")
    tempo, meter = parse_grid(manifest)
    phase = number(manifest.get("phase"), 0, 3600, "Začátek první doby")
    title = manifest.get("title")
    rows = manifest.get("tracks")
    if (not isinstance(title, str) or not 1 <= len(title) <= 160
            or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TRACKS):
        raise BadRequest("Relace nemá platný název nebo stopy.")
    tracks: list[Track] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BadRequest("Neplatná stopa v uložené relaci.")
        key = identity(row.get("id"))
        if key in seen:
            raise ValueError("Relace má duplicitní stopy.")
        seen.add(key)
        label, method = row.get("title"), row.get("method")
        if not isinstance(label, str) or not 1 <= len(label) <= 160 or not isinstance(method, str):
            raise BadRequest("Neplatný název stopy.")
        notes = parse_notes(row["notes"]) if row.get("notes") is not None else None
        path = media.get(key)
        setup = parse_setup(row["setup"], len(notes or [])) if row.get("setup") is not None else None
        tracks.append(Track(label, instrument(row.get("mode")), path=path,
                            wave=audio.waveform(path) if path else None, notes=notes,
                            method=method[:24], setup=setup, id=key))
    source = identity(manifest["source"]) if manifest.get("source") is not None else None
    active = identity(manifest.get("active"))
    if not media.keys() <= seen or (source is not None and source not in media) or active not in seen:
        raise ValueError("V uložené relaci chybí odkazovaná stopa.")
    settings = practice_settings(manifest.get("practice"), set(media), set(media) - {source})
    project.title, project.tracks = title, tracks
    project.tempo, project.meter, project.phase = tempo, meter, phase
    project.timing = "Uložená mřížka"
    project.source, project.active_track = source, active
    project.separated = manifest.get("separated") is True
    project.settings = settings


def parse_notes(value: object) -> list[Note]:
    if not isinstance(value, list) or len(value) > MAX_NOTES:
        raise ValueError(f"Přepis smí obsahovat nejvýš {MAX_NOTES} not.")
    result: list[Note] = []
    for index, row in enumerate(value, 1):
        if not isinstance(row, dict):
            raise BadRequest(f"Neplatná nota {index}.")
        start = number(row.get("start"), 0, 3600, f"Nota {index}: začátek")
        end = number(row.get("end"), start, 3600, f"Nota {index}: konec")
        if end <= start:
            raise ValueError(f"Nota {index} musí mít kladnou délku.")
        result.append({
            "start": start, "end": end,
            "pitch": integer(row.get("pitch"), 0, 127, f"Nota {index}: výška"),
            "velocity": integer(row.get("velocity"), 1, 127, f"Nota {index}: síla"),
        })
    return result


def parse_grid(data: Mapping[str, object]) -> tuple[list[Tempo], list[Meter]]:
    tempos, meters = data.get("tempo"), data.get("meter")
    if not isinstance(tempos, list) or not isinstance(meters, list):
        raise BadRequest("Chybí tempo nebo takt.")
    if not 1 <= len(tempos) <= 2500 or not 1 <= len(meters) <= 2500:
        raise ValueError("Rytmická mapa je prázdná nebo příliš velká.")
    tempo: list[Tempo] = []
    meter: list[Meter] = []
    for row in tempos:
        if not isinstance(row, dict):
            raise BadRequest("Neplatný záznam tempa.")
        tempo.append({"time": number(row.get("time"), 0, 3600, "Čas tempa"),
                      "bpm": number(row.get("bpm"), 3.6, 1000, "Tempo")})
    for row in meters:
        if not isinstance(row, dict):
            raise BadRequest("Neplatný záznam taktu.")
        denominator = integer(row.get("denominator"), 1, 32, "Jmenovatel")
        if denominator not in {1, 2, 4, 8, 16, 32}:
            raise ValueError("Jmenovatel musí být mocnina dvou.")
        meter.append({"time": number(row.get("time"), 0, 3600, "Čas taktu"),
                      "numerator": integer(row.get("numerator"), 1, 32, "Počet dob"),
                      "denominator": denominator})
    for rows in ([row["time"] for row in tempo], [row["time"] for row in meter]):
        if rows[0] != 0 or any(b <= a for a, b in pairwise(rows)):
            raise ValueError("Změny rytmu musí začínat v nule a vzestupně pokračovat.")
    return tempo, meter


def create_app(root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(WEB), static_url_path="/assets")
    app.config.update(MAX_CONTENT_LENGTH=256 * 1024 * 1024, MAX_FORM_PARTS=8,
                      TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"])
    projects: dict[str, Project] = {}
    lock = Lock()
    engraving = local()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio")
    app.extensions["studio_executor"] = executor

    @app.before_request
    def check_same_origin():
        if request.is_json:
            request.max_content_length = 4 * 1024 * 1024
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403, "Aplikace přijímá požadavky jen z místní stránky.")
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            abort(403, "Požadavek má jiný původ.")

    @app.after_request
    def headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; media-src 'self'; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(error: ValueError):
        return jsonify(error=str(error)), 400

    @app.errorhandler(OSError)
    def storage_error(error: OSError):
        app.logger.warning("Local I/O failed (%s)", type(error).__name__)
        return jsonify(error="Soubor se nepodařilo zpracovat. Zkontroluj volné místo a přístup k souboru."), 500

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        message = (
            "Soubor přesahuje limit pro uloženou relaci." if request.path == "/api/restore"
            else "Požadavek přesahuje povolenou velikost."
        ) if error.code == 413 else error.description
        return jsonify(error=message), error.code or 500

    def lookup(id: str) -> Project:
        project = projects.get(id)
        if project is None:
            abort(404, "Relace vypršela nebo už neexistuje. Nahraj soubor znovu.")
        project.last_used = monotonic()
        return project

    def track_for(project: Project, id: str) -> Track:
        for track in project.tracks:
            if track.id == id:
                return track
        abort(404, "Stopa neexistuje.")

    def snapshot(project: Project):
        return jsonify(
            id=project.id, title=project.title, status=project.status, message=project.message,
            tempo=project.tempo, meter=project.meter, phase=project.phase, timing=project.timing,
            separated=project.separated, tracks=[track.summary() for track in project.tracks],
            source=project.source, active=project.active_track, practice=project.settings,
        )

    def run(project: Project, job: Callable[[Callable[[str], None]], None]) -> None:
        project.status = "busy"

        def progress(message: str) -> None:
            with lock:
                project.message = message

        def execute() -> None:
            try:
                job(progress)
            except Exception as error:  # noqa: BLE001
                if not isinstance(error, (ValueError, BadRequest)):
                    app.logger.warning("Processing failed (%s)", type(error).__name__)
                with lock:
                    if isinstance(error, BadRequest) and error.description:
                        project.message = error.description
                    elif isinstance(error, ValueError):
                        project.message = str(error)
                    else:
                        project.message = (
                            "Zpracování selhalo. Zkontroluj volné místo, instalaci a při "
                            "prvním spuštění separace připojení k internetu."
                        )
                    project.status = "error"
            else:
                with lock:
                    project.message = "Hotovo."
                    project.status = "ready"
            finally:
                with lock:
                    project.last_used = monotonic()

        executor.submit(execute)

    def create_project(title: str) -> Project:
        for closed in root.glob("closed-*"):
            executor.submit(shutil.rmtree, closed, ignore_errors=True)
        expired = [p for p in projects.values()
                   if p.status != "busy" and not p.mixing and monotonic() - p.last_used > IDLE_LIMIT]
        for project in expired:
            projects.pop(project.id)
            executor.submit(shutil.rmtree, project.root, ignore_errors=True)
        if len(projects) >= MAX_PROJECTS:
            failed = min(
                (p for p in projects.values() if p.status == "error" and not p.tracks),
                key=lambda p: p.last_used,
                default=None,
            )
            if failed is None:
                abort(409, "Zavři jednu z ostatních relací nebo restartuj aplikaci.")
            projects.pop(failed.id)
            executor.submit(shutil.rmtree, failed.root, ignore_errors=True)
        id = uuid4().hex
        directory = root / id
        directory.mkdir()
        project = Project(title, directory, id)
        projects[id] = project
        return project

    def ensure_idle(project: Project) -> None:
        if project.status == "busy" or project.mixing:
            abort(409, "Počkej na dokončení běžící úlohy.")

    @app.get("/")
    def index():
        return send_file(WEB / "index.html")

    @app.post("/api/projects")
    def import_project():
        clips = [file for file in request.files.getlist("files") if file.filename]
        if not 1 <= len(clips) <= 2:
            raise ValueError("Vyber jeden soubor, případně audio a MIDI společně.")
        files = {}
        for clip in clips:
            name = (clip.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            ext = Path(name.lower()).suffix
            key = "midi" if ext in {".mid", ".midi"} else "audio"
            if key in files or (key == "audio" and ext not in audio.AUDIO_FORMATS):
                raise ValueError("Vyber nejvýš jedno WAV, MP3 nebo FLAC a jedno MIDI.")
            files[key] = (clip, ext, name)
        title = Path(next(iter(files.values()))[2]).stem[:160]
        with lock:
            project = create_project(title)
        try:
            for key, (clip, ext, _) in files.items():
                target = project.root / (key + ext)
                clip.save(target)
                if key == "midi" and target.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("MIDI smí mít nejvýš 8 MB.")
        except (OSError, ValueError):
            with lock:
                projects.pop(project.id, None)
            shutil.rmtree(project.root, ignore_errors=True)
            raise

        def load(progress: Callable[[str], None]) -> None:
            tracks: list[Track] = []
            tempo: list[Tempo] = project.tempo
            meter: list[Meter] = project.meter
            timing = "Výchozí"
            phase = 0.0
            if "audio" in files:
                progress("Načítám průběh audia.")
                _, ext, name = files["audio"]
                source = project.root / ("audio" + ext)
                mix = project.root / "source.wav"
                audio.normalize(source, mix)
                source.unlink()
                tracks.append(Track(name[:160], "melody", path=mix, wave=audio.waveform(mix)))
                if "midi" not in files:
                    progress("Odhaduji tempo a doby.")
                    bpm, phase = audio.rhythm(mix)
                    if bpm:
                        tempo = [{"time": 0.0, "bpm": bpm}]
                        timing = "Odhad z audia"
            if "midi" in files:
                progress("Načítám MIDI stopy, tempa a takty.")
                try:
                    midi_tracks, tempo, meter = read_midi(project.root / ("midi" + files["midi"][1]))
                    parse_grid({"tempo": tempo, "meter": meter})
                except (ValueError, EOFError, OSError, IndexError, KeyError):
                    raise ValueError("MIDI je poškozené nebo má nepodporované časování.") from None
                for result in midi_tracks:
                    notes = parse_notes(result["notes"])
                    tracks.append(Track(result["title"][:120], result["mode"], notes=notes, method="MIDI"))
                timing = "Mapa MIDI"
            if len(tracks) > MAX_TRACKS - (6 if "audio" in files else 0):
                raise ValueError("Relace obsahuje příliš mnoho stop.")
            with lock:
                project.tracks, project.tempo, project.meter = tracks, tempo, meter
                project.phase, project.timing = phase, timing
                project.source = tracks[0].id if "audio" in files else None

        with lock:
            run(project, load)
            return snapshot(project), 202

    @app.post("/api/demo")
    def demo():
        def prepare(progress: Callable[[str], None]) -> None:
            progress("Připravuji studiovou ukázku.")
            bass = [17, 17, 24, 22, 25, 27, 29, 22]
            tracks = [
                Track("Drop F basa", "bass", method="Demo", notes=[
                    {"start": i * 0.25, "end": i * 0.25 + 0.2,
                     "pitch": bass[(i // 8 + i % 8) % 8], "velocity": 100}
                    for i in range(48)
                ]),
                Track("Klavír", "piano", method="Demo", notes=[
                    {"start": beat * .5, "end": beat * .5 + .46, "pitch": pitch, "velocity": 90}
                    for beat, pitches in enumerate(([48, 60, 64, 67], [45, 57, 60, 64],
                                                    [41, 57, 60, 65], [43, 55, 59, 62]) * 6)
                    for pitch in pitches
                ]),
                Track("Bicí", "drums", method="Demo", notes=[
                    {"start": i * .25, "end": i * .25 + .07, "pitch": pitch, "velocity": 90}
                    for i in range(48) for pitch in ([42, 36] if i % 4 == 0
                                                    else [42, 38] if i % 4 == 2 else [42])
                ]),
            ]
            stems = []
            for track in tracks:
                assert track.notes is not None
                track.path = project.root / f"{track.mode}.wav"
                track.wave = audio.demo_audio(track.path, track.notes, track.mode)
                stems.append(track.path)
            mix = project.root / "source.wav"
            mix_track = Track("Celá skladba", "melody", path=mix,
                              wave=audio.mix_tracks(stems, mix), method="Demo")
            with lock:
                project.tracks = [mix_track, *tracks]
                project.active_track = tracks[0].id
                project.source, project.separated = mix_track.id, True
                project.timing = "Demo"

        with lock:
            project = create_project("Studiová ukázka")
            run(project, prepare)
            return snapshot(project), 202

    @app.get("/api/projects/<id>")
    def project_status(id: str):
        with lock:
            return snapshot(lookup(id))

    @app.post("/api/restore")
    def restore():
        request.max_content_length = ARCHIVE_LIMIT
        source = request.files.get("project")
        if source is None or not (source.filename or "").lower().endswith(".zip"):
            raise ValueError("Vyber uloženou relaci ve formátu ZIP.")
        with lock:
            project = create_project("Načítám relaci")
        incoming = project.root / "incoming.zip"
        try:
            source.save(incoming)
        except OSError:
            with lock:
                projects.pop(project.id, None)
            shutil.rmtree(project.root, ignore_errors=True)
            raise

        def load(progress: Callable[[str], None]) -> None:
            progress("Otevírám uložené stopy a partitury.")
            directory = project.root / "media"
            directory.mkdir()
            try:
                manifest, media = read_archive(incoming, directory)
                hydrate(project, manifest, media)
            finally:
                incoming.unlink(missing_ok=True)

        with lock:
            run(project, load)
            return snapshot(project), 202

    @app.post("/api/projects/<id>/save")
    def save(id: str):
        data = payload()
        with lock:
            project = lookup(id)
            ensure_idle(project)
            manifest, media = project_bytes(project, data)

            def write(progress: Callable[[str], None]) -> None:
                progress("Pakuji upravené noty a zvukové stopy.")
                ready = project.root / f"save-{uuid4().hex}.zip"
                write_archive(ready, manifest, media)
                with lock:
                    old, project.archive = project.archive, ready
                if old is not None:
                    try:
                        old.unlink(missing_ok=True)
                    except OSError:
                        app.logger.info("Previous archive is still downloading.")

            project.message = "Připravuji soubor relace."
            run(project, write)
            return snapshot(project), 202

    @app.get("/api/projects/<id>/download")
    def download(id: str):
        with lock:
            project = lookup(id)
            ensure_idle(project)
            if project.archive is None:
                abort(404, "Relace zatím nemá připravený soubor.")
            return send_file(project.archive, as_attachment=True,
                             download_name=download_name(project.title, ".music-analyzer.zip"))

    @app.delete("/api/projects/<id>")
    def close_project(id: str):
        with lock:
            project = lookup(id)
            ensure_idle(project)
            closed = project.root.with_name(f"closed-{project.id}")
            try:
                project.root.rename(closed)
            except OSError:
                abort(409, "Audio se ještě používá. Počkej, až se přehrávač uvolní, a zavři relaci znovu.")
            projects.pop(id)
        shutil.rmtree(closed, ignore_errors=True)
        return "", 204

    @app.get("/api/projects/<id>/tracks/<track_id>")
    def track_detail(id: str, track_id: str):
        with lock:
            track = track_for(lookup(id), track_id)
            return jsonify(**track.summary(), notes=track.notes, wave=track.wave, setup=track.setup)

    @app.get("/api/projects/<id>/tracks/<track_id>/audio")
    def listen(id: str, track_id: str):
        with lock:
            track = track_for(lookup(id), track_id)
            if track.path is None:
                abort(404, "Tato stopa nemá audio.")
            return send_file(track.path, conditional=True, mimetype="audio/wav",
                             download_name=download_name(track.title, ".wav"),
                             as_attachment=request.args.get("download") == "1")

    @app.post("/api/projects/<id>/separate")
    def separate(id: str):
        def process(mix: Path, progress: Callable[[str], None]) -> None:
            paths = audio.separate(mix, project.root, progress)
            tracks = [
                Track(title, mode, path=paths[key], wave=audio.waveform(paths[key]), method="Demucs")
                for key, (title, mode) in audio.STEMS.items()
            ]
            with lock:
                project.tracks.extend(tracks)
                project.separated = True

        with lock:
            project = lookup(id)
            ensure_idle(project)
            if project.source is None or project.separated:
                abort(409, "Separace vyžaduje zvukový mix, který ještě nebyl rozdělen.")
            mix = track_for(project, project.source).path
            if mix is None:
                abort(409, "Zvuk mixu už není dostupný.")
            project.message = "Připravuji separaci."
            run(project, lambda progress: process(mix, progress))
            return snapshot(project), 202

    @app.post("/api/projects/<id>/practice")
    def practice(id: str):
        ids = payload().get("tracks")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 6:
            raise ValueError("Vyber jednu až šest zvukových stop pro poslech.")
        chosen = list(dict.fromkeys(identity(item) for item in ids))
        with lock:
            project = lookup(id)
            ensure_idle(project)
            media = []
            for key in chosen:
                track = track_for(project, key)
                if track.path is None or key == project.source:
                    raise ValueError("Doprovod lze složit z oddělených zvukových stop.")
                media.append(track.path)
            key = uuid4().hex
            destination = project.root / f"practice-{key}.wav"
            project.mixing = True
        try:
            wave = audio.mix_tracks(media, destination)
            response = jsonify(key=key, wave=wave)
            with lock:
                project.practice[key] = (destination, wave)
                if len(project.practice) > 2:
                    old = next(iter(project.practice))
                    path, _ = project.practice.pop(old)
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        app.logger.info("Previous practice audio is still streaming.")
            return response
        except Exception:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            raise
        finally:
            with lock:
                project.mixing = False
                project.last_used = monotonic()

    @app.get("/api/projects/<id>/practice/<key>")
    def practice_audio(id: str, key: str):
        with lock:
            value = lookup(id).practice.get(key)
            if value is None:
                abort(404, "Tento mix už není dostupný.")
            return send_file(value[0], mimetype="audio/wav", conditional=True)

    @app.post("/api/projects/<id>/tracks/<track_id>/transcribe")
    def transcribe(id: str, track_id: str):
        data = payload()
        mode = instrument(data.get("mode"))
        minimum = integer(data.get("minimum", 21), 0, 127, "Dolní tón")
        maximum = integer(data.get("maximum", 108), minimum, 127, "Horní tón")

        def process(path: Path, progress: Callable[[str], None]) -> None:
            progress("Hledám údery bicích." if mode == "drums" else "Přepisuji tóny z vybrané stopy.")
            if mode == "drums":
                notes, method = audio.drums(path), "Odhad bicích"
            elif mode == "bass":
                notes, method = bass.transcribe(path, minimum, maximum), "Analýza basy"
            else:
                notes, method = audio.melody(path, mode, minimum, maximum), "Basic Pitch"
            if not notes:
                raise ValueError("Ve stopě jsem nenašel noty. Zkus jinou stopu nebo rozsah.")
            checked = parse_notes(sorted(notes, key=lambda note: (note["start"], note["pitch"])))
            with lock:
                track.notes, track.mode = checked, mode
                track.method = method
                track.setup = None

        with lock:
            project = lookup(id)
            ensure_idle(project)
            track = track_for(project, track_id)
            path = track.path
            if path is None:
                abort(409, "MIDI už obsahuje noty. Pro přepis vyber zvukovou stopu.")
            project.message = "Připravuji přepis."
            run(project, lambda progress: process(path, progress))
            return snapshot(project), 202

    @app.post("/api/tab")
    def tab():
        data = payload()
        strings = parse_tuning(data.get("tuning"))
        frets = integer(data.get("frets"), 1, 48, "Počet pražců")
        notes = parse_notes(data.get("notes"))
        if any(a["start"] > b["start"] for a, b in pairwise(notes)):
            raise ValueError("Noty pro tabulaturu musí být seřazené podle času.")
        preferred = data.get("preferred")
        if preferred is not None:
            if not isinstance(preferred, list) or len(preferred) != len(notes):
                raise BadRequest("Pevné struny musí odpovídat počtu not.")
            preferred = [integer(value, 0, 15, "Pevná struna") if value is not None else None
                         for value in preferred]
        return jsonify(positions=fingering(notes, strings, frets, data.get("bass") is True, preferred))

    @app.post("/api/export/<kind>")
    def export(kind: str):
        if kind not in {"midi", "musicxml", "score"}:
            abort(404, "Neznámý formát exportu.")
        data = payload()
        notes = parse_notes(data.get("notes"))
        if not notes:
            raise ValueError("Přepis je prázdný.")
        tempo, meter = parse_grid(data)
        mode = instrument(data.get("mode"))
        title = str(data.get("title") or "Přepis")[:160]
        if kind == "midi":
            return Response(midi_file(notes, tempo, meter, mode), mimetype="audio/midi",
                            headers={"Content-Disposition": "attachment; filename=prepis.mid"})
        if data.get("tuning") is not None and mode in {"bass", "guitar"}:
            tuning = parse_tuning(data.get("tuning"))
            positions = tab_positions(data.get("positions"), notes, tuning)
            xml = musicxml(title, notes, tempo, meter, False, tuning=tuning,
                           tab_positions=positions)
        else:
            xml = score_file(title, notes, tempo, meter, mode)
        if kind == "musicxml":
            return Response(xml, mimetype="application/vnd.recordare.musicxml+xml",
                            headers={"Content-Disposition": "attachment; filename=prepis.musicxml"})
        import verovio

        toolkit = getattr(engraving, "toolkit", None)
        if toolkit is None:
            toolkit = verovio.toolkit(False)
            toolkit.setResourcePath(str(package_files("verovio").joinpath("data")))
            engraving.toolkit = toolkit
        width = integer(data.get("width", 1600), 900, 2700, "Šířka partitury")
        toolkit.setOptions({"pageWidth": width, "pageHeight": 1050, "scale": 46,
                            "header": "none", "footer": "none", "adjustPageHeight": True,
                            "breaks": "auto", "mnumInterval": 1, "minLastJustification": .35,
                            "pageMarginLeft": 85, "pageMarginRight": 65})
        display = ET.fromstring(xml)
        for name in display.findall(".//part-name"):
            name.text = None
        if not toolkit.loadData(ET.tostring(display, encoding="unicode")):
            raise ValueError("Notovou sazbu se nepodařilo vykreslit.")
        pages = toolkit.getPageCount()
        if not pages:
            raise ValueError("Notová sazba je prázdná.")
        page = min(integer(data.get("page", 1), 1, 10000, "Strana"), pages)
        timeline = toolkit.renderToTimemap({"includeMeasures": True})
        if data.get("at") is not None:
            at = number(data.get("at"), 0, 3600, "Pozice partitury") * 1000
            identity = None
            for row in timeline:
                if row.get("on"):
                    identity = row["on"][0]
                    if row["tstamp"] >= at:
                        break
            if identity:
                page = toolkit.getPageWithElement(identity) or page
        return jsonify(svg=toolkit.renderToSVG(page), pages=pages, page=page,
                       measures={entry["measureOn"]: entry["tstamp"]
                                 for entry in timeline if entry.get("measureOn")})

    return app


def payload() -> dict[str, object]:
    data = request.get_json()
    if not isinstance(data, dict):
        raise BadRequest("Očekávám objekt JSON.")
    return data


def launch(port: int, open_browser: bool = True) -> None:
    import webbrowser

    from waitress.server import create_server

    with TemporaryDirectory(prefix="music-analyzer-", ignore_cleanup_errors=True) as directory:
        app = create_app(Path(directory))
        server = create_server(app, host="127.0.0.1", port=port,
                               max_request_body_size=ARCHIVE_LIMIT)
        url = f"http://127.0.0.1:{server.effective_port}/"  # pyright: ignore[reportAttributeAccessIssue]
        print(f"Music Analyzer: {url}", flush=True)
        try:
            if open_browser:
                webbrowser.open(url)
            server.run()
        finally:
            server.close()
            app.extensions["studio_executor"].shutdown(wait=True, cancel_futures=True)
