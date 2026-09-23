import os
import json

from pathlib import Path
BACKEND_DIR = Path(__file__).parent
EXPORTS_DIR = str(BACKEND_DIR / "data" / "exports")
OUTPUT_FILE = str(BACKEND_DIR / "data" / "personality_dataset.json")

MIN_LENGTH = 5

LOW_SIGNAL = {
    "ok", "k", "lol", "lmao", "real", "fr", "?",
    "w", "nah", "yes", "no", "true", "false"
}

all_messages = []

for filename in os.listdir(EXPORTS_DIR):

    if not filename.endswith(".json"):
        continue

    path = os.path.join(EXPORTS_DIR, filename)

    print(f"Reading: {filename}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

    except Exception as e:
        print(f"Failed loading {filename}: {e}")
        continue

    if not isinstance(data, list):
        print(f"Skipping {filename} (not a list)")
        continue

    for msg in data:

        if not isinstance(msg, dict):
            continue

        content = msg.get("Contents", "")

        if not isinstance(content, str):
            continue

        content = content.strip()
        if len(content.split()) < 3:
            continue

        if content.lower() in ["rosia", "rose", "chanel"]:
            continue

        if len(content) < MIN_LENGTH:
            continue

        if content.lower() in LOW_SIGNAL:
            continue

        if content.startswith("http"):
            continue

        all_messages.append({
            "text": content,
            "timestamp": msg.get("Timestamp", "")
        })

print(f"\nCollected {len(all_messages)} useful messages")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(all_messages, f, ensure_ascii=False, indent=2)

print(f"Saved cleaned dataset to {OUTPUT_FILE}")