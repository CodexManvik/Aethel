"""Filesystem locations. Everything user-specific lives under ~/.aethel."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(PROJECT_ROOT / "models")))
LEGACY_SETTINGS_PATH = PROJECT_ROOT / "backend" / "data" / "settings.json"


def aethel_home() -> Path:
    home = Path(os.environ.get("AETHEL_HOME", str(Path.home() / ".aethel")))
    home.mkdir(parents=True, exist_ok=True)
    return home


def db_path() -> Path:
    return aethel_home() / "aethel.db"
