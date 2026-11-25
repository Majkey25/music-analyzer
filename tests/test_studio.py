import math
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from xml.etree import ElementTree as ET

import mido
import numpy as np
import soundfile as sf
from werkzeug.http import parse_options_header

from studio import audio as audio_engine
from studio.archive import write_archive
from studio.audio import drums
from studio.musical import fingering, read_midi
from studio.notation import Note
from studio.server import create_app


def note(pitch: int, start: float, end: float) -> Note:
    return {"pitch": pitch, "start": start, "end": end, "velocity": 95}


class MusicalTests(unittest.TestCase):
    def test_custom_tuning_holds_chord_strings_and_respects_fixed_string(self):
        tuning = [17, 24, 29, 34, 39, 44, 49]
        source = [note(17, 0, 1), note(29, 0, 1), note(24, .5, .8), note(2, 1.2, 1.3)]
        positions = fingering(source, tuning, 20, True, [None, 1, None, None])
        self.assertEqual(positions, [{"string": 0, "fret": 0}, {"string": 1, "fret": 5}, None, None])

    def test_midi_tracks_keep_tempo_meter_and_sustain(self):
        song = mido.MidiFile(ticks_per_beat=480)
        song.add_track().extend([
            mido.MetaMessage("time_signature", numerator=6, denominator=8),
            mido.MetaMessage("set_tempo", tempo=600000, time=480),
        ])
        song.add_track("Klavír").extend([
            mido.Message("control_change", control=64, value=127),
            mido.Message("note_on", note=60, velocity=90),
            mido.Message("note_off", note=60, time=480),
            mido.Message("control_change", control=64, value=0, time=480),
        ])
        song.add_track("Bicí").extend([
            mido.Message("note_on", channel=9, note=36, velocity=100, time=480),
            mido.Message("note_off", channel=9, note=36, time=240),
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "song.mid"
            song.save(path)
            tracks, tempo, meter = read_midi(path)
        self.assertEqual([track["mode"] for track in tracks], ["piano", "drums"])
        self.assertAlmostEqual(tracks[0]["notes"][0]["end"], 1.1)
        self.assertAlmostEqual(tracks[1]["notes"][0]["start"], .5)
        self.assertEqual(tempo[-1], {"time": .5, "bpm": 100})
        self.assertEqual(meter[0], {"time": 0, "numerator": 6, "denominator": 8})

    def test_percussion_detects_low_and_high_hits(self):
        rate = 44100
        timeline = np.arange(4410) / rate
        kick = np.sin(math.tau * 70 * timeline) * np.exp(-timeline * 40)
        hat = np.sin(math.tau * 7200 * timeline) * np.exp(-timeline * 120)
        signal = np.zeros(rate * 2)
        for at, hit in [(11025, kick), (33075, hat)]:
            signal[at:at + len(hit)] = hit * .7
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drums.wav"
            sf.write(path, signal, rate)
            detected = drums(path)
        self.assertTrue(any(item["pitch"] == 36 and abs(item["start"] - .25) < .05 for item in detected))
        self.assertTrue(any(item["pitch"] == 42 and abs(item["start"] - .75) < .05 for item in detected))

    def test_program_changes_apply_from_note_on(self):
        song = mido.MidiFile(ticks_per_beat=480)
        song.add_track("Part").extend([
            mido.Message("program_change", program=33),
            mido.Message("note_on", note=40, velocity=90),
            mido.Message("program_change", program=40, time=240),
            mido.Message("note_off", note=40, time=240),
            mido.Message("note_on", note=67, velocity=90),
            mido.Message("note_off", note=67, time=480),
        ])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "programs.mid"
            song.save(path)
            tracks, _, _ = read_midi(path)
        self.assertEqual([(track["mode"], track["notes"][0]["pitch"]) for track in tracks],
                         [("bass", 40), ("violin", 67)])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.client = self.app.test_client()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.app.extensions["studio_executor"].shutdown)

    def test_reject_cross_site_requests_and_bad_tuning(self):
        self.assertEqual(self.client.post("/api/demo", headers={"Origin": "https://example.org"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "remote.example"}).status_code, 400)
        data = {"notes": [note(17, 0, 1)], "frets": 24, "tuning": [True]}
        self.assertEqual(self.client.post("/api/tab", json=data).status_code, 400)
        data["tuning"] = [17, 24, 29, 34]
        self.assertEqual(self.client.post("/api/tab", json=data).get_json(),
                         {"positions": [{"string": 0, "fret": 0}]})

    def test_drum_exports_are_playable_and_engraved(self):
        data = {
            "title": "Test <score>", "mode": "drums",
            "notes": [note(36, 0, .125), {**note(42, .5, .625), "head": "ghost"}],
            "tempo": [{"time": 0, "bpm": 120}],
            "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
        }
        midi = self.client.post("/api/export/midi", json=data)
        self.assertEqual(midi.status_code, 200)
        events = [event for track in mido.MidiFile(file=BytesIO(midi.data)).tracks
                  for event in track if event.type == "note_on"]
        self.assertEqual([event.channel for event in events], [9, 9])
        xml = self.client.post("/api/export/musicxml", json=data)
        self.assertEqual(xml.status_code, 200)
        percussion = ET.fromstring(xml.data)
        self.assertEqual(len(percussion.findall(".//unpitched")), 2)
        self.assertEqual([item.text for item in percussion.findall(".//notehead")], ["x"])
        score = self.client.post("/api/export/score", json=data)
        self.assertEqual(score.status_code, 200)
        self.assertIn("<svg", score.get_json()["svg"])

    def test_drum_export_retriggers_same_pitch_on_one_channel(self):
        data = {
            "mode": "drums", "notes": [note(36, 0, 1), note(36, .25, .75)],
            "tempo": [{"time": 0, "bpm": 120}],
            "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
        }
        midi = self.client.post("/api/export/midi", json=data)
        self.assertEqual(midi.status_code, 200)
        events = [event for track in mido.MidiFile(file=BytesIO(midi.data)).tracks
                  for event in track if event.type in {"note_on", "note_off"}]
        self.assertEqual([(event.type, event.channel, event.time) for event in events],
                         [("note_on", 9, 0), ("note_off", 9, 480),
                          ("note_on", 9, 0), ("note_off", 9, 960)])

    def test_tablature_techniques_are_rendered_for_screen_and_print(self):
        data = {
            "mode": "bass", "notes": [
                {**note(17, 0, .24), "head": "ghost"},
                {**note(20, .25, .49), "slideIn": "up", "palmMute": True},
                {**note(22, .5, .74), "head": "dead"},
            ],
            "tempo": [{"time": 0, "bpm": 120}],
            "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
            "tuning": [17, 24, 29, 34],
            "positions": [{"string": 0, "fret": fret} for fret in [0, 3, 5]],
        }
        screen = self.client.post("/api/export/score", json=data)
        printed = self.client.post("/api/export/print", json=data)
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(printed.status_code, 200)
        ns = {"s": "http://www.w3.org/2000/svg"}
        for svg in (screen.get_json()["svg"], printed.get_json()["pages"][0]):
            root = ET.fromstring(svg)
            frets = [mark.text for mark in root.findall(".//s:g[@class='note']/s:text/s:tspan", ns)]
            self.assertEqual(frets, ["(0)", "3", "x"])
            self.assertTrue(root.findall(".//s:g[@class='note']/s:path", ns))
            self.assertEqual(root.findtext(".//s:g[@class='tabPalmMute']/s:text", namespaces=ns), "P.M.")
        data["notes"][0]["head"] = ["invalid"]
        self.assertEqual(self.client.post("/api/export/score", json=data).status_code, 400)

    def wait_for(self, response):
        self.assertEqual(response.status_code, 202)
        self.app.extensions["studio_executor"].submit(lambda: None).result(timeout=8)
        state = self.client.get(f"/api/projects/{response.get_json()['id']}").get_json()
        self.assertEqual(state["status"], "ready", state["message"])
        return state

    def test_add_midi_and_use_it_on_an_existing_audio_track(self):
        session = self.wait_for(self.client.post("/api/demo"))
        base = f"/api/projects/{session['id']}"
        bass = next(track for track in session["tracks"] if track["mode"] == "bass")
        piano = next(track for track in session["tracks"] if track["mode"] == "piano")
        dest = f"{base}/tracks/{bass['id']}"
        with self.client.get(dest + "/audio") as original:
            self.assertEqual(original.status_code, 200)
            original_audio = original.data
        self.assertEqual(self.client.post(dest + "/midi", json={
            "mode": "bass", "reference": piano["id"],
        }).status_code, 400)

        part = mido.MidiFile(ticks_per_beat=480)
        part.add_track("Bass reference").extend([
            mido.MetaMessage("set_tempo", tempo=600000),
            mido.Message("program_change", program=33),
            mido.Message("note_on", note=17, velocity=95, time=240),
            mido.Message("note_off", note=17, time=480),
        ])
        buffer = BytesIO()
        part.save(file=buffer)
        buffer.seek(0)
        uploaded = self.client.post(base + "/midi", data={"file": (buffer, "bass.mid")})
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.get_json()["tempo"], session["tempo"])
        reference = next(track for track in uploaded.get_json()["tracks"] if track["method"] == "MIDI")
        applied = self.client.post(dest + "/midi", json={"mode": "bass", "reference": reference["id"]})
        self.assertEqual(applied.status_code, 200)
        track = self.client.get(dest).get_json()
        self.assertEqual(track["method"], "MIDI reference")
        self.assertEqual(track["notes"][0]["pitch"], 17)
        self.assertAlmostEqual(track["notes"][0]["start"], .3)
        with self.client.get(dest + "/audio") as updated:
            self.assertEqual(original_audio, updated.data)

    def test_abandoned_failed_imports_do_not_use_all_session_slots(self):
        demo = self.wait_for(self.client.post("/api/demo"))
        for _ in range(5):
            response = self.client.post("/api/restore", data={"project": (BytesIO(b"broken"), "bad.zip")})
            self.assertEqual(response.status_code, 202)
            self.app.extensions["studio_executor"].submit(lambda: None).result(timeout=8)
            failed = self.client.get(f"/api/projects/{response.get_json()['id']}").get_json()
            self.assertEqual(failed["status"], "error")
            self.assertIn("The archive is corrupt", failed["message"])
        self.assertEqual(self.client.get(f"/api/projects/{demo['id']}").get_json()["status"], "ready")
        self.wait_for(self.client.post("/api/demo"))

    def test_upload_write_failure_returns_localized_json_and_releases_project(self):
        with patch("werkzeug.datastructures.FileStorage.save", side_effect=OSError("private path")):
            for route, field, filename in [("/api/projects", "files", "track.wav"),
                                           ("/api/restore", "project", "project.zip")]:
                with self.subTest(route=route):
                    response = self.client.post(route, data={field: (BytesIO(b"sample"), filename)})
                    self.assertEqual(response.status_code, 500)
                    self.assertIn("free space", response.get_json()["error"])
                    self.assertFalse(any(Path(self.directory.name).iterdir()))

    def test_saved_audio_project_reopens_with_note_edits_and_all_practice_sources(self):
        origin, bass, drums_id = "f" * 32, "a" * 32, "d" * 32
        titles = {origin: "Celý mix", bass: "Basa: Drop F", drums_id: "Bicí"}
        root = Path(self.directory.name)
        audio = {}
        for key, amplitude in zip(titles, [.3, .1, .2]):
            path = root / f"{key}.wav"
            sf.write(path, np.full((4410, 2), amplitude, dtype=np.float32), 44100, subtype="PCM_16")
            audio[key] = path
        initial = {"version": 1, "title": "Pracovní skladba/02", "source": origin, "active": bass,
                   "separated": True, "phase": 0, "tempo": [{"time": 0, "bpm": 120}],
                   "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
                   "tracks": [{"id": key, "title": title, "mode": "bass", "method": "Demo",
                               "notes": [note(17, 0, .09)] if key == bass else None}
                              for key, title in titles.items()]}
        archive = root / "fixture.zip"
        write_archive(archive, initial, audio)
        with archive.open("rb") as source:
            state = self.wait_for(self.client.post("/api/restore", data={"project": (source, "fixture.zip")}))
        base = f"/api/projects/{state['id']}"
        self.assertEqual(state["active"], bass)
        with self.client.get(f"{base}/tracks/{bass}/audio?download=1") as stem:
            self.assertEqual(parse_options_header(stem.headers["Content-Disposition"])[1]["filename"],
                             "Basa_ Drop F.wav")
        playback = self.client.post(base + "/practice", json={"tracks": [drums_id]}).get_json()
        response = self.client.get(f"{base}/practice/{playback['key']}")
        signal, rate = sf.read(BytesIO(response.data))
        response.close()
        self.assertEqual(rate, 44100)
        self.assertAlmostEqual(float(signal.mean()), .2, places=3)
        altered = {"tempo": initial["tempo"], "meter": initial["meter"], "phase": 0.01,
                   "active": bass, "edits": {bass: {"notes": [note(29, .01, .1)], "mode": "bass",
                                                  "setup": {"tuning": [17, 24, 29, 34], "frets": 24,
                                                            "preferred": [1]}}},
                   "practice": {"monitor": "@custom", "mix": [drums_id], "speed": .75,
                                "position": .05, "view": "tab", "loop": True}}
        self.wait_for(self.client.post(base + "/save", json=altered))
        download = self.client.get(base + "/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(parse_options_header(download.headers["Content-Disposition"])[1]["filename"],
                         "Pracovní skladba_02.music-analyzer.zip")
        content = download.data
        download.close()
        restored = self.wait_for(self.client.post("/api/restore", data={
            "project": (BytesIO(content), "saved.music-analyzer.zip"),
        }))
        self.assertEqual(restored["phase"], .01)
        self.assertEqual(restored["title"], initial["title"])
        self.assertEqual(restored["practice"], altered["practice"])
        self.assertEqual(restored["source"], origin)
        self.assertEqual(len(restored["tracks"]), 3)
        reopened = self.client.get(f"/api/projects/{restored['id']}/tracks/{bass}").get_json()
        self.assertEqual(reopened["notes"][0]["pitch"], 29)
        self.assertEqual(reopened["setup"]["preferred"], [1])
        restored_base = f"/api/projects/{restored['id']}"
        altered["edits"] = {bass: {"mode": "piano", "notes": [note(60, .02, .1), note(64, .02, .1)]}}
        self.wait_for(self.client.post(restored_base + "/save", json=altered))
        download = self.client.get(restored_base + "/download")
        changed_content = download.data
        download.close()
        changed_state = self.wait_for(self.client.post("/api/restore", data={
            "project": (BytesIO(changed_content), "changed.music-analyzer.zip"),
        }))
        changed_track = self.client.get(f"/api/projects/{changed_state['id']}/tracks/{bass}").get_json()
        self.assertIsNone(changed_track["setup"])
        self.assertEqual(changed_track["mode"], "piano")
        self.assertEqual(len(changed_track["notes"]), 2)

    def test_score_engravings_keep_frets_and_seekable_notes(self):
        changed = {
            "mode": "bass", "notes": [note(17, 0, .25), note(29, .25, .5)],
            "tempo": [{"time": 0, "bpm": 120}],
            "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
            "tuning": [17, 24, 29, 34],
            "positions": [{"string": 0, "fret": 0}, {"string": 1, "fret": 5}],
        }
        response = self.client.post("/api/export/score", json=changed)
        self.assertEqual(response.status_code, 200)
        score = response.get_json()
        graph = ET.fromstring(score["svg"])
        self.assertTrue(any(node.get("id") == "n0-0" for node in graph.iter()))
        self.assertIn(0, score["measures"].values())
        self.assertEqual(score["onsets"]["n0-0"], 0)
        svg = {"s": "http://www.w3.org/2000/svg"}
        matched = []
        for group in graph.findall(".//s:g[@class='tabGrp']", svg):
            digit = group.find("s:g[@class='note']/s:text", svg)
            stem = group.find(".//s:g[@class='stem']/s:path", svg)
            if digit is not None and stem is not None:
                start = re.match(r"M(-?\d+)", stem.attrib["d"])
                assert start is not None
                matched.append(abs(int(digit.attrib["x"]) - int(start[1])))
        self.assertEqual(len(matched), 2)
        self.assertLess(max(matched), 21)
        xml = self.client.post("/api/export/musicxml", json=changed)
        self.assertEqual(ET.fromstring(xml.data).findtext(".//staff-lines"), "4")
        self.assertEqual([item.text for item in ET.fromstring(xml.data).findall(".//technical/fret")],
                         ["0", "5"])

    def test_print_includes_every_score_page(self):
        notes = [note(17, float(i), i + .25) for i in range(96)]
        request = {
            "mode": "bass", "notes": notes, "tuning": [17, 24, 29, 34, 39],
            "positions": [{"string": 0, "fret": 0}] * len(notes),
            "tempo": [{"time": 0, "bpm": 120}],
            "meter": [{"time": 0, "numerator": 4, "denominator": 4}],
        }
        response = self.client.post("/api/export/print", json=request)
        self.assertEqual(response.status_code, 200)
        pages = response.get_json()["pages"]
        self.assertGreater(len(pages), 1)
        self.assertIn('id="n0-', pages[0])
        self.assertIn('id="n95-', pages[-1])
        page = ET.fromstring(pages[0])
        ns = {"s": "http://www.w3.org/2000/svg"}
        staff = page.find(".//s:g[@class='staff']", ns)
        self.assertIsNotNone(staff)
        assert staff is not None
        bottom = max(float(line.attrib["d"].split()[1]) for line in staff.findall("s:path", ns))
        rhythms = [(glyph, stem) for glyph in staff.findall(".//s:g[@class='tabDurSym']", ns)
                   for stem in glyph.findall(".//s:g[@class='stem']/s:path", ns)]
        self.assertTrue(rhythms)
        for rhythm, stem in rhythms:
            offset = float(rhythm.attrib["transform"].split()[1].rstrip(")"))
            coordinates = stem.attrib["d"].split()
            self.assertGreater(offset - max(float(coordinates[1]), float(coordinates[-1])), bottom)
        display = self.client.post("/api/export/score", json=request).get_json()
        self.assertGreater(display["end"], notes[-1]["end"] * 1000)

    def test_mixing_leaves_other_routes_responsive_and_prevents_close(self):
        project = self.wait_for(self.client.post("/api/demo"))
        base = f"/api/projects/{project['id']}"
        stem = next(track for track in project["tracks"] if track["mode"] == "bass")
        started, release = Event(), Event()
        mixed = audio_engine.mix_tracks
        statuses = []

        def deferred(paths: list[Path], dest: Path) -> audio_engine.Wave:
            started.set()
            assert release.wait(3)
            return mixed(paths, dest)

        with patch.object(audio_engine, "mix_tracks", deferred):
            worker = Thread(target=lambda: statuses.append(self.app.test_client().post(
                base + "/practice", json={"tracks": [stem["id"]]},
            ).status_code))
            worker.start()
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(self.client.get(base).status_code, 200)
                self.assertEqual(self.client.delete(base).status_code, 409)
                self.assertFalse(release.is_set())
            finally:
                release.set()
                worker.join()
        self.assertEqual(statuses, [200])


if __name__ == "__main__":
    unittest.main()
