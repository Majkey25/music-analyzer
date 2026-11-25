from pathlib import Path

import librosa
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.signal import find_peaks

from .notation import Note


def transcribe(path: Path, minimum: int, maximum: int) -> list[Note]:
    """Transcribe a single bass line by combining harmonics and attack changes."""
    rate = 5512
    signal, _ = librosa.load(path, sr=rate)
    if not len(signal) or float(np.max(np.abs(signal))) < 1e-6:
        return []
    hop = 55
    window = 661
    fft = 4096
    scale = hop / rate
    pitches = np.arange(max(12, minimum), min(maximum, 96) + 1)
    if not len(pitches):
        return []
    hz = 440 * 2 ** ((pitches - 69) / 12)
    harmonics = np.rint(hz[:, None] * np.arange(1, 11)[None, :] * fft / rate).astype(int)
    safe_bins = np.minimum(harmonics, fft // 2)
    valid = harmonics <= fft // 2
    padded = np.pad(signal, (window // 2, window // 2))
    frames = np.lib.stride_tricks.sliding_window_view(padded, window)[::hop]
    window_shape = np.hanning(window)
    bands: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    levels: list[np.ndarray] = []
    for index in range(0, len(frames), 1000):
        block = frames[index:index + 1000]
        levels.append(np.sqrt(np.mean(block**2, axis=1)))
        power = np.abs(np.fft.rfft(block * window_shape, n=fft)) ** 2
        bands.append(np.stack([
            np.sqrt(power[:, lo:hi].sum(axis=1))
            for lo, hi in [(16, 80), (80, 170), (170, 360), (360, 740), (740, 1470)]
        ], axis=1))
        grid = power[:, safe_bins] * valid
        odd = grid[:, :, ::2].sum(axis=-1)
        even = grid[:, :, 1::2].sum(axis=-1)
        # Subharmonic candidates with only even partials describe a note an
        # octave too low. A lone strong upper partial also isn't enough.
        score = (grid.sum(axis=-1) - .48 * grid.max(axis=-1))
        score *= .18 + .82 * np.minimum(1, 4 * odd / np.maximum(1e-10, even))
        labels.append(pitches[score.argmax(axis=1)])
    pitch_track = median_filter(np.concatenate(labels), size=5)
    energy = np.concatenate(levels)
    peak = float(energy.max())
    envelope = np.concatenate(bands)
    log = np.log1p(envelope / np.maximum(envelope.max(axis=0) * .025, 1e-7))
    flux = np.maximum(np.diff(log, axis=0, prepend=log[:1]), 0).sum(axis=1)
    flux = gaussian_filter1d(flux, 1.1)
    attacks, _ = find_peaks(flux, height=.095, prominence=.038, distance=6)
    changes = np.flatnonzero(np.diff(pitch_track)) + 1
    boundaries = np.unique(np.concatenate(([0], attacks, changes, [len(pitch_track)])))
    result: list[Note] = []
    # The window and flux smoothing delay attacks. Use the front of the
    # window's useful energy rather than the spectral peak for playback.
    correction = .040
    for left, right in zip(boundaries[:-1].tolist(), boundaries[1:].tolist()):
        if right - left < 7:
            continue
        level = float(energy[left:right].mean())
        if level < peak * .06:
            continue
        counts = np.bincount(pitch_track[left:right])
        start = max(0.0, left * scale - correction)
        end = min(len(signal) / rate, right * scale - correction)
        if end > start:
            result.append({
                "start": start, "end": end, "pitch": int(counts.argmax()),
                "velocity": max(1, min(127, round(112 * (level / peak) ** .42))),
            })
    return result
