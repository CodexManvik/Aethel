"""Regenerate frontend_app/src/lib/events.schema.json from backend event models."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.api.events import SCHEMA_PATH, export_schema  # noqa: E402

SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
SCHEMA_PATH.write_text(json.dumps(export_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {SCHEMA_PATH}")
