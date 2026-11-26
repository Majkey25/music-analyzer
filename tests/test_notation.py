import json
import re
import unittest
from io import BytesIO
from xml.etree import ElementTree as ET

import mido

from studio.exports import rhythm_below_tab, tab_techniques, take_tab_marks
from studio.notation import Meter, Note, Position, Tempo, midi, musicxml
from studio.server import parse_notes


def note(start: float, end: float, pitch: int, velocity: int = 80) -> Note:
    return {"start": start, "end": end, "pitch": pitch, "velocity": velocity}


TUNING = [28, 33, 38, 43]


def techniques() -> tuple[list[Note], list[Position | None]]:
    """Ghost slide-up, palm-muted dead note, slide-down tied over the bar, plain note."""
    notes: list[Note] = [
        {**note(0, .5, 31), "head": "ghost", "slideIn": "up"},
        {**note(.5, 1, 31), "head": "dead", "palmMute": True},
        {**note(1, 3, 35), "slideIn": "down", "palmMute": True},
        note(3, 3.5, 31),
    ]
    return notes, [{"string": 0, "fret": n["pitch"] - TUNING[0]} for n in notes]


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

    def test_tab_groups_straight_eighths_without_rewriting_the_score(self) -> None:
        eighths = [note(i / 4, (i + 1) / 4, 31) for i in range(8)]
        mixed = [note(4, 4.125, 31), note(4.125, 4.375, 31), note(4.375, 4.5, 31),
                 note(4.5, 4.75, 31), note(4.875, 5, 31)]
        notes = eighths + mixed
        setup = {"tuning": TUNING, "tab_positions": [{"string": 0, "fret": 3}] * len(notes)}
        standard = ET.fromstring(musicxml("Bass", notes, [], [], False, **setup))
        compact = ET.fromstring(musicxml("Bass", notes, [], [], False, **setup, compact_tab=True))
        self.assertEqual([(n.get("id"), n.findtext("duration")) for n in standard.iter("note") if n.get("id")],
                         [(n.get("id"), n.findtext("duration")) for n in compact.iter("note") if n.get("id")])
        pair = ["begin", "end"]
        first, silent, third = list(compact.iter("measure"))
        self.assertEqual([beam.text for beam in next(standard.iter("measure")).iter("beam")], pair * 4)
        self.assertEqual([beam.text for beam in first.iter("beam")], ["begin", "continue", "continue", "end"] * 2)
        self.assertIsNotNone(silent.find("note/rest[@measure='yes']"))
        self.assertEqual([beam.text for beam in third.iter("beam")], ["begin", "continue", "end"])

        compound: list[Meter] = [{"time": 0.0, "numerator": 6, "denominator": 8}]
        six = ET.fromstring(musicxml("Bass", eighths[:6], [], compound, False, tuning=TUNING,
                                    tab_positions=setup["tab_positions"][:6], compact_tab=True))
        self.assertEqual([beam.text for beam in six.iter("beam")], ["begin", "continue", "end"] * 2)

    def test_empty_and_invalid_events(self) -> None:
        score = ET.fromstring(musicxml("Silence", [], [], [], False))
        self.assertEqual(len(score.findall("part/measure")), 1)
        self.assertIsNotNone(score.find(".//note/rest"))
        for invalid in (0, -1, float("nan")):
            with self.subTest(bpm=invalid), self.assertRaises(ValueError):
                midi([], [{"time": 0, "bpm": invalid}], [])
        with self.assertRaises(ValueError):
            musicxml("Bad interval", [note(1, 1, 45)], [], [], False)

    def test_string_techniques_mark_only_the_attack(self) -> None:
        notes, positions = techniques()
        score = ET.fromstring(musicxml("Bass", notes, [], [], False, tuning=TUNING, tab_positions=positions))
        by_id = {key: item for item in score.iter("note") if (key := item.get("id"))}
        self.assertEqual(sorted(by_id), ["n0-0", "n1-32", "n2-128", "n2-64", "n3-192"])
        ghost, dead, slide, tied, plain = (by_id[key] for key in ("n0-0", "n1-32", "n2-64", "n2-128", "n3-192"))
        self.assertIsNotNone(ghost.find("notehead[@parentheses='yes']"))
        self.assertEqual(ghost.findtext("notehead"), "normal")
        self.assertIsNotNone(ghost.find("notations/articulations/scoop"))
        tags = [child.tag for child in ghost]
        self.assertLess(tags.index("notehead"), tags.index("staff"))
        self.assertEqual(dead.findtext("notehead"), "x")
        self.assertEqual(dead.findtext("notations/technical/fret"), "3")
        self.assertEqual(dead.findtext("notations/technical/other-technical"), "P.M.")
        self.assertIsNotNone(slide.find("notations/articulations/plop"))
        self.assertIsNone(score.find(".//slide"))
        for unmarked in (tied, plain):
            self.assertIsNone(unmarked.find("notehead"))
            self.assertIsNone(unmarked.find("notations/articulations"))
            self.assertIsNone(unmarked.find("notations/technical/other-technical"))
        self.assertIsNotNone(tied.find("notations/tied[@type='stop']"))
        bare: list[Note] = [note(n["start"], n["end"], n["pitch"]) for n in notes]
        self.assertEqual(midi(notes, [], []), midi(bare, [], []))

    def test_tab_engraving_draws_each_technique_once(self) -> None:
        import verovio

        notes, positions = techniques()
        display = ET.fromstring(musicxml("Bass", notes, [], [], False, tuning=TUNING, tab_positions=positions))
        marks = take_tab_marks(display)
        self.assertEqual(marks, {"n0-0": {"ghost", "slide-up"}, "n1-32": {"dead", "palm-mute"},
                                 "n2-64": {"slide-down", "palm-mute"}})
        self.assertIsNone(display.find(".//articulations"))
        self.assertIsNone(display.find(".//other-technical"))
        toolkit = verovio.toolkit()
        self.assertTrue(toolkit.loadData(ET.tostring(display, encoding="unicode")))
        page = ET.fromstring(tab_techniques(rhythm_below_tab(toolkit.renderToSVG(1)), marks))
        ns = {"s": "http://www.w3.org/2000/svg"}
        frets = {group.get("id"): "".join(group.itertext()).strip()
                 for group in page.iterfind(".//s:g[@class='note']", ns)}
        self.assertEqual(frets, {"n0-0": "(3)", "n1-32": "x", "n2-64": "7", "n2-128": "7", "n3-192": "3"})
        rises = {}
        for group in page.iterfind(".//s:g[@class='note']", ns):
            for stroke in group.findall("s:path", ns):
                y1, y2 = (int(value) for value in re.findall(r"-?\d+", stroke.get("d", ""))[1::2])
                rises[group.get("id")] = y2 < y1
        self.assertEqual(rises, {"n0-0": True, "n2-64": False})
        palm = page.findall(".//s:g[@class='tabPalmMute']", ns)
        self.assertEqual(len(palm), 1)
        self.assertEqual([text.text for text in palm[0].findall("s:text", ns)], ["P.M."])
        self.assertEqual(len(palm[0].findall("s:path[@stroke-dasharray]", ns)), 1)
        self.assertEqual(tab_techniques(svg := toolkit.renderToSVG(1), {}), svg)

    def test_note_techniques_are_optional_and_validated(self) -> None:
        rows = [{**note(0, 1, 40), "head": "ghost", "slideIn": "down", "palmMute": True},
                {**note(1, 2, 40), "head": None, "slideIn": None, "palmMute": False}, note(2, 3, 40)]
        self.assertEqual(parse_notes(json.loads(json.dumps(rows))), [rows[0], note(1, 2, 40), note(2, 3, 40)])
        for field, value in (("head", "x"), ("slideIn", "in"), ("palmMute", 1), ("palmMute", "yes")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                parse_notes([{**note(0, 1, 40), field: value}])
