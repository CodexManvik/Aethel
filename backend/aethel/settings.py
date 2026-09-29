"""User settings: one validated JSON document in the SQLite settings table."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .store.db import Database

ProviderId = Literal["groq", "gemini", "openrouter", "custom", "local"]


class RouteEntry(BaseModel):
    provider: ProviderId
    model: str = Field(min_length=1)


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
    skill_threshold: float = Field(default=0.6, ge=0.0, le=1.0)  # not yet calibrated
    judge_threshold: float = Field(default=0.5, ge=0.0, le=1.0)  # not yet calibrated


class AppSettings(BaseModel):
    roles: dict[str, list[RouteEntry]] = Field(default_factory=default_roles)
    custom_base_url: str = ""
    private_mode: bool = False
    internet: bool = False
    local_llm: LocalLLMSettings = Field(default_factory=LocalLLMSettings)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=16, le=32768)
    agent_max_tokens: int = Field(default=8192, ge=256, le=65536)  # tasks write whole files in one call
    history_window: int = Field(default=24, ge=2, le=200)
    system1: System1Settings = Field(default_factory=System1Settings)
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
        return AppSettings.model_validate_json(row["value"]) if row else AppSettings()

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
