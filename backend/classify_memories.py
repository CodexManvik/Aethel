import json
import re
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

LLAMA_URL = "http://127.0.0.1:8080/completion"
from pathlib import Path
BACKEND_DIR = Path(__file__).parent
INPUT_FILE = str(BACKEND_DIR / "data" / "personality_dataset.json")
OUTPUT_FILE = str(BACKEND_DIR / "data" / "classified_memories.json")

# Number of simultaneous HTTP worker threads feeding the local server
MAX_WORKERS = 10  

VALID_TAGS = [
    "playful",
    "teasing",
    "sarcastic",
    "affectionate",
    "emotional",
    "gaming",
    "nerdy",
    "chaotic",
    "dominant",
    "clingy",
    "dry_humor",
    "flirty",
    "casual"
]

# Use a globally shared connection pool to eliminate TCP handshake latency
http_session = requests.Session()


def clean_and_parse_json(raw_text):
    """Safely extracts JSON arrays even if wrapped in markdown blocks or extra spaces."""
    try:
        # Search directly for array structure brackets
        match = re.search(r"\[.*?\]", raw_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(raw_text)
    except Exception:
        return None


def classify_single_message(item):
    """Self-contained worker function executed concurrently by the thread pool."""
    text = item["text"]

    prompt = f"""
Analyze this Discord message.

MESSAGE:
{text}

AVAILABLE TAGS:
{", ".join(VALID_TAGS)}

Rules:
- Maximum 2 tags
- Only assign tags directly visible
- Prefer casual if uncertain

Return ONLY a JSON array.

Example:
["playful"]
"""

    payload = {
        "prompt": prompt.strip(),
        "temperature": 0.3,
        "top_p": 0.9,
        "n_predict": 30,
        "stop": ["\n", "]\n"]
    }

    try:
        response = http_session.post(LLAMA_URL, json=payload, timeout=60)
        result = response.json()
        raw_content = result.get("content", "").strip()

        tags = clean_and_parse_json(raw_content)

        if isinstance(tags, list):
            tags = [t for t in tags if t in VALID_TAGS][:2]
        else:
            tags = []

        if not tags:
            tags = ["casual"]

    except Exception:
        tags = ["casual"]

    return {
        "text": text,
        "tags": tags
    }


def main():
    print(f"Loading pre-filtered dataset from {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    classified = []
    
    print(f"Classifying {len(data)} unique messages using {MAX_WORKERS} concurrent workers...")
    
    # Execute inference calls concurrently while gathering outputs seamlessly
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Map target dictionary objects to active worker futures
        future_to_item = {executor.submit(classify_single_message, item): item for item in data}
        
        for future in tqdm(as_completed(future_to_item), total=len(data), desc="Classifying"):
            try:
                result = future.result()
                classified.append(result)
            except Exception as e:
                # Fallback safeguard to preserve pipeline integrity if an edge case hard-crashes
                classified.append({
                    "text": future_to_item[future]["text"],
                    "tags": ["casual"]
                })

    print(f"\nWriting final mapped tags to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)

    print("Finished classifying successfully!")


if __name__ == "__main__":
    main()