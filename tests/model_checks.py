"""Run actual model inference on repeatable, generated audio fixtures."""

import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from studio.audio import melody
from studio.bass import transcribe

RATE = 44100


def sound(path: Path, groups: list[list[int]], decay: float) -> None:
    duration = .55
    timeline = np.arange(round(RATE * duration)) / RATE
    shape = np.minimum(1, timeline / .012) * np.exp(-timeline * decay)
    shape *= np.minimum(1, (duration - timeline) / .04)
    clips = [np.zeros(round(RATE * .2))]
    for chord in groups:
        clip = np.zeros(len(timeline))
        for pitch in chord:
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            for partial, volume in enumerate((1, .38, .23, .16, .1), 1):
                clip += volume * np.sin(math.tau * frequency * partial * timeline)
        clips.extend([clip * shape / max(2, len(chord) * 2), np.zeros(round(RATE * .22))])
    sf.write(path, np.concatenate(clips), RATE)


def run() -> None:
    with TemporaryDirectory() as folder:
        path = Path(folder) / "fixture.wav"
        sound(path, [[17], [22], [29]], decay=1.5)
        notes = transcribe(path, 17, 58)
        assert {17, 22, 29} <= {item["pitch"] for item in notes}, notes
        sound(path, [[55], [62], [69]], decay=1)
        notes = melody(path, "violin", 55, 100)
        assert {55, 62, 69} <= {item["pitch"] for item in notes}, notes
        sound(path, [[60, 64], [62, 65]], decay=3)
        notes = melody(path, "piano", 21, 108)
        for moment, expected in [(.3, {60, 64}), (1.1, {62, 65})]:
            actual = {item["pitch"] for item in notes if item["start"] <= moment <= item["end"]}
            assert expected <= actual, (moment, actual)
    print("Model checks passed: low bass, violin range and polyphonic piano")


if __name__ == "__main__":
    run()
