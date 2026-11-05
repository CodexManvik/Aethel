# Models folder

You don't need anything here to use Aethel: cloud models work with just an API key, and **Settings** can point at model files anywhere on your PC. This folder is only the default place Aethel looks. In the installed app that place is `~/.aethel/models`.

| Folder | What | How it gets here |
|---|---|---|
| `llm/` | A chat model (`.gguf`) | Add one yourself, or choose a file in **Settings → Local model** |
| `tts/` | Kokoro voices | **Settings → Voice** downloads them |
| `stt/` | faster-whisper | Downloads the first time it's used |
| `image/` | Z-Image Turbo | Add one yourself, or choose a folder in **Settings → Images** |

## llm/

Any llama.cpp-compatible `.gguf` works. If there are several, the largest is used. Put a matching `mmproj-*.gguf` next to it for vision.

`llama-server` isn't kept here: **Settings → Local model** can download it, or choose your own.

## image/

Z-Image is made of parts. Put whichever you have in the folder with no subfolders, and they're recognised by name:

| Part | Recognised by | Formats |
|---|---|---|
| Transformer | name contains `z-image` | `.gguf` or `.safetensors` |
| VAE | name contains `ae` or `vae` | `.safetensors` |
| Text encoder | name contains `qwen` or `text_encoder` | `.gguf` or `.safetensors` |

Anything missing is downloaded once from `Tongyi-MAI/Z-Image-Turbo`. Local images also need their own packages, which the app installs from **Settings → Images**.
