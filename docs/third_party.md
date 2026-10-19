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

## Other models (installed by the user, not downloaded by Aethel)
| Model | Where | Licence |
|---|---|---|
| Laya (System 1) | `~/.aethel/models/laya` | see receptron/laya |
| bge-small (embeddings) | ONNX, downloaded by `memory/embed.py` | MIT |
| Kokoro (voice) | `models/tts/` | Apache-2.0 |
| faster-whisper | `models/stt/` | MIT |
| Z-Image Turbo (optional local images) | `models/image/` | Apache-2.0 (Tongyi-MAI) |
