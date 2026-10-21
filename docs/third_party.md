# Third-party models

These are model files Aethel downloads on first use, each pinned to a SHA-256 hash (D5, Phase 5b). A file whose hash doesn't match is refused. Nothing below ships inside the app.

| Model | Used for | Source (pinned commit) | Size | SHA-256 | Licence |
|---|---|---|---|---|---|
| YuNet `face_detection_yunet_2023mar.onnx` | Finding faces and their 5 landmarks in generated photos | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet) `f12e127` | 232,589 B | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | MIT ("All files in this directory are licensed under MIT License") |
| ArcFace ResNet-100 `arcfaceresnet100-8.onnx` | A face embedding for the identity check (does this photo show the same person as the anchor portrait?) | [onnx/models](https://github.com/onnx/models/tree/4c46cd00fbdb7cd30b6c1c17ab54f2e1f4f7b177/validated/vision/body_analysis/arcface) `4c46cd0` | 261,036,388 B | `f3a6bc281e72f88862f5748b53be3d76b3b48f8f1ab1f4a537941bdc4e1b01da` | Apache-2.0 (the model card's licence section and the repository's) |

## Notes on ArcFace (chosen by the user on 2026-10-19)
- The model card says the weights come from the ArcFace paper (InsightFace, MXNet). They were trained on Refined MS-Celeb-1M, which Microsoft withdrew in 2019 over consent concerns.
- The ONNX Model Zoo republishes the file under Apache-2.0. But it derives from InsightFace's MXNet ResNet-100 weights, and InsightFace says its pretrained models are for non-commercial research.
- A personal, non-commercial project is fine. Before Aethel is distributed commercially, get a legal check, or switch to SFace.
- OpenCV Zoo's SFace (`face_recognition_sface_2021dec.onnx`, Apache-2.0, about 37 MB) was the alternative considered.

## The installed app (spec 2026-10-19 §I)
| What | Used for | Source | Size | SHA-256 | Licence |
|---|---|---|---|---|---|
| uv 0.12.20 `uv-x86_64-pc-windows-msvc.zip` | Shipped in the installer: it sets up Aethel's own Python 3.11 and packages on first run | [astral-sh/uv](https://github.com/astral-sh/uv/releases/tag/0.12.20) | 18,039,150 B | `95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06` | MIT or Apache-2.0 |
| Python 3.11 (python-build-standalone) | Fetched by uv on first run | astral-sh/python-build-standalone | about 30 MB | uv checks it | PSF |
| The backend's packages (`backend/requirements/core.txt`, 104 pinned with hashes) | Fetched by uv on first run (`--require-hashes`) | PyPI | about 550 MB installed | in the lock | each package's own (MIT, BSD and Apache-2.0 mostly) |
| llama.cpp b11381, `llama-b11381-bin-win-vulkan-x64.zip` | Settings → Local model → Get llama-server (GPU) | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/releases/tag/b11381) | 33,287,351 B | `517032b9ab2e1c7991a1313fa8c39cbc1a9da6ebf82944cf34ccf6adf3d0a37a` | MIT |
| llama.cpp b11381, `llama-b11381-bin-win-cpu-x64.zip` | the same (CPU only) | the same | 19,367,262 B | `f5cb0430a9dd2174f392fdad17ed5d6417e5542c47efc993fa4917287a738a76` | MIT |
| Kokoro `kokoro-v1.0.onnx` | Settings → Voice → Download the voices | [thewh1teagle/kokoro-onnx model-files-v1.0](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0) | 325,532,387 B | `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` | Apache-2.0 |
| Kokoro `voices-v1.0.bin` | the same | the same | 28,214,398 B | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` | Apache-2.0 |

The Kokoro release publishes no digests. These hashes are of copies whose sizes match the release byte for byte; a file that doesn't match is refused, never accepted.

## Other models (installed by the user, not downloaded by Aethel)
| Model | Where | Licence |
|---|---|---|
| Laya (System 1) | `~/.aethel/models/laya` | see receptron/laya |
| bge-small (embeddings) | ONNX, downloaded by `memory/embed.py` | MIT |
| Kokoro (voice) | `models/tts/` | Apache-2.0 |
| faster-whisper | `models/stt/` | MIT |
| Z-Image Turbo (optional local images) | `models/image/` | Apache-2.0 (Tongyi-MAI) |
