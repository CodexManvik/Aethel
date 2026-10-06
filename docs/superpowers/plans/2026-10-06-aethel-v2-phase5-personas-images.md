# Aethel v2 · Phase 5 (Personas and Images): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution:** inline. Two PRs: **5a (personas)** on `v2-phase5a`, then **5b (images)** on `v2-phase5b`.

**Goal:**
- You can create a persona end to end: describe it, edit what the LLM drafts, pick a face, generate a character sheet, choose a voice, and talk to it.
- Ask it for a picture at the beach and you get its face, consistent and identity-checked, as a photo that develops in the chat.

**Architecture:**
- **Personas** are folders under `~/.aethel/personas/<id>/`. `persona.md` (YAML frontmatter + sections) is the source of truth. A `PersonaStore` reads and writes them, and chat, voice and images ask it for the active persona's prompt, voice blend, accent and references.
- **Images:**
  - An `ImageBackend` interface with three implementations: Gemini image, OpenRouter image models, and local Z-Image with VRAM handoff.
  - Code composes the prompt (anchor + sheet references + style + scene); the LLM only writes the scene.
  - An identity check (YuNet face detection + ArcFace embedding, both ONNX on CPU) gates faces against the anchor.
  - Generation runs in the background and streams `image_pending` → `image_ready` events to the developing-photo card.

**Tech Stack:**
- PyYAML, numpy, Pillow, OpenCV (`cv2.FaceDetectorYN`, already installed 4.12), onnxruntime (installed).
- Gemini REST through httpx (no new SDK).
- The existing Z-Image code (diffusers 0.38) behind the optional local backend.
- React: Radix dialog and slider (new: `@radix-ui/react-slider`, pinned).

**Spec:** parent spec §9 (personas), §10 (images), §12.3 item 3 (persona studio), §12.4 (motion: shared avatar, developing photo), §14 E4. Roadmap decisions D4, D5 and D6.

## Global Constraints
- Every earlier constraint still applies (roadmap §6).
- **Private mode:** cloud image backends are disabled. Only local Z-Image is used, and only if its weights are installed. Otherwise the image tool isn't offered, and the studio explains why.
- **Code composes image prompts.** The LLM writes only `scene` (and optional `framing` and `expression`, from fixed enums).
- **`image_frequency`** (`never | on_request | sometimes`) is enforced in code: `sometimes` allows at most one spontaneous image every 15 turns, and `on_request` requires System 1 to judge the user's message as a request for a picture.
- **The identity threshold `τ_id`** is provisional at 0.45 (spec) until E4 calibrates it. When a check fails, regenerate once and keep the better of the two. The score is stored with the image.
- **There are at most 6 references** per persona (the anchor + sheet + passing images).
- **Model files are downloaded with a pinned SHA-256,** and their license is recorded in `docs/third_party.md` before first use (D5).
- **The built-in Aethel persona** has no portrait requirement; it shows its monogram. The tone engine is off for it and on by default for companion personas (spec §9).

---

# Part A: Personas (PR 5a)

## File map (Part A)
| File | Change |
|---|---|
| `backend/aethel/personas/__init__.py` | package |
| `backend/aethel/personas/model.py` | `Persona`, `PersonaVoice`, `Accent`, parse/serialise `persona.md` |
| `backend/aethel/personas/store.py` | `PersonaStore`: list/get/create/update/delete/files; built-in seeding; legacy migration |
| `backend/aethel/personas/prompt.py` | `persona_system_prompt(persona, style_directive)`, replacing `chat/persona.py` |
| `backend/aethel/personas/accent.py` | dominant colour → OKLCH clamp → light/dark hex |
| `backend/aethel/personas/tone.py` | the tone engine port (lexicon + optional ONNX emotion classifier) |
| `backend/aethel/personas/distill.py` | chat-export parsers + style metrics → style directive (no lancedb or sentence-transformers) |
| `backend/aethel/personas/draft.py` | LLM drafts `persona.md` from a description |
| `backend/aethel/api/routes/personas.py` | CRUD, draft, files, style import, active persona per conversation |
| `backend/aethel/chat/service.py`, `context/recipes.py`, `voice/session.py`, `runtime/engine.py` | use the conversation's persona (prompt, voice blend, name) |
| `backend/aethel/protocol.py` | `PersonaUpdated`, `ToneChanged` |
| `frontend_app/src/features/personas/*` | studio: list, editor, voice-blend editor, style import |
| `frontend_app/src/features/shell/Rail.tsx`, `stores/ui.ts`, `styles/tokens.css` | the persona rail (avatars, `layoutId`), per-persona accent |

### Task A1: The persona model and file format
**`persona.md`:**
```markdown
---
id: rosia
name: Rosia
built_in: false
voice: {blend: {af_heart: 0.7, bf_emma: 0.3}, speed: 1.0}
accent: {light: "#b5523a", dark: "#d9806a", source: "anchor"}   # derived; null until a portrait exists
image_style: "warm film photo, soft natural light, 35mm"
anchors: [portraits/anchor.jpg]
sheet: [portraits/sheet-34.jpg, portraits/sheet-profile.jpg, portraits/sheet-smile.jpg]
image_frequency: on_request        # never | on_request | sometimes
tone_engine: true
created: 2026-10-10
---
## Personality
...
## Backstory
...
## Speaking style
...
## Boundaries
...
```

**Produces:**
```python
class PersonaVoice(BaseModel): blend: dict[str, float]; speed: float = 1.0
class Accent(BaseModel): light: str; dark: str; source: Literal["anchor", "manual"] = "anchor"
class Persona(BaseModel):
    id: str; name: str; built_in: bool = False; voice: PersonaVoice; accent: Accent | None = None
    image_style: str = ""; anchors: list[str] = []; sheet: list[str] = []
    image_frequency: Literal["never", "on_request", "sometimes"] = "on_request"; tone_engine: bool = True
    sections: dict[str, str]   # "Personality", "Backstory", "Speaking style", "Boundaries" (others kept, in order)
    created: str | None = None
def parse_persona(text: str, folder_id: str) -> Persona          # tolerant: missing sections → ""; unknown keys kept in `extra`
def render_persona(p: Persona) -> str                            # round-trips parse(render(p)) == p
ID_RE = r"^[a-z0-9][a-z0-9-]{0,40}$"
```
- [ ] **Tests:**
  - Round trip.
  - A missing frontmatter key falls back to its default.
  - An unknown section is kept in order.
  - A bad id is rejected.
  - A voice blend with an unknown voice is rejected via `VoiceBlend.parse` from Phase 4.
- [ ] Implement, then **commit** `feat(personas): persona.md model`.

### Task A2: `PersonaStore`, the built-in persona and migration
**Produces:**
```python
class PersonaStore:
    def __init__(self, root: Path): ...                  # ~/.aethel/personas
    def list(self) -> list[Persona]                      # built-in first, then by name
    def get(self, pid: str) -> Persona | None
    def create(self, p: Persona) -> Persona              # unique id from the name (slug, -2, -3 …)
    def update(self, pid: str, patch: dict) -> Persona   # sections and frontmatter fields; built-in: name/id are fixed
    def delete(self, pid: str) -> bool                   # never the built-in; moves the folder to personas/.trash/
    def folder(self, pid: str) -> Path
    def style_directive(self, pid: str) -> str | None    # personas/<id>/style.md, if any
    def ensure_builtin(self) -> None                     # creates personas/aethel/ from chat/persona.py's text, once
    def migrate_legacy(self, legacy_prompt: str | None, sentinel: Path) -> str | None
```
- **Legacy migration (D6):** if `backend/config.py`'s `BASE_PERSONA` text exists **and** legacy user data exists (`backend/data/` or `~/.persona_ai/`), create `rosia` from it once. It's guarded by the sentinel `~/.aethel/.migrated-personas`. Its voice is the legacy Kokoro voice setting if present, otherwise `af_heart`.
- `conversations.persona_id` already exists (it defaults to `aethel`).

- [ ] **Tests:**
  - `ensure_builtin` is idempotent.
  - `create` slugs the name and handles collisions.
  - The built-in can't be deleted or renamed.
  - `delete` moves the folder to the trash.
  - Migration runs once with legacy data and never without it.
  - `style_directive` reads `style.md`.
- [ ] Implement, then **commit** `feat(personas): persona folders, built-in Aethel, legacy migration`.

### Task A3: Persona-aware chat, voice and tasks
- `personas/prompt.py`: `persona_system_prompt(p, style)` gives the name line, then the sections in order (Personality, Speaking style, Boundaries; the Backstory is shortened to 600 chars), then the style directive if any.
- **Chat:** `ChatService._context` uses `persona_system_prompt(store.get(conv.persona_id) or builtin, store.style_directive(...))` instead of `system_prompt()`. The time note stays last (3b-1). The facts' persona scope uses `conv.persona_id`, which already works.
- **Voice:** `VoiceSession` takes its blend from `persona.voice`, and `ready.persona_name` from the persona.
- **Tasks:** the executor's final summary is written in the persona's voice. Add "Speak as {name}: {speaking style, first 300 chars}" to the `finish_task` description.
- **The conversation API:** `POST /api/conversations {persona_id}` validates it. `PATCH` doesn't change the persona of an existing conversation (that's a new conversation).
- [ ] **Tests:**
  - The chat prompt starts with the persona prompt and ends with the time.
  - The built-in persona's prompt equals today's text, so typed chat is unchanged.
  - The voice session uses the persona's blend.
  - An unknown `persona_id` → 422.
- [ ] Implement, then **commit** `feat(personas): chat, voice and tasks speak as the conversation's persona`.

### Task A4: Accent extraction
**Produces:**
```python
def dominant_colour(image: Image.Image, k: int = 5, seed: int = 0) -> tuple[int, int, int] | None
    # resize to 96 px, drop near-white/near-black/grey (chroma < 0.03) and skin (YCrCb skin box), k-means (numpy),
    # largest remaining cluster's centre; None if < 5% of pixels remain
def to_oklch(rgb) -> tuple[float, float, float];  def from_oklch(l, c, h) -> tuple[int, int, int]
def accent_for(image) -> Accent | None
    # light: L clamped to 0.55–0.68, C ≤ 0.13; dark: L 0.68–0.78, C ≤ 0.13; same hue
```
- **The frontend:** `tokens.css` reads `--accent` from `[data-persona-accent]`. The active persona sets `--accent-light` and `--accent-dark` on `<html>`, and the default terracotta remains for personas without an accent.

- [ ] **Tests:**
  - A synthetic image with a large blue region and a skin-tone face patch → the hue is blue, not skin.
  - Grey/white only → None.
  - The OKLCH round trip within 1/255.
  - Clamping keeps L and C in range for vivid inputs.
  - The CSS variables are applied when the active persona changes (vitest).
- [ ] Implement, then **commit** `feat(personas): accent colour from the anchor portrait`.

### Task A5: Drafting, the studio and the voice-blend editor
- **`POST /api/personas/draft {description ≤ 2000}`:** the `chat` role (purpose `persona_draft`) returns a `persona.md` body with the four sections, plus a suggested `name` and `image_style`. It's parsed and returned (not saved), and the user edits it. Add the purpose to `usage.PURPOSES` and the UI labels.
- **The studio screen** (`ui.Screen` adds `personas`, the rail gets a persona list):
  - **List:** cards with the portrait or monogram, the name and the accent swatch. A "New persona" card.
  - **Create flow, steps 1, 2 and 5 here:**
    1. Describe.
    2. Draft → editor.
    3. *(Part B: portraits.)*
    4. *(Part B: sheet.)*
    5. Voice.

    Steps 3–4 show "Add a face later" until 5b ships.
  - **Editor:** name, the four sections (textareas with autosave after 800 ms idle and a "Saved" ink line), image style, image frequency (segmented control), tone-engine switch, accent (swatch + "pick manually" colour input).
  - **Voice-blend editor:**
    - Up to 3 voices, each a select of Kokoro voices grouped by language (`/api/voice/voices`) with a weight slider (0–1; the weights are normalised on save).
    - A speed slider (0.7–1.3).
    - "Preview" plays `/api/voice/preview` with the line "Hi, it's {name}. How's your day going?"
  - **Style import:** upload a chat export (WhatsApp `.txt`, Telegram `.json`, Discord `.json`, CSV) and pick the speaker, then run `distill` (Task A6) and show the directive for editing before saving.
- **The rail:** persona avatars (the built-in first). Clicking one starts a new conversation with that persona. The active persona's avatar is shared with the header and call portrait through `layoutId` (spec §12.4).

- [ ] **Tests (vitest):**
  - The create flow, from the draft request to the editor to save.
  - Autosave debounces.
  - The blend editor normalises weights and calls preview.
  - The rail starts a conversation with the chosen persona.
  - The built-in persona can't be deleted (no button).
- [ ] **Tests (pytest):** draft parsing with a scripted LLM; the routes (CRUD, files 404 outside the folder).
- [ ] Implement, then **commit** `feat(personas): persona studio, drafting, voice blends`.

### Task A6: Distillation, tone engine, persona corpus
- **`personas/distill.py`:**
  - Port the four parsers and `extract_style_metrics` from `backend/persona_distillation.py`: message length, emoji rate, punctuation habits, common openers and closers, slang list, lowercase rate.
  - `directive(metrics, name) -> str` renders a short style directive, e.g. "Writes in short lowercase bursts, often 2–3 messages in a row; uses 'lol' and '😭'; rarely asks questions back."
  - No lancedb or sentence-transformers: the directive is saved to `style.md`.
- **The persona corpus** (spec §6.2: imported corpora are indexed separately):
  - The parsed messages from an import are stored in a `persona_lines(persona_id, text, ts)` table, migration `016`.
  - They're indexed in `personas/<id>/lines.tvim` with the same `EpisodicIndex` approach (vectors in SQLite).
  - A chat-recipe section, "How {name} tends to put things" (priority 5, below episodes), shows up to 3 similar past lines as **style examples** (wrapped untrusted).
- **`personas/tone.py`:**
  - Port the lexicon tone engine (mood/energy deltas, decay).
  - Port the optional ONNX emotion classifier, only if its model is present.
  - State per conversation lives in `conversations.meta` (add the column in `016`).
  - After each assistant turn of a persona with `tone_engine`, publish `ToneChanged {conversation_id, mood, energy, label}`. The UI drives a subtle accent glow (its opacity follows energy) on the portrait and rail avatar. Reduced motion means no glow animation.

- [ ] **Tests:**
  - Each parser on a small fixture.
  - Metrics on known text.
  - The directive mentions the dominant traits.
  - Corpus import → lines indexed → the recall section appears for a similar message and is untrusted-wrapped.
  - The tone engine's deltas, decay and label mapping.
  - `ToneChanged` emitted only when it's enabled.
- [ ] Implement, then **commit** `feat(personas): style import, persona lines, tone engine`.

### Task A7: Verify, review, PR 5a
- [ ] Suites → exit 0. Review subagent, then fixes.
- [ ] **Demo:** create "Mira" from a description, edit it, set a two-voice blend, preview it, chat (the style shows), call (her voice).
- [ ] Open the PR "Phase 5a: personas" and bind it.

---

# Part B: Images (PR 5b)

## File map (Part B)
| File | Change |
|---|---|
| `backend/aethel/images/__init__.py`, `images/backend.py` | `ImageBackend` protocol, `ImageRequest`, `ImageResult`, `pick_backend()` |
| `backend/aethel/images/gemini.py` | Gemini image model through native REST (reference images) |
| `backend/aethel/images/openrouter.py` | OpenRouter image-output models |
| `backend/aethel/images/local/zimage.py` | the ported Z-Image pipeline + VRAM handoff via `LocalLlama.suspend/resume` |
| `backend/aethel/images/compose.py` | prompt composition |
| `backend/aethel/images/identity.py` | YuNet + ArcFace identity check |
| `backend/aethel/images/models.py` | pinned model downloads (URL, SHA-256, license) |
| `backend/aethel/images/service.py` | `ImageService`: generate, check, retry, gallery, events |
| `backend/aethel/images/tool.py` | the `image_generate` chat/agent tool + frequency rule |
| `backend/aethel/protocol.py` | `ImagePending`, `ImageReady`, `ImageFailed` |
| `backend/aethel/api/routes/images.py` | serve images, gallery list, retry, portrait/sheet generation for the studio |
| `frontend_app/src/features/images/DevelopingPhoto.tsx`, `features/personas/Portraits.tsx`, `Gallery.tsx` | UI |
| `docs/third_party.md` | model licenses |
| `scripts/eval_identity.py` | E4 |

### Task B1: The backend interface and Gemini
**Produces:**
```python
@dataclass
class ImageRequest:
    prompt: str; references: list[bytes]; size: Literal["portrait", "square", "landscape"] = "portrait"
@dataclass
class ImageResult:
    data: bytes; mime: str; backend: str; model: str; latency_ms: int
class ImageBackend(Protocol):
    name: str; cloud: bool; max_references: int
    async def available(self) -> tuple[bool, str]        # (ok, why not)
    async def generate(self, req: ImageRequest) -> ImageResult   # raises ImageError(message, retryable)
class GeminiImage:      # name "gemini:<model>"
    # POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent (header x-goog-api-key)
    # body: {"contents": [{"parts": [{"text": prompt}, *[{"inline_data": {"mime_type": "image/jpeg", "data": b64}}]]}],
    #        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "3:4"|"1:1"|"4:3"}}}
    # response: candidates[0].content.parts[*].inlineData {mimeType, data}
def pick_backend(settings, keys, local_available: bool) -> ImageBackend | None
    # private → local only; else settings.images.backend order: gemini → openrouter → local
```
- [ ] **Step 1, verification (spec §10.2):** read Google's current image-generation docs (WebFetch `ai.google.dev/gemini-api/docs/image-generation`). Record in `images/gemini.py`'s docstring:
  - the current image model id (the starting candidate is `gemini-2.5-flash-image`)
  - the maximum number of reference images
  - the size and aspect-ratio options
  - whether `imageConfig` is accepted

  Pin the model id in `ImageSettings.gemini_model`.
- [ ] **Tests:**
  - The request body shape (MockTransport): text first, then inline images, the aspect ratio by size.
  - Parsing the inline image.
  - 429/5xx → retryable `ImageError`, 400 → not retryable.
  - No key → `available() == (False, "…")`.
  - `pick_backend` honours private mode.
- [ ] Implement, then **commit** `feat(images): image backend interface; Gemini`.

### Task B2: OpenRouter and local Z-Image
- **`OpenRouterImage`:** chat completions with `modalities: ["image", "text"]`. Reference images go as `image_url` content parts. The image comes back in `choices[0].message.images[0].image_url.url` (a data URL). The model comes from `ImageSettings.openrouter_model`, which is empty (disabled) until the user picks one.
- **`LocalZImage`:** port `backend/image_generator.py`'s Z-Image path (and SD 1.5 if a checkpoint is present) into `images/local/zimage.py`.
  - `available()` is true only if `models/image/` has a Z-Image transformer.
  - `generate` runs in a thread. Around generation it calls `local_llm.suspend()` / `resume()` when `torch.cuda` is present and VRAM ≤ 6 GB. This is the legacy handoff rule, using v2's `LocalLlama` methods.
  - References are ignored; `max_references = 0`. The UI labels it "Local (text only, faces may vary)".
  - Torch and diffusers are imported **lazily**, so the backend starts without them.
- [ ] **Tests:**
  - The OpenRouter body and response parsing (MockTransport).
  - `LocalZImage.available()` is False with no weights.
  - The handoff calls suspend and resume around generation, with a fake pipeline and a fake `LocalLlama`, and resume runs even when generation raises.
  - No torch import at module import time (`"torch" not in sys.modules` after importing `images.local.zimage`).
- [ ] Implement, then **commit** `feat(images): OpenRouter and local Z-Image backends`.

### Task B3: The identity check
**Produces:**
```python
@dataclass
class Face: box: tuple[int, int, int, int]; landmarks: np.ndarray; score: float   # 5 points (eyes, nose, mouth corners)
class IdentityCheck:
    def __init__(self, models_dir: Path): ...      # ~/.aethel/models/face/
    def ready(self) -> tuple[bool, str]
    def detect(self, image: np.ndarray) -> list[Face]                  # cv2.FaceDetectorYN (YuNet), score ≥ 0.8
    def embed(self, image: np.ndarray, face: Face) -> np.ndarray       # align to the 112×112 ArcFace template by
                                                                       # similarity transform on the 5 landmarks, ONNX, L2-normalised
    def compare(self, candidate: bytes, anchor_embedding: np.ndarray) -> float | None
        # the largest face's cosine with the anchor; None if no face (scenery → skip)
```
- **`images/models.py`:** `FACE_MODELS = {"yunet": {url, sha256, license}, "arcface": {url, sha256, license}}`. They're downloaded on first use to `~/.aethel/models/face/`, verified, and refused on a hash mismatch.
- **Step 1 (D5):**
  1. Fetch the candidate models' licenses: YuNet from `opencv/opencv_zoo` (MIT), and `arcfaceresnet100-8.onnx` from `onnx/models`.
  2. Record them in `docs/third_party.md`.
  3. If ArcFace's license isn't suitable for a dissertation project, stop and ask the user.

  Pin both hashes after downloading once.
- The anchor embedding is cached in `personas/<id>/anchor.npy` and refreshed when the anchor changes.

- [ ] **Tests** (small committed fixtures: two photos of the same synthetic face rendered by the image backend once, plus one of a different face; or public-domain portraits noted in `third_party.md`):
  - Same face > different face.
  - Scenery → None.
  - The alignment output is 112×112.
  - A hash mismatch → refused.
  - `ready()` is False before the download.

  Real-model tests sit behind `AETHEL_IMAGES_IT=1`; unit tests use a fake ONNX session.
- [ ] Implement, then **commit** `feat(images): face identity check (YuNet + ArcFace)`.

### Task B4: Composition, the image service, the chat tool
**Produces:**
```python
CONSISTENCY = ("Keep this exact person: same face, age, skin tone, hair and features as in the reference images. "
               "Photorealistic, natural, no text or watermarks.")
def compose(persona: Persona, scene: str, framing: str | None, expression: str | None) -> str
    # "{image_style}. {scene}. Framing: {framing}. Expression: {expression}. {CONSISTENCY}" (parts omitted when empty)
class ImageService:
    async def generate(self, *, persona: Persona, scene: str, framing=None, expression=None, conversation_id: str,
                       message_id: str | None, reason: Literal["chat", "task", "studio"]) -> str   # returns image_id
        # 1. publish ImagePending(id, caption=scene[:80], conversation_id, message_id)
        # 2. references: anchor + sheet (+ passing pool) up to backend.max_references
        # 3. generate → identity check (if the persona has an anchor) → if score < τ_id: regenerate once, keep the better
        # 4. save to personas/<id>/gallery/<image_id>.jpg + .json {scene, backend, model, score, created, conversation_id}
        # 5. if score ≥ τ_id and the pool has < 6 refs: add to the pool (gallery sidecar flag "reference": true)
        # 6. publish ImageReady(id, url, identity_score) or ImageFailed(id, message, retryable)
IMAGE_TOOL = ToolSpec("image_generate", "Send the user a photo of yourself (or of something) …",
    {"type": "object", "properties": {"scene": {"type": "string"},
      "framing": {"type": "string", "enum": ["close-up", "portrait", "half-body", "full-body", "scene"]},
      "expression": {"type": "string", "enum": ["neutral", "smiling", "laughing", "thoughtful", "surprised", "sleepy"]}},
     "required": ["scene"]})
class FrequencyRule:
    async def allowed(self, persona, conversation_id, user_text) -> bool
        # never → False; on_request → System 1 noul("Is the user asking for a picture or photo?") ≥ τ_req (provisional 0.6);
        # sometimes → asked, or ≥ 15 assistant turns since the last image in this conversation
```
- **The chat tool loop** (from 3b-3) offers `image_generate` when a backend is available and `FrequencyRule.allowed(...)` is true. The tool result tells the model "A photo is on its way (you'll see it as an attachment); mention it naturally." The generation runs as a background task, so the reply keeps streaming.
- **The message:** the assistant message's `meta.images = [{id, status, score}]` is updated when ready. The REST message list includes it, so a reload shows the photo.
- **Tasks:** the agent may call `image_generate` too (`toolgroup="images"`, on demand). The image goes to `Documents\Aethel\images\` as well when the task asks.
- **Private mode:** only `LocalZImage`. If it's not available, the tool isn't offered.

- [ ] **Tests:**
  - `compose` ordering and omissions.
  - The service with a fake backend and a fake identity check:
    - A pass → saved and pooled.
    - A fail then a pass → the second is kept.
    - A fail twice → the better is kept and marked low.
    - No face → no check.
    - Events in order: pending → ready.
  - The frequency rule for all three modes (fake S1, fake turn counts).
  - The chat tool loop with a scripted `image_generate` call → `image_pending` is published while the text keeps streaming, and `meta.images` is stored.
  - The tool isn't offered when there's no backend or in private mode without local.
- [ ] Implement, then **commit** `feat(images): persona-consistent images in chat`.

### Task B5: The developing photo, the gallery, portraits in the studio
- **`DevelopingPhoto`** (spec §10.4, §12.4):
  - On `image_pending`, a 3:4 card shows a warm paper tone with grain, blurred 14 px and sepia 0.4, with the caption in Newsreader italic.
  - It resolves over the expected generation time: a running average of past latencies per backend, kept in `localStorage`, default 12 s.
  - On `image_ready`, the image swaps in with a focus pull (blur 4 → 0, scale 1.02 → 1, gentle spring). On `image_failed`, a quiet inline note, "The photo didn't come out", with "Try again".
  - Reduced motion: opacity only.
- **The gallery** in the studio: a grid of the persona's images (the "memories" album), each with its date and an identity-score dot (ok / low). Clicking opens a lightbox. A "Use as reference" toggle is capped at 6.
- **Studio steps 3–4** (create flow):
  1. "Generate 4 faces" calls `POST /api/personas/{id}/portraits` (4 generations from `image_style` + a neutral portrait scene, no references), shown in a 2×2 grid.
  2. Pick one → it becomes `anchors[0]`, then accent extraction runs (Task A4) and the anchor embedding is cached.
  3. "Make the character sheet" generates the ¾ view, the profile, and 2 expressions with the anchor as reference. Each is identity-checked, retried once if low, and saved to `sheet`.
- [ ] **Tests (vitest):**
  - The developing card goes from the pending state to ready to failed + retry.
  - The duration comes from the stored average.
  - Reduced motion.
  - The gallery reference toggle respects the cap.
  - The portrait grid pick calls the anchor endpoint.
- [ ] **Tests (pytest):** the portrait and sheet endpoints with a fake backend; anchor → accent and embedding updated.
- [ ] Implement, then **commit** `feat(images): developing photo, gallery, faces in the studio`.

### Task B6: The E4 harness, verify, PR 5b
- **`scripts/eval_identity.py`:** 50 scenes (beach, café, rain at night, hiking, reading at home, …), all for one persona with an anchor and sheet.
  - **Conditions:** anchor only, anchor + sheet.
  - **Metrics:** the ArcFace similarity distribution (p10/p50/p90), the share below τ_id, and the regenerate rate. Then calibrate τ_id: choose the score separating same-persona images from a set of 20 different-face images (generated without references) at a 5% false-accept rate. The calibrated value goes into `ImageSettings.identity_threshold` with a comment.
  - It spends image credits, so it runs only with the user's go-ahead. It writes `~/.aethel/eval/identity.json`.
- [ ] Suites → exit 0. Review subagent, then fixes.
- [ ] **Demo:**
  1. Create a persona end to end (describe → draft → 4 faces → pick → sheet → voice).
  2. Ask "send me a picture of you at the beach": the photo develops in the chat, the face matches, and it's in the gallery.
- [ ] Open the PR "Phase 5b: images" and bind it. Update the memory file.

---

## Self-review
- **Spec §9 coverage:**
  - The folder → A1, A2.
  - The built-in persona and the Rosia migration → A2.
  - The creation flow: steps 1–2, 5 → A5; steps 3–4 → B5.
  - Accent → A4.
  - Tone engine → A6.
  - Distillation → A6.
- **§10 coverage:**
  - Trigger and frequency → B4.
  - Composition → B4.
  - Backends → B1, B2.
  - Identity → B3, B4.
  - UX → B5.
  - Gallery → B4, B5.
- **§12.4:** the shared avatar → A5; the developing photo → B5.
- **E4** → B6.
- **Names used across tasks:** `Persona`, `PersonaStore`, `persona_system_prompt`, `Accent`, `ImageBackend.generate`, `ImageRequest`, `ImageService.generate`, `IdentityCheck.compare`, `FrequencyRule.allowed`, `IMAGE_TOOL` and `ImagePending`/`Ready`/`Failed`.
