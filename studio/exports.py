from io import BytesIO
from xml.etree import ElementTree as ET

import mido

from .notation import Meter, Note, Tempo, midi, musicxml

DRUMS = {
    35: ("Akustický kopák", "F", "4", False), 36: ("Kopák", "F", "4", False),
    37: ("Ráfek", "C", "5", True), 38: ("Virbl", "C", "5", False),
    40: ("Elektrický virbl", "C", "5", False), 41: ("Floor tom", "A", "4", False),
    42: ("Zavřená hi-hat", "G", "5", True), 43: ("Floor tom", "A", "4", False),
    44: ("Pedál hi-hat", "D", "4", True), 45: ("Nízký tom", "B", "4", False),
    46: ("Otevřená hi-hat", "G", "5", True), 47: ("Tom", "D", "5", False),
    48: ("Tom", "E", "5", False), 49: ("Crash", "A", "5", True),
    50: ("Vysoký tom", "E", "5", False), 51: ("Ride", "F", "5", True),
}
PROGRAMS = {"bass": 33, "guitar": 27, "piano": 0, "violin": 40}
PART_NAMES = {"bass": "Basa", "guitar": "Kytara", "violin": "Housle", "drums": "Bicí"}


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
        _, step, octave, cross = DRUMS.get(number, ("Perkuse", "C", "5", False))
        index = list(note).index(pitch)
        note.remove(pitch)
        unpitched = ET.Element("unpitched")
        ET.SubElement(unpitched, "display-step").text = step
        ET.SubElement(unpitched, "display-octave").text = octave
        note.insert(index, unpitched)
        if cross:
            following = {"staff", "beam", "notations", "lyric"}
            index = next((i for i, item in enumerate(note) if item.tag in following), len(note))
            head = ET.Element("notehead")
            head.text = "x"
            note.insert(index, head)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
