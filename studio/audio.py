import shutil
import subprocess
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks, stft

from .notation import Note

AUDIO_FORMATS = {".wav": "wav", ".mp3": "mp3", ".flac": "flac"}
MAX_DURATION = 900
STEMS = {
    "bass": ("Bass", "bass"), "guitar": ("Guitar", "guitar"),
    "drums": ("Drums", "drums"), "piano": ("Piano", "piano"),
    "vocals": ("Vocals", "melody"), "other": ("Other", "melody"),
}


@dataclass
class Wave:
    duration: float
    peaks: list[float]


def normalize(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg is missing. Install it and add it to PATH.")
    args = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file,pipe", "-f", AUDIO_FORMATS[source.suffix],
        "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
        "-t", str(MAX_DURATION + 1), "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_s16le", str(target),
    ]
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError("Could not read the file. Try a valid WAV, MP3, or FLAC.") from None
    duration = sf.info(target).duration
    if not 0 < duration <= MAX_DURATION:
        raise ValueError("Choose audio that is 15 minutes or shorter.")


def waveform(path: Path) -> Wave:
    with sf.SoundFile(path) as source:
        duration = source.frames / source.samplerate
        blocks = source.blocks(blocksize=max(1, (source.frames + 1199) // 1200),
                               dtype="float32", always_2d=True)
        return Wave(duration, [round(float(np.max(np.abs(block))), 4) for block in blocks])


def mix_tracks(paths: list[Path], destination: Path) -> Wave:
    """Build a synchronized practice source from normalized stereo tracks."""
    with ExitStack() as files:
        sources = [files.enter_context(sf.SoundFile(path)) for path in paths]
        length = max(source.frames for source in sources)

        def chunks():
            for sample in range(0, length, 65536):
                block = np.zeros((min(65536, length - sample), 2), dtype=np.float32)
                for source in sources:
                    value = source.read(len(block), dtype="float32", always_2d=True)
                    block[:len(value)] += value
                yield block

        peak = max(1., max(float(np.max(np.abs(block))) for block in chunks()))
        for source in sources:
            source.seek(0)
        with sf.SoundFile(destination, mode="w", samplerate=44100, channels=2,
                          subtype="PCM_16") as output:
            for block in chunks():
                output.write(block / peak)
    return waveform(destination)


def demo_audio(path: Path, notes: list[Note], mode: str) -> Wave:
    rate = 44100
    output = np.zeros(12 * rate, dtype=np.float32)
    rng = np.random.default_rng(25)
    for note in notes:
        start, end = round(note["start"] * rate), round(note["end"] * rate)
        t = np.arange(end - start) / rate
        pitch = note["pitch"]
        if mode == "drums":
            if pitch == 36:
                signal = np.sin(2 * np.pi * (52 * t + 1.45 * (1 - np.exp(-45 * t))))
            elif pitch == 42:
                noise = rng.normal(size=len(t) + 1)
                signal = np.diff(noise) * .38
            else:
                signal = rng.normal(size=len(t)) * .48 + np.sin(2 * np.pi * 180 * t) * .28
            signal *= np.exp(-42 * t)
        else:
            radians = 2 * np.pi * (440 * 2 ** ((pitch - 69) / 12)) * t
            harmonic_level = .45 if mode == "bass" else .24
            signal = np.sin(radians) + harmonic_level * np.sin(2 * radians) + .18 * np.sin(3 * radians)
            signal *= np.exp((-2 if mode == "bass" else -4.2) * t)
        envelope = np.minimum(1, t * 180) * np.minimum(1, (len(t) / rate - t) * 75)
        output[start:end] += signal * envelope * (note["velocity"] / 127) * .16
    sf.write(path, np.column_stack((output, output)), rate, subtype="PCM_16")
    return waveform(path)


def rhythm(path: Path) -> tuple[float | None, float]:
    import librosa

    audio, rate = librosa.load(path, sr=22050, duration=180)
    if len(audio) < rate * 3:
        return None, 0.0
    tempo, beats = librosa.beat.beat_track(y=audio, sr=rate, trim=False)
    bpm = float(np.asarray(tempo).reshape(-1)[0])
    if len(beats) < 4 or not 20 <= bpm <= 350:
        return None, 0.0
    first_beat = float(librosa.frames_to_time(int(beats[0]), sr=rate))
    return round(bpm, 2), round(first_beat % (60 / bpm), 3)


@lru_cache(maxsize=1)
def separator():
    try:
        from demucs_infer import DemucsSeparator
    except ModuleNotFoundError as error:
        if error.name == "demucs_infer":
            raise ValueError("Run uv sync --extra separation to install track separation.") from None
        raise
    return DemucsSeparator(model="htdemucs_6s", device="cpu", shifts=0)


def separate(path: Path, directory: Path, progress: Callable[[str], None]) -> dict[str, Path]:
    progress("Loading the six-track model. The first run downloads the model weights.")
    engine = separator()
    mix, rate = sf.read(path, dtype="float32", always_2d=True)
    import torch

    progress("Separating tracks. Longer songs can take several minutes.")
    _, result = engine(torch.from_numpy(mix.T.copy()), sample_rate=rate)
    if set(result) != set(STEMS):
        raise RuntimeError("The model did not return all six tracks.")
    progress("Saving separated tracks.")
    paths = {}
    for key, samples in result.items():
        destination = directory / f"{key}.wav"
        sf.write(destination, samples.detach().cpu().numpy().T, rate, subtype="PCM_16")
        paths[key] = destination
    return paths


@lru_cache(maxsize=1)
def pitch_model():
    from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
    from basic_pitch.inference import Model

    return Model(build_icassp_2022_model_path(FilenameSuffix.onnx))


def melody(path: Path, mode: str, minimum: int, maximum: int) -> list[Note]:
    from basic_pitch.inference import predict

    model_min = max(21, minimum)
    model_max = min(108, maximum)
    if model_min > model_max:
        return []
    _, _, events = predict(
        path, pitch_model(),
        minimum_frequency=440 * 2 ** ((model_min - 69) / 12),
        maximum_frequency=440 * 2 ** ((model_max + 1 - 69) / 12),
        minimum_note_length=55,
        melodia_trick=mode in {"melody", "violin"},
    )
    return [
        {"start": float(start), "end": float(end),
         "pitch": int(pitch), "velocity": max(1, min(127, round(float(amplitude) * 127)))}
        for start, end, pitch, amplitude, _ in events
        if minimum <= pitch <= maximum and end > start
    ]


def drums(path: Path) -> list[Note]:
    """Detect common kit hits with band-specific spectral flux and attack shape."""
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1, dtype=np.float32)
    frequencies, times, spec = stft(mono, fs=rate, nperseg=1024, noverlap=768)
    power = np.abs(spec) ** 2
    bands = np.vstack([
        np.sum(power[(frequencies >= lo) & (frequencies < hi)], axis=0)
        for lo, hi in [(30, 120), (120, 360), (360, 2200), (2200, 5000), (5000, 14000)]
    ])
    bands = np.sqrt(bands)
    floor = max(float(bands.max()) * 0.006, 1e-5)
    log = np.log1p(bands / floor)
    flux = np.maximum(np.diff(log, prepend=log[:, :1]), 0)
    weighted = (flux * np.asarray([2, 1, 1, 0.8, 1])[:, None]).sum(axis=0)
    threshold = max(0.20, float(np.percentile(weighted, 78)) * 1.6)
    peaks, _ = find_peaks(weighted, height=threshold, distance=max(1, round(0.052 * rate / 256)),
                          prominence=0.1)
    notes: list[Note] = []
    for event in peaks:
        low, body, mid, snap, air = bands[:, event:event + 7].max(axis=1)
        maximum = float(max(low, body, mid, snap, air))
        if maximum < floor * 1.4:
            continue
        pitches = []
        if low > 1.2 * body and low > 0.55 * maximum:
            pitches.append(36)
        if snap + mid > 1.25 * (air + low) and body + mid > 0.50 * maximum:
            pitches.append(38 if snap > 0.23 * body else 45)
        if air > 0.62 * maximum and air > 0.82 * (mid + body):
            pitches.append(42)
        if not pitches:
            pitches.append(45 if body > snap else 38)
        start = max(0.0, float(times[event]) - 0.006)
        notes.extend({"start": start, "end": start + 0.075, "pitch": pitch,
                      "velocity": min(127, max(20, round(110 * (maximum / float(bands.max()))**0.4)))}
                     for pitch in pitches)
    return notes
