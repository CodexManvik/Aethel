# Changelog

## v2.0.0-beta.1 (2026-10-21), pre-release

The first release of Aethel v2, a rebuild of the original prototype. It's a beta: the evaluation work and first-run onboarding (Phase 6) are still to come.

### The app
- **A Windows installer** (`Aethel_2.0.0-beta.1_x64-setup.exe`). It installs to Program Files for every user and needs admin rights once.
  - The first launch sets up Aethel's own Python 3.11 and its packages in your `~/.aethel`. That takes about 3 minutes and 580 MB, needs the internet, and happens once.
  - Every package is checked against a pinned hash.
  - Later launches take a few seconds.
- **Your data lives in `C:\Users\<you>\.aethel`:** settings, conversations, memory, personas, the runtime and models. Uninstalling leaves it, so delete that folder to remove everything.

### What it does
- **Chat** with a warm built-in companion, or **personas** you create:
  - the model drafts a persona from a description, and you pick its face from four portraits;
  - a character sheet, a voice blend, its own accent colour, and a style learned from a chat export;
  - a tone engine;
  - photos it sends that keep its face, checked by an optional local face check.
- **Calls:** hands-free voice with barge-in. Azure Speech, or this PC's Whisper, hears you; Kokoro voices (on this PC) or Azure speak.
- **Tasks** on your computer:
  - plans, file and shell tools, desktop control of any app through accessibility, Office automation, and a background browser;
  - approvals for anything outside your allowed folders, and undo for file changes;
  - a replay timeline.
- **It learns:** finished tasks become reviewable Markdown skills, and repeated ones compile into macros that replay with a small local judge (Laya) and call the LLM only when something has changed.
- **Memory:** facts it remembers, earlier conversations, and per-persona facts, all visible and editable.
- **Web:** search and reading with citations, switchable per conversation.
- **Models anywhere:**
  - Groq, Gemini, OpenRouter, and any number of your own OpenAI-compatible endpoints (LM Studio, Ollama, vLLM, another PC);
  - a local `.gguf` with `llama-server` from any folder, or downloaded in the app;
  - images from Gemini, OpenRouter, any OpenAI-compatible images server, ComfyUI with your own workflow, or local Z-Image Turbo.
- **Private mode** keeps everything on this computer.

### Known limits
- **SmartScreen warning:** the installer isn't code-signed, so Windows shows one. Choose "More info", then "Run anyway".
- **Node.js** is needed for the background browser and Desktop Commander. Without it they say so, and the rest works.
- **Not measured yet:** several thresholds (end-of-turn, identity, style recall) are marked "not measured" in Settings. Phase 6's evaluation calibrates them.
- **Windows only.**
