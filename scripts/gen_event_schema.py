"""Regenerate frontend_app/src/lib/events.schema.json (and voice.schema.json) from the backend protocol models."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from aethel.protocol import SCHEMA_PATH, export_schema  # noqa: E402
from aethel.voice.protocol import VOICE_SCHEMA_PATH, export_voice_schema  # noqa: E402

for path, schema in ((SCHEMA_PATH, export_schema()), (VOICE_SCHEMA_PATH, export_voice_schema())):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path}")
