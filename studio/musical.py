from bisect import bisect_right
from collections import defaultdict, deque
from itertools import pairwise
from pathlib import Path
from typing import TypedDict

import mido
import numpy as np
from scipy.optimize import linear_sum_assignment

from .notation import Meter, Note, Position, Tempo


class MidiTrack(TypedDict):
    title: str
    mode: str
    notes: list[Note]


def read_midi(path: Path) -> tuple[list[MidiTrack], list[Tempo], list[Meter]]:
    midi_file = mido.MidiFile(path)
    if midi_file.type == 2 or midi_file.ticks_per_beat <= 0:
        raise ValueError("Choose a type 0 or 1 MIDI file with PPQ timing.")
    tempo_changes = {0: 500000}
    meter_changes = {0: (4, 4)}
    for track in midi_file.tracks:
        tick = 0
        for event in track:
            tick += event.time
            if event.type == "set_tempo" and event.tempo > 0:
                tempo_changes[tick] = event.tempo
            if event.type == "time_signature":
                meter_changes[tick] = (event.numerator, event.denominator)

    starts = sorted(tempo_changes)
    elapsed = [0.0]
    for left, right in pairwise(starts):
        elapsed.append(elapsed[-1] + mido.tick2second(
            right - left, midi_file.ticks_per_beat, tempo_changes[left],
        ))

    def seconds(tick: int) -> float:
        index = bisect_right(starts, tick) - 1
        return elapsed[index] + mido.tick2second(
            tick - starts[index], midi_file.ticks_per_beat, tempo_changes[starts[index]],
        )

    tracks: list[MidiTrack] = []
    for index, track in enumerate(midi_file.tracks, 1):
        groups: dict[tuple[int, int], list[Note]] = defaultdict(list)
        running: dict[tuple[int, int], deque[tuple[int, int, int]]] = defaultdict(deque)
        sustained: dict[int, bool] = defaultdict(bool)
        held: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
        programs: dict[int, int] = defaultdict(int)

        def finish(channel: int, pitch: int, onset: int, velocity: int, program: int, tick: int,
                   output: dict[tuple[int, int], list[Note]] = groups) -> None:
            if tick > onset:
                output[channel, program].append({
                    "start": seconds(onset), "end": seconds(tick),
                    "pitch": pitch, "velocity": velocity,
                })

        tick = 0
        for event in track:
            tick += event.time
            if event.type == "program_change":
                if event.channel != 9:
                    programs[event.channel] = event.program
            elif event.type == "control_change" and event.control == 64 and event.channel != 9:
                sustained[event.channel] = event.value >= 64
                if not sustained[event.channel]:
                    for pitch, onset, velocity, program in held.pop(event.channel, []):
                        finish(event.channel, pitch, onset, velocity, program, tick)
            elif event.type == "note_on" and event.velocity:
                running[event.channel, event.note].append((tick, event.velocity, programs[event.channel]))
            elif event.type in {"note_off", "note_on"}:
                pending = running[event.channel, event.note]
                if not pending:
                    continue
                onset, velocity, program = pending.popleft()
                if sustained[event.channel]:
                    held[event.channel].append((event.note, onset, velocity, program))
                else:
                    finish(event.channel, event.note, onset, velocity, program, tick)
        for channel, pending_held in held.items():
            for pitch, onset, velocity, program in pending_held:
                finish(channel, pitch, onset, velocity, program, tick)
        for (channel, pitch), pending in running.items():
            for onset, velocity, program in pending:
                finish(channel, pitch, onset, velocity, program, tick)
        for (channel, program), notes in sorted(groups.items()):
            mode = (
                "drums" if channel == 9 else "bass" if 32 <= program <= 39
                else "guitar" if 24 <= program <= 31 else "piano" if program <= 7
                else "violin" if program == 40 else "melody"
            )
            title = track.name or f"Track {index}"
            if len(groups) > 1:
                title += f" / channel {channel + 1}, program {program + 1}"
            tracks.append({
                "title": title, "mode": mode,
                "notes": sorted(notes, key=lambda note: (note["start"], note["pitch"])),
            })
    if not tracks:
        raise ValueError("The MIDI file contains no notes with a positive duration.")
    tempo: list[Tempo] = [{"time": seconds(tick), "bpm": mido.tempo2bpm(tempo_changes[tick])}
                          for tick in starts]
    meter: list[Meter] = [
        {"time": seconds(tick), "numerator": value[0], "denominator": value[1]}
        for tick, value in sorted(meter_changes.items())
    ]
    return tracks, tempo, meter


def fingering(
    notes: list[Note], tuning: list[int], frets: int, bass: bool,
    preferred: list[int | None] | None = None,
) -> list[Position | None]:
    """Assign each onset to available strings while holding earlier notes."""
    result: list[Position | None] = [None] * len(notes)
    held_until = [0.0] * len(tuning)
    hand = 3.0
    cursor = 0
    while cursor < len(notes):
        start = notes[cursor]["start"]
        stop = cursor + 1
        while stop < len(notes) and notes[stop]["start"] - start < 0.025:
            stop += 1
        group = notes[cursor:stop]
        available = [i for i, until in enumerate(held_until) if until <= start + 0.025]
        if not available:
            cursor = stop
            continue
        cost = np.full((len(available), len(group) + len(available)), 1000.0)
        cost[:, len(group):] = 200.0
        for col, note in enumerate(group):
            requested = preferred[cursor + col] if preferred is not None else None
            for row, string in enumerate(available):
                fret = note["pitch"] - tuning[string]
                if 0 <= fret <= frets and (requested is None or string == requested):
                    stretch = abs(fret - hand) * 0.55 if fret else 0.5
                    priority = -100 if requested is not None else 0
                    cost[row, col] = priority + fret * 0.7 + stretch + string * (2.4 if bass else 0.15)
        rows, columns = linear_sum_assignment(cost)
        placed = []
        for row, col in zip(rows.tolist(), columns.tolist()):
            if col >= len(group):
                continue
            string = available[row]
            fret = group[col]["pitch"] - tuning[string]
            result[cursor + col] = {"string": string, "fret": fret}
            held_until[string] = group[col]["end"]
            if fret:
                placed.append(fret)
        if placed:
            hand = sum(placed) / len(placed)
        cursor = stop
    return result
