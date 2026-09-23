import os
import json
import numpy as np
import ftfy
import torch
from rapidfuzz import fuzz
from detoxify import Detoxify
from sentence_transformers import SentenceTransformer

# Configurations
from pathlib import Path
BACKEND_DIR = Path(__file__).parent
EXPORTS_DIR = str(BACKEND_DIR / "data" / "exports")
OUTPUT_FILE = str(BACKEND_DIR / "data" / "personality_dataset.json")
MIN_WORDS = 1
MAX_SIMILARITY = 92
MAX_SEMANTIC_SIM = 0.95
BATCH_SIZE = 128    # Doubled batch size for faster parallel GPU ingestion
BUFFER_LIMIT = 500  # Sliding memory buffer limit

# Enable CUDA hardware acceleration optimizations if available
device = "cuda" if torch.cuda.is_available() else "cpu"
if device == "cuda":
    torch.backends.cudnn.benchmark = True

print(f"Loading models on {device}...")
# Switched to 'unbiased' to provide the 'sexual_explicit' evaluation metric
toxicity_model = Detoxify("unbiased", device=device)
embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device=device)

def is_basic_garbage(text):
    """Fast CPU-only pre-filter to drop unusable strings instantly"""
    if not text:
        return True
    text_stripped = text.strip()
    if not text_stripped or len(text_stripped.split()) < MIN_WORDS:
        return True
    if text_stripped.startswith("http"):
        return True
    return False

def fast_cosine_sim(query_norm, buffer_matrix):
    """Blazing fast vectorized matrix similarity check"""
    # Calculate dot-product across the entire buffer matrix simultaneously
    return np.max(np.dot(buffer_matrix, query_norm))

def process_batch(batch_data, stored_texts, stored_embeddings, seen_exact):
    """Processes message batches using optimized filter routing"""
    # Ensure raw inputs are structurally clean ahead of inference
    texts = [ftfy.fix_text(item['text']).strip() for item in batch_data]
    
    # 1. GPU Batch Inference: Toxicity Scores
    results = toxicity_model.predict(texts)
    
    # 2. GPU Batch Inference: Semantic Vectors
    embeddings = embedding_model.encode(texts, show_progress_bar=False)
    
    # Pre-normalize the entire batch of vectors directly to keep sliding dot products pure
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized_embeddings = embeddings / norms
    
    valid_items = []
    
    for i, text in enumerate(texts):
        text_lower = text.lower()
        
        # Filter A: Instant O(1) Exact Match lookup
        if text_lower in seen_exact:
            continue
            
        # Filter B: Toxicity Threshold Evaluation
        if results['sexual_explicit'][i] > 0.80 or results['severe_toxicity'][i] > 0.90:
            continue
            
        # Filter C: High-Speed Vectorized Semantic Match
        if len(stored_embeddings) > 0:
            buffer_matrix = np.array(stored_embeddings)
            if fast_cosine_sim(normalized_embeddings[i], buffer_matrix) > MAX_SEMANTIC_SIM:
                continue

        # Filter D: Deferred CPU Fuzzy Match 
        # Only triggered if the text survives the optimized vector checks above
        is_fuzzy = False
        for old in stored_texts:
            if fuzz.ratio(text_lower, old) > MAX_SIMILARITY:
                is_fuzzy = True
                break
        if is_fuzzy:
            continue

        # Item successfully qualified
        valid_items.append({
            "text": text,
            "timestamp": batch_data[i]['timestamp']
        })
        
        seen_exact.add(text_lower)
        stored_texts.append(text_lower)
        stored_embeddings.append(normalized_embeddings[i])
        
        # Enforce sliding buffer window bounds strictly to prevent memory leaks
        if len(stored_texts) > BUFFER_LIMIT:
            stored_texts.pop(0)
            stored_embeddings.pop(0)

    return valid_items

# Pipeline Data Tracking
all_messages = []
stored_texts = []
stored_embeddings = []
seen_exact = set()

# Pre-load and clean baseline targets into pipeline memory
raw_queue = []
if os.path.exists(EXPORTS_DIR):
    for filename in os.listdir(EXPORTS_DIR):
        if not filename.endswith(".json"): 
            continue
        filepath = os.path.join(EXPORTS_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                for msg in data:
                    content = msg.get("Contents", "")
                    if not is_basic_garbage(content):
                        raw_queue.append({
                            "text": content,
                            "timestamp": msg.get("Timestamp", "")
                        })
        except Exception as e:
            print(f"Skipping corrupt export chunk {filename}: {e}")

print(f"Pre-filtered {len(raw_queue)} messages. Starting optimized batch processing...")

# Process cleanly scaled segments
for i in range(0, len(raw_queue), BATCH_SIZE):
    batch = raw_queue[i : i + BATCH_SIZE]
    processed = process_batch(batch, stored_texts, stored_embeddings, seen_exact)
    all_messages.extend(processed)
    
    if i > 0 and i % (BATCH_SIZE * 10) == 0:
        print(f"Progress: {i}/{len(raw_queue)} messages handled.")

print(f"\nCollected {len(all_messages)} high-quality messages.")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(all_messages, f, ensure_ascii=False, indent=2)

print(f"Saved cleaned dataset successfully to {OUTPUT_FILE}")