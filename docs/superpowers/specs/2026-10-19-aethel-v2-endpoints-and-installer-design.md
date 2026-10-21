# Aethel v2: Endpoints Anywhere, and an Installable App

- **Date:** 2026-10-19
- **State:** Phases 0–5 merged (#2–#19).
- **Ships as two PRs:** **E** (endpoints) from `v2-endpoints`, then **I** (installer) from `v2-installer`. Phase 6 follows.

## User decisions (2026-10-19)
- **Installer: "anyone, self-contained."**
  - The installer carries everything needed to set up the backend.
  - The first launch installs a private Python and the backend's packages into `~/.aethel/runtime`. That needs internet once.
  - Model files are pointed at or downloaded inside the app.
- **LLM:** several named custom endpoints (any OpenAI-compatible server), plus the local `.gguf` *and* `llama-server` taken from any folder.
- **Images:** an OpenAI-compatible images endpoint, a ComfyUI server with the user's own workflow, and the local Z-Image weights from any folder.

## E. Endpoints anywhere

### E1. Named LLM endpoints
- **Settings.** `AppSettings.endpoints: list[Endpoint]`, where `Endpoint = {id, name, base_url}`.
  - `id` is a slug matching `^[a-z][a-z0-9-]{1,30}$` and must not be a built-in provider id.
  - `base_url` must be http(s).
- **Migration.** The old single `custom_base_url` becomes the endpoint `{id: "custom", name: "Custom", base_url}`. Role entries that say `provider: "custom"` keep working unchanged.
- **Roles.** `RouteEntry.provider` becomes a string: a built-in (`groq`, `gemini`, `openrouter`, `local`) or an endpoint id. Saving settings rejects an entry naming an endpoint that doesn't exist.
- **Keys.** Each endpoint has an optional key, stored as `endpoint:<id>`: in the OS credential store (Tauri `secret_*` accepts that pattern) and in the backend's KeyStore. No key means `no-key`, as for `custom` today.
- **Catalogue.** `/api/providers` lists the built-ins plus every endpoint, with label = name. `models` and `test` work for endpoints. Settings → Providers gets "Add endpoint" (name, URL, optional key, Test, List models, Remove). Removing an endpoint that a role still uses is refused, and the response names the role.
- **Private mode** is unchanged: only the `local` provider. An endpoint is never trusted to be local, even one on 127.0.0.1 (spec rule: nothing leaves through a door Aethel doesn't run).

### E2. Local model and server from anywhere
- `LocalLLMSettings.server_path`: the `llama-server` executable (or its folder). Empty means the current search: `LLAMA_SERVER_BINARY`, PATH, `bin/`.
- `model_path` already exists.
- Settings → Local model gets **Browse…** for both, through `tauri-plugin-dialog`. In browser dev it falls back to a text field.
- The section shows what it found: "Using `<model>` with `<server>`", or why it found nothing.

### E3. Image endpoints
- **`OpenAIImages`** (`images/openai_images.py`):
  - Settings: `images.openai = {base_url, model, size}`. The key is stored as `endpoint:images-openai`.
  - With references it sends `POST {base}/images/edits`, multipart, with `image[]` = the references and `prompt`. Without references it sends `POST {base}/images/generations` as JSON.
  - It reads `data[0].b64_json`, or downloads `data[0].url`. `max_references` is 6.
  - `cloud = True`, unless the URL's host is a loopback address. Then it counts as on this PC and may be used in private mode: its traffic never leaves the machine.
- **`ComfyUI`** (`images/comfyui.py`):
  - Settings: `images.comfyui = {base_url, workflow_path}`. The workflow is the user's "Save (API format)" JSON.
  - Placeholders in string inputs: `{{prompt}}`, `{{negative}}`, `{{seed}}` (int), `{{width}}`, `{{height}}`, and `{{reference}}` (a `LoadImage` filename).
  - Flow:
    1. Upload the first reference to `/upload/image` (when the workflow has `{{reference}}`).
    2. `POST /prompt {prompt: <filled workflow>, client_id}`.
    3. Poll `/history/<prompt_id>` (every second, up to 300 s).
    4. Take the first output image and fetch it from `/view?filename&subfolder&type`.
  - `max_references` is 1 when the workflow has `{{reference}}`, else 0. Loopback counts as local, as above.
  - Settings → Images: URL, workflow file (Browse…), and "Check", which validates the JSON and lists the placeholders it found.
- **Local Z-Image folder:** `images.local_dir`, the folder holding the Z-Image weights. Empty means `models/image`.
- **Backend order:** `images.backends` gains `openai` and `comfyui`. The default is `["gemini", "openai", "comfyui", "openrouter", "local"]`. A backend that isn't set up simply isn't available.

## I. The installer (Windows first)

### I1. What ships
- A Tauri NSIS installer (`Aethel_<version>_x64-setup.exe`). It is per-user, so it needs no admin rights. Tauri also builds an MSI.
- Bundled resources:
  - `backend/aethel/` (the source);
  - `requirements/core.txt` and `requirements/local-images.txt` (fully pinned);
  - `uv.exe` (Astral uv, a single pinned binary, MIT/Apache-2.0);
  - `overlay.html` and the icons.
- **Not bundled:** model files (LLM, voices, speech, image, Laya, face models), Node.js (used by the background browser and Desktop Commander), llama-server.

### I2. First launch (the boot screen shows each step and the log)
1. **Runtime.** If `~/.aethel/runtime/.venv/Scripts/python.exe` is missing, or `runtime/requirements.sha256` ≠ the bundled lock's hash:
   - `uv python install 3.11` (python-build-standalone);
   - `uv venv ~/.aethel/runtime/.venv --python 3.11`;
   - `uv pip install -r core.txt`.
   About 400 MB, and it says so. The hash is then written. A failure shows the log and a **Retry**.
2. **Backend.** The backend runs with `AETHEL_BACKEND_DIR = <resources>/backend` and `MODELS_DIR = ~/.aethel/models` (the user can repoint it).
3. **Onboarding** (Phase 6b does the full flow; here, the minimum). If there is no LLM at all (no key, no endpoint, no local model), the app opens Settings → Providers with a note.
- Later updates replace the app's files; the runtime is reinstalled only if the lock changed.
- `uvx` (Windows-MCP) comes from the bundled uv: the backend gets `UV` / `PATH` pointing at it.

### I3. In-app downloads (each pinned to a SHA-256, recorded in docs/third_party.md)
- **Voices (Kokoro):** `kokoro-v1.0.onnx` and `voices-v1.0.bin`, about 340 MB, from Settings → Voice.
- **llama-server:** a pinned llama.cpp Windows release (the Vulkan build: one binary for NVIDIA, AMD and Intel), from Settings → Local model. Alternatively, Browse to your own.
- **Local images extra:** `uv pip install -r local-images.txt` (torch, diffusers…, about 3 GB), from Settings → Images. It only appears when the user wants local Z-Image.
- **Speech to text:** faster-whisper already downloads its model on first use. Laya and the face models already have their own downloads.

### I4. Dev mode is unchanged
`pnpm tauri dev`, and the dev backend (repo checkout, `.venv` or the system Python), behave exactly as today. Only a release build (`cfg!(not(debug_assertions))`) uses the resources and the runtime.

### I5. Not in scope
- Code signing: the installer is unsigned, so SmartScreen will warn. Signing needs a certificate.
- macOS and Linux bundles.
- Auto-update.
- Bundling Node.js.
- A LICENSE file. The repo is public with none, which is the owner's decision. Phase 6b reminds.

## Tests
- **E:**
  - endpoint migration;
  - validation (slug, built-in clash, a role naming a missing endpoint);
  - routing through an endpoint;
  - keys by `endpoint:<id>`;
  - removal refused while in use;
  - the `server_path` override;
  - the OpenAI images edit/generation bodies, parsing b64 and url;
  - ComfyUI placeholder filling, the upload → prompt → history → view flow (MockTransport), and workflow checks;
  - loopback counting as local;
  - private mode.
  - **Vitest:** add/remove an endpoint, roles offer endpoints, the Browse fallback, the image settings forms.
- **I:**
  - the runtime-setup state machine in Rust, unit-tested with a fake command runner (missing → install → hash written; hash mismatch → reinstall; failure → error with log);
  - `cargo test`;
  - the locks' pins checked by a pytest that every core import resolves from `core.txt`;
  - a real `pnpm tauri build` producing the installer, then installing it on this PC into a throwaway `AETHEL_HOME` and starting it. That last step is a manual check by me, on this PC.
