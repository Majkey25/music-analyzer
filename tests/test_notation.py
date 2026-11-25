import unittest
from io import BytesIO
from xml.etree import ElementTree as ET

import mido

from studio.notation import Meter, Note, Tempo, midi, musicxml


def note(start: float, end: float, pitch: int, velocity: int = 80) -> Note:
    return {"start": start, "end": end, "pitch": pitch, "velocity": velocity}


class NotationTests(unittest.TestCase):
    def test_midi_preserves_performance_time_across_tempo_change(self) -> None:
        notes = [note(0.193, 1.8, 60, 85), note(0.72, 1.16, 60, 105)]
        tempo: list[Tempo] = [{"time": 1, "bpm": 60}, {"time": 0, "bpm": 120}]
        meter: list[Meter] = [{"time": 1.5, "numerator": 7, "denominator": 8}]
        parsed = mido.MidiFile(file=BytesIO(midi(notes, tempo, meter)))
        elapsed = 0.0
        active: dict[tuple[int, int], tuple[float, int]] = {}
        result = []
        signatures = []
        changes = []
        for event in parsed:
            elapsed += event.time
            if event.type == "set_tempo":
                changes.append((round(elapsed, 3), event.tempo))
            elif event.type == "time_signature":
                signatures.append(
                    (round(elapsed, 3), event.numerator, event.denominator)
                )
            elif event.type == "note_on":
                self.assertNotIn((event.channel, event.note), active)
                active[event.channel, event.note] = (elapsed, event.velocity)
            elif event.type == "note_off":
                start, velocity = active.pop((event.channel, event.note))
                result.append(
                    (round(start, 3), round(elapsed, 3), event.note, velocity)
                )
        self.assertFalse(active)
        self.assertEqual(sorted(result), [(0.193, 1.8, 60, 85), (0.72, 1.16, 60, 105)])
        self.assertEqual(changes, [(0, 500000), (1, 1000000)])
        self.assertEqual(signatures, [(0, 4, 4), (1.5, 7, 8)])

    def test_score_preserves_chords_overlaps_rests_and_cross_bar_ties(self) -> None:
        notes = [note(0.5, 2.5, p) for p in (60, 64)]
        notes.extend([note(1, 1.5, 67), note(0, 1, 45)])
        meter: list[Meter] = [{"time": 1.5, "numerator": 3, "denominator": 4}]
        tempo: list[Tempo] = [{"time": 0.5, "bpm": 60}]
        xml = musicxml("Keys & bass", notes, tempo, meter, True)
        score = ET.fromstring(xml)
        first, second = score.findall("part/measure")
        self.assertEqual(score.findtext("work/work-title"), "Keys & bass")
        self.assertEqual(first.findtext("attributes/staves"), "2")
        self.assertEqual(first.findtext("direction[offset]/offset"), "32")
        self.assertEqual(second.findtext("attributes/time/beats"), "3")
        self.assertEqual(first.get("implicit"), "yes")
        self.assertEqual(first.findtext("note[chord]/pitch/step"), "E")
        self.assertIsNotNone(first.find("note/rest"))
        self.assertIsNotNone(first.find("note/tie[@type='start']"))
        self.assertIsNotNone(second.find("note/notations/tied[@type='stop']"))
        for measure, bar_length in ((first, 64), (second, 96)):
            cursor = 0
            for child in measure:
                if child.tag == "backup":
                    self.assertEqual(cursor, bar_length)
                    cursor -= int(child.findtext("duration", "0"))
                    self.assertEqual(cursor, 0)
                if child.tag == "note" and child.find("chord") is None:
                    cursor += int(child.findtext("duration", "0"))
            self.assertEqual(cursor, bar_length)
        self.assertEqual(
            {n.findtext("voice") for n in first.findall("note[pitch]")}, {"1", "2", "3"}
        )

    def test_score_renders_in_verovio(self) -> None:
        import verovio

        xml = musicxml("Bass", [note(0, 3, 37)], [], [], False)
        toolkit = verovio.toolkit()
        self.assertTrue(toolkit.loadData(xml.decode("utf-8")))
        self.assertGreater(toolkit.getPageCount(), 0)
        page = ET.fromstring(toolkit.renderToSVG(1))
        classes = {token for el in page.iter() for token in el.get("class", "").split()}
        self.assertTrue({"note", "tie", "clef"}.issubset(classes))
        self.assertEqual(ET.fromstring(xml).findtext(".//clef/sign"), "F")

    def test_empty_and_invalid_events(self) -> None:
        score = ET.fromstring(musicxml("Silence", [], [], [], False))
        self.assertEqual(len(score.findall("part/measure")), 1)
        self.assertIsNotNone(score.find(".//note/rest"))
        for invalid in (0, -1, float("nan")):
            with self.subTest(bpm=invalid), self.assertRaises(ValueError):
                midi([], [{"time": 0, "bpm": invalid}], [])
        with self.assertRaises(ValueError):
            musicxml("Bad interval", [note(1, 1, 45)], [], [], False)
