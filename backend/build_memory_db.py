import json
import os
from pathlib import Path
import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from turbovec import IdMapIndex
from config import TV_INDEX_PATH, METADATA_PATH, CLASSIFIED_MEMORIES_PATH, PERSONALITY_DATASET_PATH, EMBEDDING_MODEL, TURBOVEC_BIT_WIDTH

BACKEND_DIR = Path(__file__).parent

def resolve_path(p_str: str) -> Path:
    p = Path(p_str)
    if p.is_absolute() and p.exists():
        return p
    p_backend = BACKEND_DIR / p
    if p_backend.exists():
        return p_backend
    clean_parts = [part for part in p.parts if part != "backend"]
    p_stripped = BACKEND_DIR / Path(*clean_parts)
    if p_stripped.exists():
        return p_stripped
    return p_backend

source_path = resolve_path(CLASSIFIED_MEMORIES_PATH)
if not source_path.exists():
    fallback_path = resolve_path(PERSONALITY_DATASET_PATH)
    if fallback_path.exists():
        print(f"[MEMORY BUILDER] '{source_path}' not found. Using dataset fallback '{fallback_path}'...")
        source_path = fallback_path
    else:
        raise FileNotFoundError(f"Neither classified memories ({source_path}) nor personality dataset ({fallback_path}) could be found.")

tv_index_target = str(resolve_path(TV_INDEX_PATH))
metadata_target = str(resolve_path(METADATA_PATH))

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"[MEMORY BUILDER] Loading memory source from: {source_path}")
print(f"[MEMORY BUILDER] Initializing sentence-transformer core on {device.upper()}...")
model = SentenceTransformer(EMBEDDING_MODEL, device=device)

with open(source_path, "r", encoding="utf-8") as f:
    data = json.load(f)

abstractions = []
valid_ids = []
metadata_lookup = {}

for i, item in enumerate(data):
    if isinstance(item, str):
        text = item
        tags = ["conversation"]
    else:
        text = item.get("text", "")
        tags = item.get("tags", ["conversation"])

    if not text:
        continue

    abstraction = f"Conversation style example. Tone: {', '.join(tags)}. Message: {text}"
    abstractions.append(abstraction)
    valid_ids.append(i)

    metadata_lookup[str(i)] = {
        "raw_text": text,
        "tags": tags
    }

print(f"[MEMORY BUILDER] Batch vectorizing {len(abstractions)} memories on GPU (batch_size=256)...")
vectors = model.encode(abstractions, batch_size=256, show_progress_bar=True, convert_to_numpy=True).astype(np.float32)
ids_np = np.array(valid_ids, dtype=np.uint64)

print("[MEMORY BUILDER] Committing arrays to TurboQuant layout...")
idx = IdMapIndex(bit_width=TURBOVEC_BIT_WIDTH)
idx.add_with_ids(vectors, ids_np)

os.makedirs(os.path.dirname(tv_index_target), exist_ok=True)
idx.write(tv_index_target)
with open(metadata_target, "w", encoding="utf-8") as f:
    json.dump(metadata_lookup, f, indent=4)

print(f"Success! Turbovec index written to '{tv_index_target}' and lookup map to '{metadata_target}'.")