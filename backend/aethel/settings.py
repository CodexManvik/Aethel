"""User settings: one validated JSON document in the SQLite settings table."""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from .store.db import Database

ProviderId = Literal["groq", "gemini", "openrouter", "custom", "local"]


class RouteEntry(BaseModel):
    provider: ProviderId
    model: str = Field(min_length=1)
    # The model's context window in tokens, for the context builder's budget. The hosted models Aethel
    # suggests all take 32k or more; lower it for a smaller model. (The per-role caps usually bind first.)
    context_size: int = Field(default=32768, ge=1024, le=2_000_000)


class LocalLLMSettings(BaseModel):
    model_path: str = ""
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
        # Background housekeeping (fact extraction). Groq's free-tier limits are per model, so this keeps it
        # off the chat model's quota. The chat models follow it in the chain (providers/router.py).
        # Its quality is checked by scripts/eval_extract.py (not yet measured).
        "utility": [RouteEntry(provider="groq", model="llama-3.1-8b-instant", context_size=131072)],
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


def _check_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not re.match(r"^https?://[^\s/]+", value):
        raise ValueError("must start with http:// or https://, e.g. http://127.0.0.1:1234/v1")
    return value


class AppSettings(BaseModel):
    roles: dict[str, list[RouteEntry]] = Field(default_factory=default_roles)
    custom_base_url: str = ""  # empty, or an http(s) URL of an OpenAI-compatible API (usually ending in /v1)
    private_mode: bool = False
    internet: bool = False
    local_llm: LocalLLMSettings = Field(default_factory=LocalLLMSettings)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=16, le=32768)
    agent_max_tokens: int = Field(default=8192, ge=256, le=65536)  # tasks write whole files in one call
    history_window: int = Field(default=24, ge=2, le=200)
    system1: System1Settings = Field(default_factory=System1Settings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    token_saving: TokenSavingSettings = Field(default_factory=TokenSavingSettings)
    # Upper bound on the prompt per role, whatever the model allows: long prompts are slow and costly.
    context_caps: dict[str, int] = Field(default_factory=lambda: {"chat": 16000, "agent": 24000})

    @field_validator("custom_base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _check_base_url(value)
    replay_thumbnails: bool = True  # a small screenshot after each on-screen step, kept only on this PC
    auto_approve_skills: bool = True  # learned skills go live at once (spec §6.3); off = quarantined until approved


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key != "roles":
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
            # A custom URL saved before it was validated mustn't lock the user out of every setting.
            data = json.loads(row["value"])
            data["custom_base_url"] = ""
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
