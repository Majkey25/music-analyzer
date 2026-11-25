import re
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from xml.etree import ElementTree as ET

import mido

from .notation import Meter, Note, Tempo, midi, musicxml

DRUMS = {
    35: ("Acoustic kick", "F", "4", False), 36: ("Kick", "F", "4", False),
    37: ("Side stick", "C", "5", True), 38: ("Snare", "C", "5", False),
    40: ("Electric snare", "C", "5", False), 41: ("Floor tom", "A", "4", False),
    42: ("Closed hi-hat", "G", "5", True), 43: ("Floor tom", "A", "4", False),
    44: ("Pedal hi-hat", "D", "4", True), 45: ("Low tom", "B", "4", False),
    46: ("Open hi-hat", "G", "5", True), 47: ("Tom", "D", "5", False),
    48: ("Tom", "E", "5", False), 49: ("Crash", "A", "5", True),
    50: ("High tom", "E", "5", False), 51: ("Ride", "F", "5", True),
}
PROGRAMS = {"bass": 33, "guitar": 27, "piano": 0, "violin": 40}
PART_NAMES = {"bass": "Bass", "guitar": "Guitar", "violin": "Violin", "drums": "Drums"}

_SVG = "{http://www.w3.org/2000/svg}"
_STAFF_LINE = re.compile(r"M(-?\d+) (-?\d+) L(-?\d+) \2")
_TRANSLATE = re.compile(r"translate\((-?[\d.]+)[ ,]")
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

Mark = Literal["ghost", "dead", "slide-up", "slide-down", "palm-mute"]


@dataclass(slots=True)
class _Line:
    """A horizontal string line segment. Two signs can shorten the same segment."""

    path: ET.Element
    start: int
    end: int
    y: int


def take_tab_marks(score: ET.Element) -> dict[str, set[Mark]]:
    """Read written techniques by engraved note ID for tab_techniques to draw.

    Removes the scoop, plop and P.M. elements from this display copy, because
    Verovio cannot attach articulations to TAB notes and would only log errors.
    """
    marks: dict[str, set[Mark]] = {}
    for note in score.iter("note"):
        found: set[Mark] = set()
        head = note.find("notehead")
        if head is not None and head.text == "x":
            found.add("dead")
        elif head is not None and head.get("parentheses") == "yes":
            found.add("ghost")
        for notations in note.iterfind("notations"):
            for articulations in notations.findall("articulations"):
                if articulations.find("scoop") is not None:
                    found.add("slide-up")
                if articulations.find("plop") is not None:
                    found.add("slide-down")
                notations.remove(articulations)
        for technical in note.iterfind("notations/technical"):
            for other in technical.findall("other-technical"):
                if other.text == "P.M.":
                    found.add("palm-mute")
                    technical.remove(other)
        if found and (key := note.get("id")):
            marks[key] = found
    return marks


def tab_techniques(svg: str, marks: Mapping[str, set[Mark]]) -> str:
    """Engrave the string techniques that Verovio ignores on TAB staves."""
    if not marks:
        return svg
    root = ET.fromstring(svg)
    for system in root.iter(_SVG + "g"):
        if system.get("class") != "system":
            continue
        # Palm mute spans consecutive attacks and rests break it.
        columns: dict[float, bool] = {}
        top = size = 0.0
        for staff in system.iter(_SVG + "g"):
            if staff.get("class") != "staff":
                continue
            lines = [_Line(path, int(match[1]), int(match[3]), int(match[2]))
                     for path in staff.findall(_SVG + "path")
                     if (match := _STAFF_LINE.fullmatch(path.get("d", "")))]
            if not lines:
                continue
            top = min(line.y for line in lines)
            for rest in staff.iter(_SVG + "g"):
                glyph = rest.find(_SVG + "use") if rest.get("class") == "rest" else None
                if glyph is not None and (match := _TRANSLATE.match(glyph.get("transform", ""))):
                    columns.setdefault(float(match[1]), False)
            for note in staff.iter(_SVG + "g"):
                text = note.find(_SVG + "text") if note.get("class") == "note" else None
                span = text.find(_SVG + "tspan") if text is not None else None
                if text is None or span is None or not span.text:
                    continue
                x, baseline = float(text.get("x", "0")), float(text.get("y", "0"))
                size = float(span.get("font-size", "0px").removesuffix("px"))
                mark = marks.get(note.get("id", ""), set())
                columns[x] = columns.get(x, False) or "palm-mute" in mark
                if not mark - {"palm-mute"}:
                    continue
                label = "x" if "dead" in mark else span.text.strip()
                half = size * (.25 * len(label) + (.33 if "ghost" in mark else 0))
                span.text = f"({label})" if "ghost" in mark else label
                # Fret digits sit centered on their string line.
                y = min((line.y for line in lines), key=lambda row: abs(row - baseline + size / 3))
                left, right = x - half - size * .12, x + half + size * .12
                if slide := 1 if "slide-up" in mark else -1 if "slide-down" in mark else 0:
                    # A short stroke rising or falling into the fret, like "/3" or "\3".
                    rise = size * .25 * slide
                    ET.SubElement(note, _SVG + "path", {
                        "d": f"M{left - size * .6:.0f} {y + rise:.0f} L{left:.0f} {y - rise:.0f}",
                        "stroke": "currentColor", "fill": "none",
                        "stroke-width": f"{size * .08:.0f}", "stroke-linecap": "round",
                    })
                    left -= size * .7
                # Widen the gap Verovio cut into the string line around the fret.
                row = [line for line in lines if line.y == y]
                before = max((line for line in row if line.end <= x), key=lambda line: line.end, default=None)
                after = min((line for line in row if line.start >= x), key=lambda line: line.start, default=None)
                if before is not None:
                    before.end = max(before.start, min(before.end, round(left)))
                if after is not None:
                    after.start = min(after.end, max(after.start, round(right)))
            for line in lines:
                if f"M{line.start} {line.y} L{line.end} {line.y}" == line.path.get("d"):
                    continue
                # A stub left between two neighbouring signs would read as a dot.
                end = line.end if line.end - line.start >= size * .35 else line.start
                line.path.set("d", f"M{line.start} {line.y} L{end} {line.y}")
        _palm_mute(system, columns, top, size)
    return ET.tostring(root, encoding="unicode")


def _palm_mute(system: ET.Element, columns: dict[float, bool], top: float, size: float) -> None:
    order = sorted(columns)
    for index, first in enumerate(order):
        if not columns[first] or (index and columns[order[index - 1]]):
            continue
        last = index
        while last + 1 < len(order) and columns[order[last + 1]]:
            last += 1
        group = ET.SubElement(system, _SVG + "g", {"class": "tabPalmMute"})
        left, y = first - size * .35, top - size * .6
        label = ET.SubElement(group, _SVG + "text", {
            "x": f"{left:.0f}", "y": f"{y:.0f}", "font-size": f"{size * .7:.0f}px", "font-style": "italic",
        })
        label.text = "P.M."
        tail, right = left + size * 1.5, order[last] + size * .35
        if right > tail + size * .3:
            ET.SubElement(group, _SVG + "path", {
                "d": f"M{tail:.0f} {y - size * .2:.0f} L{right:.0f} {y - size * .2:.0f}",
                "stroke": "currentColor", "fill": "none",
                "stroke-width": f"{size * .05:.0f}", "stroke-dasharray": f"{size * .25:.0f} {size * .15:.0f}",
            })


def rhythm_below_tab(svg: str) -> str:
    """Move Verovio's TAB rhythm glyphs below the fret numbers."""
    root = ET.fromstring(svg)
    for staff in root.iter(_SVG + "g"):
        if staff.get("class") != "staff":
            continue
        durations = staff.findall(f".//{_SVG}g[@class='tabDurSym']")
        if not durations:
            continue
        lines = [int(match[2]) for path in staff.findall(_SVG + "path")
                 if (match := _STAFF_LINE.fullmatch(path.get("d", "")))]
        if not lines:
            continue
        # Reflect the stems, flags and the engraver's beam segments around the
        # middle of the staff. Fret positions and rests keep their original layout.
        reflection = f"translate(0 {min(lines) + max(lines)}) scale(1 -1)"
        beams = staff.findall(f".//{_SVG}g[@class='beam']/{_SVG}polygon")
        for glyph in [*(duration for duration in durations if len(duration)), *beams]:
            glyph.set("transform", f"{reflection} {glyph.get('transform', '')}".rstrip())
    return ET.tostring(root, encoding="unicode")


def midi_file(notes: list[Note], tempo: list[Tempo], meter: list[Meter], mode: str) -> bytes:
    if mode == "drums":
        # One GM percussion channel has one gate per pitch.
        retriggered: list[Note] = []
        starts: dict[int, float] = {}
        for note in sorted(notes, key=lambda item: (item["start"], item["velocity"]), reverse=True):
            end = min(note["end"], starts.get(note["pitch"], note["end"]))
            if end > note["start"]:
                retriggered.append({**note, "end": end})
            starts[note["pitch"]] = note["start"]
        notes = retriggered
    encoded = midi(notes, tempo, meter)
    if mode not in PROGRAMS and mode != "drums":
        return encoded
    track_file = mido.MidiFile(file=BytesIO(encoded))
    for track in track_file.tracks:
        if mode == "drums":
            for event in track:
                if event.type in {"note_on", "note_off"}:
                    event.channel = 9
        else:
            channels = sorted({event.channel for event in track if event.type == "note_on"})
            for channel in reversed(channels):
                track.insert(0, mido.Message("program_change", channel=channel, program=PROGRAMS[mode]))
    buffer = BytesIO()
    track_file.save(file=buffer)
    return buffer.getvalue()


def score_file(title: str, notes: list[Note], tempo: list[Tempo],
               meter: list[Meter], mode: str) -> bytes:
    if mode not in {"bass", "guitar"}:
        # Techniques survive a mode switch on the notes but only string parts write them.
        notes = [{"start": note["start"], "end": note["end"], "pitch": note["pitch"],
                  "velocity": note["velocity"]} for note in notes]
    encoded = musicxml(title, notes, tempo, meter, mode == "piano")
    if mode not in PART_NAMES:
        return encoded
    root = ET.fromstring(encoded)
    for name in root.findall(".//part-name"):
        name.text = PART_NAMES[mode]
    if mode not in {"bass", "drums"}:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    for clef in root.findall(".//clef"):
        sign = clef.find("sign")
        line = clef.find("line")
        if sign is not None:
            sign.text = "percussion" if mode == "drums" else "F"
        if line is not None:
            line.text = "2" if mode == "drums" else "4"
    if mode == "bass":
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    pitches = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    for note in root.findall(".//note"):
        pitch = note.find("pitch")
        if pitch is None:
            continue
        number = (int(pitch.findtext("octave", "4")) + 1) * 12
        number += pitches[pitch.findtext("step", "C")] + int(pitch.findtext("alter", "0"))
        _, step, octave, cross = DRUMS.get(number, ("Percussion", "C", "5", False))
        index = list(note).index(pitch)
        note.remove(pitch)
        unpitched = ET.Element("unpitched")
        ET.SubElement(unpitched, "display-step").text = step
        ET.SubElement(unpitched, "display-octave").text = octave
        note.insert(index, unpitched)
        if cross:
            head = note.find("notehead")
            if head is None:
                following = {"staff", "beam", "notations", "lyric"}
                index = next((i for i, item in enumerate(note) if item.tag in following), len(note))
                head = ET.Element("notehead")
                note.insert(index, head)
            head.text = "x"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
