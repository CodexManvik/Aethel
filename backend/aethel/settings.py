"""User settings: one validated JSON document in the SQLite settings table."""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .store.db import Database

# A built-in provider, or the id of one of the user's endpoints (AppSettings.endpoints).
BUILTIN_PROVIDERS = ("groq", "gemini", "openrouter", "local")
ENDPOINT_ID_RE = r"^[a-z][a-z0-9-]{1,30}$"
ProviderId = str


def _check_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not re.match(r"^https?://[^\s/]+", value):
        raise ValueError("must start with http:// or https://, e.g. http://127.0.0.1:1234/v1")
    return value


class RouteEntry(BaseModel):
    provider: ProviderId = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1)
    # The model's context window in tokens, for the context builder's budget. The hosted models Aethel
    # suggests all take 32k or more; lower it for a smaller model. (The per-role caps usually bind first.)
    context_size: int = Field(default=32768, ge=1024, le=2_000_000)
    # A model that reasons before it answers (Gemma 4, Qwen3 thinking...) spends output tokens on it first, so it
    # gets at least REASONING_REPLY_TOKENS to reply in, and the context budget reserves that much.
    reasoning: bool = False


REASONING_REPLY_TOKENS = 2048


class LocalLLMSettings(BaseModel):
    model_path: str = ""   # a .gguf anywhere; empty: the largest in models/llm
    server_path: str = ""  # llama-server(.exe), or the folder it's in; empty: LLAMA_SERVER_BINARY, PATH, bin/
    context_size: int = Field(default=0, ge=0)
    threads: int = Field(default=4, ge=1)
    gpu_layers: int = Field(default=99, ge=0)


def default_roles() -> dict[str, list[RouteEntry]]:
    # Starting points only: Settings lists each provider's live models.
    return {
        "chat": [
            RouteEntry(provider="groq", model="llama-3.3-70b-versatile"),
            RouteEntry(provider="openrouter", model="google/gemini-2.5-flash"),
            RouteEntry(provider="local", model="local"),
        ],
        "agent": [
            RouteEntry(provider="gemini", model="gemini-2.5-flash"),
            RouteEntry(provider="openrouter", model="google/gemini-2.5-flash"),
            RouteEntry(provider="local", model="local"),
        ],
        "vision": [RouteEntry(provider="gemini", model="gemini-2.5-flash")],
        # Background housekeeping (fact extraction); the chat models follow it in the chain (providers/router.py).
        # Empty until scripts/eval_extract.py shows a small model extracts as well as chat (not yet measured):
        # the candidate is Groq llama-3.1-8b-instant (Groq's free-tier limits are per model, so it would keep
        # this off the chat model's quota). Empty also means no provider the chat chain doesn't already use.
        "utility": [],
    }


class System1Settings(BaseModel):
    enabled: bool = True
    # Measured by scripts/eval_s1_intent.py on eval/s1_intent.jsonl (2026-09-29, laya@68f27df):
    # act at 0.5 was chosen on the dev half; held out: precision 0.91, recall 0.91, accuracy 0.92.
    auto_tasks: bool = True
    intent_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    # A wrong stop throws away the user's work, a missed one is one click (Cancel, Ctrl+Alt+Esc),
    # so stop favours precision: 0.9 gives P=1.00, R=0.88 on all 56 rows (in-sample, not held out).
    stop_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    # Measured by scripts/eval_s1_skill.py (30 goals, 8 skills): no wrong pick at any threshold; 0.3 chosen
    # on the dev half; held out: precision 1.00, coverage 0.89.
    skill_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    # A "none of these" only hides the maybe-helpful skills when this sure: at 0.95 it hid them in 9 of 12
    # goals with no fitting skill and in none of the 4 where System 1 missed a fitting one (in-sample).
    skill_none_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    # A judge check fails only below this P(yes). On eval/s1_judge.jsonl (20 author-written pairs, in-sample):
    # 0.2 rejects 9 of 10 false claims and none of the 10 true ones.
    judge_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    # A macro step's element, when it isn't an exact match: not yet calibrated (the E2 grounding set).
    ground_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class BrowserSettings(BaseModel):
    # Aethel's own background browser. True runs it with a window you can watch; it takes effect the next time
    # the browser starts (Settings -> Browser has a Restart for that).
    show: bool = False


class VoiceSettings(BaseModel):
    # Speech to text. "azure" needs the Azure Speech key (Settings -> Voice) and a region; without them, or in
    # private mode, or when Azure can't be reached, the local faster-whisper model is used (slow on a laptop CPU:
    # about 8 s for a 2-second sentence with "small", measured 2026-10-14).
    stt: Literal["azure", "local"] = "azure"
    stt_model: str = Field(default="small", pattern=r"^[A-Za-z0-9._-]+$")  # the local fallback's size
    language: str = Field(default="en-GB", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$")  # Azure takes a locale
    azure_region: str = Field(default="", pattern=r"^([a-z][a-z0-9]{2,29})?$")  # e.g. uksouth
    # Text to speech. Kokoro runs on this PC and takes the persona's voice blend; "azure" speaks with a neural
    # Azure voice instead (never in private mode). Azure is also the fallback when Kokoro fails.
    tts: Literal["kokoro", "azure"] = "kokoro"
    azure_voice: str = Field(default="en-GB-SoniaNeural", pattern=r"^[A-Za-z0-9-]{3,60}$")
    # Whether System 1 judges if a pause is the end of a thought. OFF: scripts/eval_s1_eot.py (100 hand-written
    # transcripts, 2026-10-14, laya@68f27df) found Laya close to chance at this: of five wordings none reached
    # precision 0.9 except at a threshold where it fires on ~4% of finished thoughts (held out: 1 of 25), and a false
    # "finished" talks over the user. So every turn waits max_wait_ms of silence (voice-activity detection alone);
    # E3 compares the two. Fine-tuning Laya on this set, or a better judge, is the way to make it worth turning on.
    system1_end_of_turn: bool = False
    # P(finished) at which a pause is answered after short_wait_ms; the lowest with dev precision >= 0.9. Only
    # used when system1_end_of_turn is on.
    eot_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    short_wait_ms: int = Field(default=300, ge=0, le=5000)    # silence before answering a finished thought
    max_wait_ms: int = Field(default=1500, ge=200, le=10000)  # silence before answering an unfinished one
    ack: bool = True        # a short "Mm." when the first words of the reply are slow
    narration: bool = True  # speak a task's progress during a call
    default_blend: dict[str, float] = Field(default_factory=lambda: {"af_heart": 1.0, "speed": 1.0})


ImageBackendId = Literal["gemini", "openai", "comfyui", "openrouter", "local"]
IMAGE_BACKENDS: tuple[str, ...] = ("gemini", "openai", "comfyui", "openrouter", "local")
NEW_IMAGE_BACKENDS = ("openai", "comfyui")  # added 2026-10-19


class OpenAIImageSettings(BaseModel):
    # Any server with the OpenAI images API (/images/generations, /images/edits): OpenAI itself, LocalAI, proxies.
    # Its key, if it needs one, is kept as "endpoint:images-openai". Empty URL or model: off.
    base_url: str = ""
    model: str = Field(default="", pattern=r"^([A-Za-z0-9._:/-]{1,120})?$")

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_base_url(value)


class ComfyUISettings(BaseModel):
    # A ComfyUI server and the user's own workflow, exported with "Save (API format)"; images/comfyui.py fills its
    # {{prompt}}, {{negative}}, {{seed}}, {{width}}, {{height}} and {{reference}} placeholders. Empty: off.
    base_url: str = ""
    workflow_path: str = ""

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_base_url(value)


class ImageSettings(BaseModel):
    # Which backend draws a persona's photos, tried in this order; one that isn't set up is skipped. Private mode
    # uses only those on this PC: the local model, and an OpenAI-compatible or ComfyUI server at a loopback address.
    backends: list[ImageBackendId] = Field(default_factory=lambda: list(IMAGE_BACKENDS))
    openai: OpenAIImageSettings = Field(default_factory=OpenAIImageSettings)
    comfyui: ComfyUISettings = Field(default_factory=ComfyUISettings)
    local_dir: str = ""  # the folder with the Z-Image weights; empty: models/image
    # Gemini's native image model, through generateContent (images/gemini.py records what was checked). Settings
    # -> Images lists the ids the key can use.
    gemini_model: str = Field(default="gemini-3.1-flash-image", pattern=r"^[A-Za-z0-9._-]{3,80}$")
    # An OpenRouter model that outputs images, e.g. "google/gemini-3.1-flash-image"; empty leaves OpenRouter off.
    openrouter_model: str = Field(default="", pattern=r"^([A-Za-z0-9._:/-]{3,120})?$")
    # Cosine (ArcFace) a photo's face must reach against the anchor portrait. Provisional (spec §10.3) until E4
    # (scripts/eval_identity.py) calibrates it at a 5% false-accept rate.
    identity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    # System 1's P(the user asks for a picture) for "when asked" personas. Provisional, not measured.
    request_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    @field_validator("backends")
    @classmethod
    def _every_backend(cls, value: list[str]) -> list[str]:
        """A list saved before "openai" and "comfyui" existed (it has neither) gains them, before "local" (the slowest).
        A list that has either was written knowing them, so a backend left out of it stays out."""
        out = list(dict.fromkeys(value))
        if not any(name in out for name in NEW_IMAGE_BACKENDS):
            for name in NEW_IMAGE_BACKENDS:
                out.insert(out.index("local") if "local" in out else len(out), name)
        return out


class MemorySettings(BaseModel):
    facts_enabled: bool = True
    # Measured by scripts/eval_s1_fact.py on eval/s1_fact.jsonl (120 hand-written messages, 2026-09-30,
    # laya@68f27df): the highest threshold with dev recall >= 0.9 is 0.05; held out: recall 1.00, precision 0.64,
    # so the gate lets ~78% of messages through and saves only ~22% of extraction calls. Laya is weak zero-shot
    # here (ECE 0.26); fine-tuning it on this set is the obvious next step.
    fact_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    episodic_enabled: bool = True
    # Exact bge cosine an earlier exchange must reach to be recalled. Measured by scripts/eval_episodic.py on
    # eval/episodic.json (8 scripted conversations, 40 queries, 2026-09-30): 0.63 chosen on the even queries;
    # held out (20 queries, so rough): recall 0.71, precision 0.67, 1 of 6 unanswerable queries recalled something.
    episodic_min_score: float = Field(default=0.63, ge=0.0, le=1.0)
    facts_k: int = Field(default=6, ge=0, le=30)
    episodes_k: int = Field(default=3, ge=0, le=10)


class TokenSavingSettings(BaseModel):
    # Leave stale screen/page snapshots out of a task's context once a newer one exists (token spec §5.1).
    # On by default; scripts/eval_tokens.py confirms it doesn't cost task success (not yet measured).
    mask_superseded: bool = True
    mask_batch: int = Field(default=3, ge=1, le=20)  # rewrite earlier messages at most once per this many
    # Send tool schemas without JSON-schema annotation and with short curated descriptions (tools/compact.py).
    # On: every parameter, type, enum and required flag is kept, so a tool is called the same way.
    compact_schemas: bool = True
    # A task carries the core tools plus a catalogue of the rest; `use_tools` adds a group on request
    # (tools/groups.py). Off: every call carries every tool. Not measured yet; it's turned on by default only
    # if `scripts/eval_tokens.py --axis groups` shows no drop in task success.
    tool_groups: bool = False


class Endpoint(BaseModel):
    """An OpenAI-compatible server the user added: LM Studio, Ollama, vLLM, another PC, a hosted API. Its key (if
    any) is kept as "endpoint:<id>" ("custom" for the one migrated from the old single URL)."""
    id: str = Field(pattern=ENDPOINT_ID_RE)
    name: str = Field(min_length=1, max_length=60)
    base_url: str = ""  # empty: not set up yet (unusable until it is)

    @field_validator("id")
    @classmethod
    def _not_builtin(cls, value: str) -> str:
        if value in BUILTIN_PROVIDERS or value == "images-openai":  # the image server's key is "endpoint:images-openai"
            raise ValueError(f"{value!r} is a built-in provider's name")
        return value

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_base_url(value)


class AppSettings(BaseModel):
    roles: dict[str, list[RouteEntry]] = Field(default_factory=default_roles)
    endpoints: list[Endpoint] = Field(default_factory=list)
    private_mode: bool = False
    internet: bool = False
    local_llm: LocalLLMSettings = Field(default_factory=LocalLLMSettings)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=16, le=32768)
    agent_max_tokens: int = Field(default=8192, ge=256, le=65536)  # tasks write whole files in one call
    history_window: int = Field(default=24, ge=2, le=200)
    system1: System1Settings = Field(default_factory=System1Settings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    images: ImageSettings = Field(default_factory=ImageSettings)
    token_saving: TokenSavingSettings = Field(default_factory=TokenSavingSettings)
    # Upper bound on the prompt per role, whatever the model allows: long prompts are slow and costly.
    context_caps: dict[str, int] = Field(default_factory=lambda: {"chat": 16000, "agent": 24000})

    @model_validator(mode="before")
    @classmethod
    def _migrate_custom(cls, data):
        """The single "custom_base_url" of earlier versions is now the endpoint "custom"; a role that names
        "custom" keeps working. Still accepted on input (the eval scripts set it)."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        url = data.pop("custom_base_url", None)  # None: not given; "" clears it, as it always did
        raw = data.get("endpoints") or []
        if not isinstance(raw, list):
            return data  # not a list: pydantic rejects it with a proper message (a 422, not a crash here)
        endpoints = [e.model_dump() if isinstance(e, BaseModel) else e for e in raw]
        has_custom = any(isinstance(e, dict) and e.get("id") == "custom" for e in endpoints)
        roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
        uses_custom = any((e.get("provider") if isinstance(e, dict) else getattr(e, "provider", None)) == "custom"
                          for entries in roles.values() if isinstance(entries, list) for e in entries)
        if url is not None and has_custom:
            endpoints = [{**e, "base_url": url} if isinstance(e, dict) and e.get("id") == "custom" else e
                         for e in endpoints]
        elif url:
            endpoints.insert(0, {"id": "custom", "name": "Custom", "base_url": url})
        elif uses_custom and not has_custom:
            endpoints.insert(0, {"id": "custom", "name": "Custom", "base_url": ""})
        data["endpoints"] = endpoints
        return data

    @model_validator(mode="after")
    def _known_providers(self):
        ids = [e.id for e in self.endpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("two endpoints have the same id")
        known = set(BUILTIN_PROVIDERS) | set(ids)
        for role, entries in self.roles.items():
            for entry in entries:
                if entry.provider not in known:
                    raise ValueError(f"the {role} role uses {entry.provider!r}, which is no longer a provider or "
                                     "endpoint: take it out of the role first")
        return self

    def endpoint(self, endpoint_id: str) -> Endpoint | None:
        return next((e for e in self.endpoints if e.id == endpoint_id), None)

    @property
    def custom_base_url(self) -> str:
        """Earlier versions' single custom URL, read through (scripts still use it)."""
        ep = self.endpoint("custom")
        return ep.base_url if ep else ""
    replay_thumbnails: bool = True  # a small screenshot after each on-screen step, kept only on this PC
    auto_approve_skills: bool = True  # learned skills go live at once (spec §6.3); off = quarantined until approved
    # Off: tasks run without learned skills or macros and nothing new is learned (E1's "LLM only" condition,
    # and scripts/eval_tokens.py, which compares full LLM runs).
    use_learned_skills: bool = True


REPLACED_WHOLE = {"roles", "default_blend", "backends"}  # a patch to these is the new value, not merged into the old one


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key not in REPLACED_WHOLE:
            _deep_merge(base[key], value)
        elif key == "roles" and isinstance(value, dict):
            base.setdefault("roles", {}).update(value)
        else:
            base[key] = value
    return base


class SettingsService:
    KEY = "app"
    LEGACY_MARKER = "legacy_imported"

    def __init__(self, db: Database):
        self.db = db

    def get(self) -> AppSettings:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (self.KEY,))
        if not row:
            return AppSettings()
        try:
            return AppSettings.model_validate_json(row["value"])
        except ValidationError:
            # Something saved before it was checked mustn't lock the user out of every setting: repair only what is
            # broken (an old custom URL, an endpoint URL, a role naming a provider that's gone), keep the rest.
            data = json.loads(row["value"])
            try:
                _check_base_url(str(data.get("custom_base_url") or ""))
            except ValueError:
                data["custom_base_url"] = ""
            endpoints = [ep for ep in data.get("endpoints") or [] if isinstance(ep, dict)]
            for ep in endpoints:
                try:
                    _check_base_url(str(ep.get("base_url", "")))
                except ValueError:
                    ep["base_url"] = ""
            data["endpoints"] = endpoints
            known = set(BUILTIN_PROVIDERS) | {str(ep.get("id")) for ep in endpoints}
            if data.get("custom_base_url") or any(
                    isinstance(e, dict) and e.get("provider") == "custom" for es in (data.get("roles") or {}).values()
                    for e in es or []):
                known.add("custom")  # the migration makes it
            data["roles"] = {role: [e for e in es or [] if isinstance(e, dict) and e.get("provider") in known]
                             for role, es in (data.get("roles") or {}).items()}
            return AppSettings.model_validate(data)

    def update(self, patch: dict) -> AppSettings:
        merged = _deep_merge(self.get().model_dump(), patch)
        settings = AppSettings.model_validate(merged)
        self._put(self.KEY, settings.model_dump_json())
        return settings

    def import_legacy(self, path: Path) -> bool:
        """Copy local-LLM fields from the v1 backend/data/settings.json, once."""
        if self.db.query_one("SELECT 1 FROM settings WHERE key = ?", (self.LEGACY_MARKER,)):
            return False
        if not path.is_file():
            return False
        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        local = {
            "model_path": str(legacy.get("llm_model_path") or ""),
            "context_size": int(legacy.get("context_size", 0) or 0),
            "threads": int(legacy.get("threads", 4) or 4),
            "gpu_layers": int(legacy.get("gpu_layers", 99)),
        }
        self.update({"local_llm": local})
        self._put(self.LEGACY_MARKER, "true")
        return True

    def _put(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
