# Models Folder

Drop your model files here and Aethel picks them up automatically at startup —
no configuration needed. Everything runs 100% locally.

```
models/
├── llm/      Chat model (.gguf) — REQUIRED
├── tts/      Voice output (Kokoro) — optional
├── stt/      Voice input (Whisper) — auto-downloads
└── image/    Selfie generation (Stable Diffusion) — optional
```

## llm/ — the chat model (required)

Place any llama.cpp-compatible `.gguf` chat model here. If several are
present, the largest file is used. Add the matching `mmproj-*.gguf` file
alongside it to enable image understanding (vision).

Recommended starting points (4 GB VRAM class):

| Model | File | Notes |
|---|---|---|
| Gemma 4 E4B Instruct | `gemma-4-E4B-it-Q4_K_M.gguf` | default choice |
| Qwen3 4B Instruct | `Qwen3-4B-Instruct-Q4_K_M.gguf` | strong tool calling |

Download from Hugging Face (search the model name + "GGUF"), or use the
in-app setup flow.

## tts/ — voice output (automatic)

The app speaks using [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M)
(local, CPU, no cloud). The two model files (~340 MB total) **download
automatically in the background on first startup** — no action needed.
Until the download finishes, voice replies fall back to Edge-TTS.

To manage manually instead (`AUTO_DOWNLOAD_SPEECH_MODELS=false` in `.env`),
place these two files here:

- `kokoro-v1.0.onnx`  — https://github.com/thewh1teagle/kokoro-onnx/releases
- `voices-v1.0.bin`   — same releases page

## stt/ — voice input (automatic)

Speech-to-text uses faster-whisper. The model (default: `small`, ~460 MB)
**downloads automatically in the background on first startup** into this
folder. No action needed. Change the size with `STT_MODEL_SIZE` in `.env`
(`tiny` | `base` | `small` | `medium`).

## image/ — selfie generation (optional)

Drop an image model here; the architecture is auto-detected:

| Model | File type | VRAM | Notes |
|---|---|---|---|
| **SD 1.5** (e.g. Realistic Vision V6) | `.safetensors` | ~4 GB | best for 4 GB GPUs |
| **Z-Image Turbo** | `.gguf` or `.safetensors` | 6–8 GB (GGUF) | 8 steps, no CFG |

A `.gguf` file, or a filename containing `z-image`, is treated as Z-Image
Turbo; any other `.safetensors` is treated as SD 1.5. Force it with
`IMAGE_MODEL_ARCH=sd15|zimage` in `.env`.

**VRAM handoff:** on a GPU shared with the LLM (≤8 GB), the app automatically
suspends the LLM to free VRAM, generates the image, then resumes the LLM —
your conversation is preserved across the swap. Disable with
`IMAGE_GEN_VRAM_HANDOFF=false`.

### Z-Image component files

Z-Image is a multi-part model. Drop whichever parts you have into
`models/image/` (flat, no subfolders needed) — they're identified by name:

| Component | Recognized by | Formats |
|---|---|---|
| Transformer | name contains `z-image` | `.gguf` or `.safetensors` |
| VAE | name contains `ae` or `vae` | `.safetensors` |
| Text encoder | name contains `qwen` or `text_encoder` | `.gguf` or `.safetensors` |

**GGUF text encoders are fully supported** — a GGUF Qwen3 text encoder is
dequantized and rebuilt into a working model in-process (no llama.cpp needed),
so you keep the small quantized file instead of the ~8 GB safetensors one.

Any component you don't provide is downloaded once from the base repo
(`Tongyi-MAI/Z-Image-Turbo`) and cached. The Qwen3 tokenizer (~10 MB) always
comes from there unless you drop a `tokenizer/` folder in `models/image/`.

---

**Where do llama.cpp binaries live?** `bin/llama/` in the project root —
the install script downloads them for you. See the main README.
