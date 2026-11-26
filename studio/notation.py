"""Export second-based transcriptions as measured sheet music or performance MIDI."""

from bisect import bisect_right
from collections import defaultdict
from io import BytesIO
from itertools import groupby
from math import isfinite
from statistics import median
from typing import Literal, NotRequired, TypedDict
from xml.etree import ElementTree as ET

import mido


class Note(TypedDict):
    start: float
    end: float
    pitch: int
    velocity: int
    # Written string techniques change notation only, never MIDI. slideIn is an
    # indeterminate slide into this note, not a slide between two notes.
    head: NotRequired[Literal["ghost", "dead"]]
    slideIn: NotRequired[Literal["up", "down"]]
    palmMute: NotRequired[bool]


class Tempo(TypedDict):
    time: float
    bpm: float


class Meter(TypedDict):
    time: float
    numerator: int
    denominator: int


class Position(TypedDict):
    string: int
    fret: int


def _time(value: float) -> float:
    if not isfinite(value) or value < 0:
        raise ValueError("Event time must be finite and nonnegative")
    return value


class _Clock:
    def __init__(self, tempo: list[Tempo]) -> None:
        changes = {0.0: 120.0}
        for change in tempo:
            bpm = change["bpm"]
            if not isfinite(bpm) or bpm <= 0 or not 1 <= 60_000_000 / bpm <= 0xFFFFFF:
                raise ValueError("Tempo is outside the MIDI range")
            changes[_time(change["time"])] = bpm
        self.times = sorted(changes)
        self.bpms = [changes[t] for t in self.times]
        self.beats = [0.0]
        for i in range(1, len(self.times)):
            elapsed = self.times[i] - self.times[i - 1]
            self.beats.append(self.beats[-1] + elapsed * self.bpms[i - 1] / 60)

    def ticks(self, time: float, resolution: int) -> int:
        i = bisect_right(self.times, time) - 1
        return round(
            (self.beats[i] + (time - self.times[i]) * self.bpms[i] / 60) * resolution
        )


def _meters(meter: list[Meter]) -> dict[float, tuple[int, int]]:
    result = {0.0: (4, 4)}
    for change in meter:
        n, d = change["numerator"], change["denominator"]
        if not 1 <= n <= 255 or d not in (1, 2, 4, 8, 16, 32, 64, 128):
            raise ValueError("Unsupported time signature")
        result[_time(change["time"])] = (n, d)
    return dict(sorted(result.items()))


def _validate(notes: list[Note]) -> None:
    for note in notes:
        if _time(note["end"]) <= _time(note["start"]):
            raise ValueError("Note end must follow its start")
        if not 0 <= note["pitch"] <= 127 or not 1 <= note["velocity"] <= 127:
            raise ValueError("Invalid MIDI pitch or note-on velocity")


def midi(notes: list[Note], tempo: list[Tempo], meter: list[Meter]) -> bytes:
    """Keep performance times to the nearest MIDI tick."""
    _validate(notes)
    clock = _Clock(tempo)
    ppq = 960
    score = mido.MidiFile(type=0, ticks_per_beat=ppq)
    track = score.add_track()
    events = []
    for t, bpm in zip(clock.times, clock.bpms):
        events.append(
            (
                clock.ticks(t, ppq),
                0,
                mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)),
            )
        )
    for t, (n, d) in _meters(meter).items():
        events.append(
            (
                clock.ticks(t, ppq),
                0,
                mido.MetaMessage("time_signature", numerator=n, denominator=d),
            )
        )
    # Independent note-offs need separate channels for overlapping unisons.
    busy: dict[tuple[int, int], int] = {}
    for note in sorted(notes, key=lambda item: item["start"]):
        pitch = note["pitch"]
        start = clock.ticks(note["start"], ppq)
        end = max(start + 1, clock.ticks(note["end"], ppq))
        channel = next(
            (c for c in range(16) if c != 9 and busy.get((pitch, c), 0) <= start), None
        )
        if channel is None:
            raise ValueError("More than 15 overlapping notes of the same pitch")
        busy[pitch, channel] = end
        events.append(
            (
                start,
                2,
                mido.Message(
                    "note_on", channel=channel, note=pitch, velocity=note["velocity"]
                ),
            )
        )
        events.append(
            (end, 1, mido.Message("note_off", channel=channel, note=pitch, velocity=0))
        )
    previous = 0
    for tick, _, event in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(event.copy(time=tick - previous))
        previous = tick
    output = BytesIO()
    score.save(file=output)
    return output.getvalue()


_DIVISIONS = 32
_VALUES = (
    (128, "whole"),
    (64, "half"),
    (32, "quarter"),
    (16, "eighth"),
    (8, "16th"),
    (4, "32nd"),
    (2, "64th"),
    (1, "128th"),
)
_DURATIONS = sorted(
    [(size, name, False) for size, name in _VALUES]
    + [(size * 3 // 2, name, True) for size, name in _VALUES if size >= 2],
    reverse=True,
)
_STEPS = "CCDDEFFGGAAB"


def _tag(parent: ET.Element, name: str, value: str | int) -> None:
    ET.SubElement(parent, name).text = str(value)


def _beam(measure: ET.Element, numerator: int, denominator: int, *, compact_tab: bool) -> None:
    """Beam straight eighths in fours on 4/4 TAB. Group other runs by beat."""
    beat_size = 4 * _DIVISIONS // denominator
    if denominator == 8 and numerator > 3 and numerator % 3 == 0:
        beat_size *= 3
    cursor = 0
    run: list[tuple[ET.Element, int, int]] = []

    def flush_beat(events: list[tuple[ET.Element, int, int]]) -> None:
        for level in range(1, 5):
            groups: list[list[ET.Element]] = [[]]
            for row, _, count in events:
                if count >= level:
                    groups[-1].append(row)
                elif groups[-1]:
                    groups.append([])
            for group in groups:
                if len(group) < 2:
                    continue
                for i, note in enumerate(group):
                    beam = ET.Element("beam", number=str(level))
                    beam.text = "begin" if not i else "end" if i == len(group) - 1 else "continue"
                    index = next((j for j, node in enumerate(note) if node.tag == "notations"), len(note))
                    note.insert(index, beam)

    def flush() -> None:
        half_bar = 2 * beat_size
        extended = set()
        if compact_tab and (numerator, denominator) == (4, 4):
            for i, (_, onset, _) in enumerate(run):
                if onset % half_bar == 0 and i + 4 <= len(run) and all(
                    count == 1 and duration.find("dot") is None
                    for duration, _, count in run[i:i + 4]
                ):
                    extended.add(onset)

        def group_start(event: tuple[ET.Element, int, int]) -> int:
            onset = event[1]
            half = onset - onset % half_bar
            return half if half in extended else onset - onset % beat_size

        for _, events in groupby(run, key=group_start):
            flush_beat(list(events))
        run.clear()

    flags = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4}
    for event in measure:
        if event.tag == "backup":
            flush()
            cursor -= int(event.findtext("duration", "0"))
        elif event.tag == "note" and event.find("chord") is None:
            count = flags.get(event.findtext("type", ""), 0)
            if count and event.find("rest") is None:
                run.append((event, cursor, count))
            else:
                flush()
            cursor += int(event.findtext("duration", "0"))
    flush()


def _span(
    measure: ET.Element,
    start: int,
    end: int,
    onset: int,
    release: int,
    pitches: list[tuple[int, int]],
    voice: int,
    staff: int,
    positions: list[Position | None] | None,
    tuning: list[int] | None,
    notes: list[Note],
) -> None:
    while start < end:
        duration, kind, dotted = next(
            item for item in _DURATIONS if item[0] <= end - start
        )
        for index, (pitch, identity) in enumerate(pitches or [(-1, -1)]):
            # Techniques belong to the attack, so tied continuations stay plain.
            mark = notes[identity] if pitch >= 0 and start == onset else None
            note = ET.SubElement(measure, "note")
            if index:
                ET.SubElement(note, "chord")
            if pitch < 0:
                ET.SubElement(note, "rest")
            else:
                note.set("id", f"n{identity}-{start}")
                p = ET.SubElement(note, "pitch")
                _tag(p, "step", _STEPS[pitch % 12])
                if pitch % 12 in (1, 3, 6, 8, 10):
                    _tag(p, "alter", 1)
                _tag(p, "octave", pitch // 12 - 1)
            _tag(note, "duration", duration)
            ties = []
            if pitches and start > onset:
                ties.append("stop")
            if pitches and start + duration < release:
                ties.append("start")
            for tie in ties:
                ET.SubElement(note, "tie", type=tie)
            _tag(note, "voice", voice)
            _tag(note, "type", kind)
            if dotted:
                ET.SubElement(note, "dot")
            if mark is not None and (head := mark.get("head")):
                notehead = ET.SubElement(note, "notehead")
                notehead.text = "x" if head == "dead" else "normal"
                if head == "ghost":
                    notehead.set("parentheses", "yes")
            _tag(note, "staff", staff)
            placement = positions[identity] if positions is not None and identity >= 0 else None
            slide = mark.get("slideIn") if mark is not None else None
            muted = mark is not None and mark.get("palmMute", False)
            if ties or placement or slide or muted:
                notations = ET.SubElement(note, "notations")
                for tie in ties:
                    ET.SubElement(notations, "tied", type=tie)
                if slide:
                    # Scoop and plop are MusicXML's indeterminate slides into a note.
                    articulations = ET.SubElement(notations, "articulations")
                    ET.SubElement(articulations, "scoop" if slide == "up" else "plop")
                fingered = placement is not None and tuning is not None
                if fingered or muted:
                    technical = ET.SubElement(notations, "technical")
                    if placement is not None and tuning is not None:
                        _tag(technical, "string", len(tuning) - placement["string"])
                        _tag(technical, "fret", placement["fret"])
                    if muted:
                        _tag(technical, "other-technical", "P.M.")
        start += duration


def musicxml(
    title: str, notes: list[Note], tempo: list[Tempo], meter: list[Meter], piano: bool,
    *, tuning: list[int] | None = None, tab_positions: list[Position | None] | None = None,
    compact_tab: bool = False,
) -> bytes:
    """Quantize to sixteenths, assigning overlapping durations to independent voices."""
    _validate(notes)
    clock = _Clock(tempo)

    def grid(t: float) -> int:
        return clock.ticks(t, 4) * (_DIVISIONS // 4)

    tempos = {grid(t): bpm for t, bpm in zip(clock.times, clock.bpms)}
    meters = {grid(t): sig for t, sig in _meters(meter).items()}
    grouped: dict[tuple[int, int, int], dict[int, int]] = defaultdict(dict)
    length = max(max(tempos), max(meters)) + 1
    for identity, note in enumerate(notes):
        if tab_positions is not None and tab_positions[identity] is None:
            continue
        start = grid(note["start"])
        end = max(start + _DIVISIONS // 4, grid(note["end"]))
        staff = 2 if piano and note["pitch"] < 60 else 1
        grouped[start, end, staff].setdefault(note["pitch"], identity)
        length = max(length, end)

    root = ET.Element("score-partwise", version="4.0")
    _tag(ET.SubElement(root, "work"), "work-title", title)
    part_list = ET.SubElement(root, "part-list")
    _tag(
        ET.SubElement(part_list, "score-part", id="P1"),
        "part-name",
        "Piano" if piano else title,
    )
    part = ET.SubElement(root, "part", id="P1")
    boundaries = list(meters)
    tempo_times = list(tempos)
    # Each voice has a sorted sequence of non-overlapping chords.
    voices: dict[int, list[list[tuple[int, int, list[tuple[int, int]]]]]] = {1: [[]]}
    if piano:
        voices[2] = [[]]
    for (start, end, staff), pitches in sorted(grouped.items()):
        lanes = voices[staff]
        lane = next((v for v in lanes if not v or v[-1][1] <= start), None)
        if lane is None:
            lane = []
            lanes.append(lane)
        lane.append((start, end, sorted(pitches.items())))
    positions: dict[int, int] = defaultdict(int)
    bass = bool(notes) and median(n["pitch"] for n in notes) < 60
    start = 0
    number = 1
    while start < length:
        signature = bisect_right(boundaries, start) - 1
        n, d = meters[boundaries[signature]]
        bar = n * 4 * _DIVISIONS // d
        end = start + bar
        if signature + 1 < len(boundaries):
            end = min(end, boundaries[signature + 1])
        measure = ET.SubElement(part, "measure", number=str(number))
        if end - start < bar:
            measure.set("implicit", "yes")
        if start in meters:
            attrs = ET.SubElement(measure, "attributes")
            if not start:
                _tag(attrs, "divisions", _DIVISIONS)
            time = ET.SubElement(attrs, "time")
            _tag(time, "beats", n)
            _tag(time, "beat-type", d)
            if not start:
                if piano:
                    _tag(attrs, "staves", 2)
                    _tag(attrs, "part-symbol", "brace")
                for staff in voices:
                    low = staff == 2 or (not piano and bass)
                    clef = ET.SubElement(attrs, "clef", number=str(staff))
                    _tag(clef, "sign", "TAB" if tuning else "F" if low else "G")
                    _tag(clef, "line", (len(tuning) + 1) // 2 if tuning else 4 if low else 2)
                if tuning:
                    details = ET.SubElement(attrs, "staff-details")
                    _tag(details, "staff-type", "tablature")
                    _tag(details, "staff-lines", len(tuning))
                    for string, pitch in enumerate(tuning, 1):
                        string_tuning = ET.SubElement(details, "staff-tuning", line=str(string))
                        _tag(string_tuning, "tuning-step", _STEPS[pitch % 12])
                        if pitch % 12 in (1, 3, 6, 8, 10):
                            _tag(string_tuning, "tuning-alter", 1)
                        _tag(string_tuning, "tuning-octave", pitch // 12 - 1)
        for tick in tempo_times[
            bisect_right(tempo_times, start - 1) : bisect_right(tempo_times, end - 1)
        ]:
            direction = ET.SubElement(measure, "direction", placement="above")
            mark = ET.SubElement(
                ET.SubElement(direction, "direction-type"), "metronome"
            )
            _tag(mark, "beat-unit", "quarter")
            _tag(mark, "per-minute", f"{tempos[tick]:.2f}".rstrip("0").rstrip("."))
            if tick != start:
                offset = ET.SubElement(direction, "offset", sound="yes")
                offset.text = str(tick - start)
            ET.SubElement(direction, "sound", tempo=str(tempos[tick]))
        voice = 0
        for staff, lanes in voices.items():
            for lane_no, lane in enumerate(lanes):
                voice += 1
                index = positions[voice]
                if lane_no and (index == len(lane) or lane[index][0] >= end):
                    continue
                if voice > 1:
                    _tag(ET.SubElement(measure, "backup"), "duration", end - start)
                cursor = start
                while index < len(lane) and lane[index][0] < end:
                    onset, release, chord = lane[index]
                    _span(measure, cursor, onset, 0, 0, [], voice, staff, tab_positions, tuning, notes)
                    cursor = min(end, release)
                    _span(
                        measure,
                        max(start, onset),
                        cursor,
                        onset,
                        release,
                        chord,
                        voice,
                        staff,
                        tab_positions,
                        tuning,
                        notes,
                    )
                    if release > end:
                        break
                    index += 1
                positions[voice] = index
                if compact_tab and cursor == start and end - start == bar:
                    rest = ET.SubElement(measure, "note")
                    ET.SubElement(rest, "rest", measure="yes")
                    _tag(rest, "duration", end - start)
                    _tag(rest, "voice", voice)
                    _tag(rest, "staff", staff)
                else:
                    _span(measure, cursor, end, 0, 0, [], voice, staff, tab_positions, tuning, notes)
        _beam(measure, n, d, compact_tab=compact_tab)
        start = end
        number += 1
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
