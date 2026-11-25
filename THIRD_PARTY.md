# Third-party software and models

The Music Analyzer source ZIP contains no dependency binaries or model weights. During installation, uv downloads the packages listed in [uv.lock](uv.lock). The optional separator downloads its checkpoint when first used. Each component retains its upstream license and notices.

## Transcription and separation

* **Basic Pitch 0.4.0** by Spotify AB transcribes pitched notes. Music Analyzer loads Spotify’s ICASSP 2022 model, `nmp.onnx`, from the installed Basic Pitch package using Microsoft ONNX Runtime. Spotify publishes Basic Pitch under Apache License 2.0. [Model](https://github.com/spotify/basic-pitch/blob/v0.4.0/basic_pitch/saved_models/icassp_2022/nmp.onnx) | [License](https://github.com/spotify/basic-pitch/blob/v0.4.0/LICENSE) | [Notices](https://github.com/spotify/basic-pitch/blob/v0.4.0/NOTICE)
* **Demucs code** by Meta Platforms, Inc. and affiliates performs source separation. The application uses Bo-Yu Chen’s `demucs-infer` fork at commit `a8552bb9da29a56018ae2f1828d360cc60830b54`. The fork’s MIT license credits both the original authors and the fork author. [Pinned license](https://github.com/openmirlab/demucs-infer/blob/a8552bb9da29a56018ae2f1828d360cc60830b54/LICENSE)
* **HTDemucs six-source weights** come from Meta. For `htdemucs_6s`, the fork identifies `5c90dfd2-34c22ccb.th` with SHA-256 `34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd` and labels the artifact MIT. Before the v4 release, a Demucs maintainer stated that pretrained weights were provided only for scientific purposes. We could not verify a later grant from Meta allowing general personal use of this checkpoint. Use it only if you have the necessary rights. [Pinned artifact catalog](https://github.com/openmirlab/demucs-infer/blob/a8552bb9da29a56018ae2f1828d360cc60830b54/demucs_infer/config/checkpoints.toml#L295-L304) | [Upstream maintainer statement](https://github.com/facebookresearch/demucs/issues/327#issuecomment-1134828611)

## Notation and runtime

* **Verovio** by the RISM Digital Center and contributors engraves MusicXML scores. The Python package declares LGPL-3.0-only. [Verovio licensing](https://book.verovio.org/introduction/licensing.html) | [Package metadata](https://pypi.org/project/verovio/6.4.1/)
* **librosa** by the librosa development team provides audio analysis, resampling and beat tracking under the ISC license. [librosa license](https://github.com/librosa/librosa/blob/main/LICENSE.md)
* **FFmpeg** decodes imported audio as a separately installed executable. Its license depends on the build configuration. [FFmpeg licensing](https://ffmpeg.org/legal.html)

| Runtime package | Role | Package license |
| --- | --- | --- |
| Flask | Local web application | [BSD-3-Clause](https://pypi.org/project/Flask/3.1.3/) |
| Waitress | Local HTTP server | [ZPL-2.1](https://pypi.org/project/waitress/3.0.2/) |
| Mido | MIDI import and export | [MIT](https://pypi.org/project/mido/1.3.3/) |
| SoundFile | Audio I/O through libsndfile | [BSD-3-Clause](https://pypi.org/project/soundfile/0.14.0/) |
| ONNX Runtime | Basic Pitch model inference | [MIT](https://pypi.org/project/onnxruntime/1.32.0/) |
| Setuptools | Compatibility for Basic Pitch’s dependencies | [MIT](https://pypi.org/project/setuptools/81.0.0/) |

NumPy and SciPy support signal processing. The optional separator uses PyTorch and torchaudio. For transitive dependencies and native libraries included in wheels, consult the license files and notices supplied with each installed package. [Runtime requirements](pyproject.toml) | [Resolved versions and package sources](uv.lock)
