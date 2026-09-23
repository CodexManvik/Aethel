"""
persona_distillation.py

Privacy-First Persona & Chat Style Distillation Pipeline.
Parses exported chat logs (WhatsApp, Telegram, Discord, CSV) locally without storing
raw messages. Extracts mathematical style metrics (Type-Token Ratio, emoji density,
capitalization/punctuation habits, response length distribution) and synthesizes a
Document-Grounded Persona Style Directive for system prompt injection.
"""

import re
import json
import csv
import io
import math
import unicodedata
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
import lancedb
import pyarrow as pa
from pydantic import BaseModel, Field

# Local Sentence Transformer model for style embedding
import torch
from sentence_transformers import SentenceTransformer
from config import METADATA_PATH

LANCEDB_DIR = Path.home() / ".persona_ai" / "lancedb"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_embed_model = None


def get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _embed_model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    return _embed_model


# ─────────────────────────────────────────────────────────────
# LanceDB `persona_style` Table Setup
# ─────────────────────────────────────────────────────────────

def get_style_db_table():
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))

    if "persona_style" not in db.table_names():
        schema = pa.schema([
            ("persona_id", pa.string()),
            ("platform", pa.string()),
            ("ttr", pa.float32()),
            ("avg_words", pa.float32()),
            ("emoji_rate", pa.float32()),
            ("lowercase_rate", pa.float32()),
            ("style_directive", pa.string()),
            ("vector", pa.list_(pa.float32(), 384)),
            ("created_at", pa.string()),
        ])
        return db.create_table("persona_style", schema=schema)
    return db.open_table("persona_style")


# ─────────────────────────────────────────────────────────────
# Parsers for WhatsApp, Telegram, Discord, CSV
# ─────────────────────────────────────────────────────────────

def parse_whatsapp_text(content: str, target_sender: Optional[str] = None) -> List[str]:
    """Parses WhatsApp txt exports: [12/05/23, 14:30:15] Sender: Message"""
    messages = []
    # Pattern matching WhatsApp export formats
    pattern = re.compile(r"^(?:\[?\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s+[AP]M)?\]?\s+)?([^:]+):\s+(.+)$", re.MULTILINE)

    senders_found: Dict[str, int] = {}
    parsed_pairs = []

    for line in content.splitlines():
        match = pattern.match(line.strip())
        if match:
            sender = match.group(1).strip()
            msg = match.group(2).strip()
            if msg and not msg.startswith("<Media omitted>") and not msg.startswith("This message was deleted"):
                senders_found[sender] = senders_found.get(sender, 0) + 1
                parsed_pairs.append((sender, msg))

    if not parsed_pairs:
        return []

    # If no target sender specified, pick the sender with the most messages
    if not target_sender:
        target_sender = max(senders_found, key=senders_found.get)

    messages = [msg for sender, msg in parsed_pairs if sender.lower() == target_sender.lower()]
    return messages


def parse_telegram_json(data: dict, target_sender: Optional[str] = None) -> List[str]:
    """Parses Telegram official JSON export."""
    raw_msgs = data.get("messages", [])
    senders_found: Dict[str, int] = {}
    parsed_pairs = []

    for item in raw_msgs:
        if item.get("type") == "message":
            sender = item.get("from") or "Unknown"
            text_val = item.get("text", "")
            if isinstance(text_val, list):
                # Telegram represents entities as lists
                text_val = "".join([t.get("text", "") if isinstance(t, dict) else str(t) for t in text_val])
            text_str = str(text_val).strip()
            if text_str:
                senders_found[sender] = senders_found.get(sender, 0) + 1
                parsed_pairs.append((sender, text_str))

    if not parsed_pairs:
        return []

    if not target_sender:
        target_sender = max(senders_found, key=senders_found.get)

    return [msg for sender, msg in parsed_pairs if sender.lower() == target_sender.lower()]


def parse_discord_json(data: dict, target_sender: Optional[str] = None) -> List[str]:
    """Parses DiscordChatExporter JSON format."""
    raw_msgs = data.get("messages", [])
    senders_found: Dict[str, int] = {}
    parsed_pairs = []

    for item in raw_msgs:
        author = item.get("author", {})
        sender = author.get("name") or author.get("nickname") or "Unknown"
        content = item.get("content", "").strip()
        if content:
            senders_found[sender] = senders_found.get(sender, 0) + 1
            parsed_pairs.append((sender, content))

    if not parsed_pairs:
        return []

    if not target_sender:
        target_sender = max(senders_found, key=senders_found.get)

    return [msg for sender, msg in parsed_pairs if sender.lower() == target_sender.lower()]


def parse_generic_csv(csv_text: str, target_sender: Optional[str] = None) -> List[str]:
    """Parses generic CSV with columns like sender, message."""
    messages = []
    try:
        reader = csv.reader(io.StringIO(csv_text))
        header = next(reader, None)
        sender_col = 0
        msg_col = 1

        if header:
            header_lower = [h.lower() for h in header]
            if "message" in header_lower:
                msg_col = header_lower.index("message")
            if "sender" in header_lower:
                sender_col = header_lower.index("sender")
            elif "user" in header_lower:
                sender_col = header_lower.index("user")

        senders_found: Dict[str, int] = {}
        parsed_pairs = []

        for row in reader:
            if len(row) > max(sender_col, msg_col):
                sender = row[sender_col].strip()
                msg = row[msg_col].strip()
                if msg:
                    senders_found[sender] = senders_found.get(sender, 0) + 1
                    parsed_pairs.append((sender, msg))

        if not parsed_pairs:
            return []

        if not target_sender and senders_found:
            target_sender = max(senders_found, key=senders_found.get)

        return [msg for sender, msg in parsed_pairs if sender.lower() == (target_sender or "").lower()]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Privacy-First Metric Extraction
# ─────────────────────────────────────────────────────────────

STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "it", "that", "this", "i", "you", "me", "my", "we"}


def is_emoji(char: str) -> bool:
    """Detects Unicode emoji characters."""
    category = unicodedata.category(char)
    return category in ("So", "Symbol, Other") or ord(char) > 0x1F600


def extract_style_metrics(messages: List[str]) -> Dict[str, Any]:
    """
    Computes mathematical metrics across message corpus:
    - Type-Token Ratio (TTR)
    - Avg words & chars per message
    - Emoji usage rate
    - Lowercase start rate
    - Trailing ellipsis & exclamation frequency
    """
    if not messages:
        return {
            "total_messages": 0,
            "ttr": 0.5,
            "avg_words": 10.0,
            "emoji_rate": 0.0,
            "lowercase_rate": 0.5,
            "ellipsis_rate": 0.0,
            "exclamation_rate": 0.0,
            "top_words": [],
            "style_directive": "Maintain standard conversational style.",
        }

    total_msgs = len(messages)
    all_tokens = []
    total_chars = 0
    emoji_count = 0
    lowercase_starts = 0
    ellipsis_count = 0
    exclamation_count = 0

    for msg in messages:
        total_chars += len(msg)
        words = re.findall(r"\b\w+\b", msg.lower())
        all_tokens.extend(words)

        # Emoji count
        emoji_count += sum(1 for char in msg if is_emoji(char))

        # Capitalization habit
        if msg and msg[0].islower():
            lowercase_starts += 1

        # Punctuation style
        if "..." in msg or "…" in msg:
            ellipsis_count += 1
        if "!" in msg:
            exclamation_count += 1

    total_words = len(all_tokens)
    unique_words = len(set(all_tokens))
    ttr = round(unique_words / max(1, total_words), 3)
    avg_words = round(total_words / max(1, total_msgs), 1)

    emoji_rate = round(emoji_count / max(1, total_msgs), 2)
    lowercase_rate = round(lowercase_starts / max(1, total_msgs), 2)
    ellipsis_rate = round(ellipsis_count / max(1, total_msgs), 2)
    exclamation_rate = round(exclamation_count / max(1, total_msgs), 2)

    # Word frequencies excluding stopwords
    word_counts: Dict[str, int] = {}
    for w in all_tokens:
        if len(w) > 2 and w not in STOPWORDS:
            word_counts[w] = word_counts.get(w, 0) + 1

    top_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Synthesize Document-Grounded Style Directive
    vocab_desc = (
        "high vocabulary richness and diverse lexicon"
        if ttr > 0.6
        else "concise, direct vocabulary" if ttr < 0.35 else "balanced conversational vocabulary"
    )
    len_desc = (
        "short, punchy messaging responses (1–7 words)"
        if avg_words < 8
        else "detailed, descriptive messaging responses" if avg_words > 20 else "natural medium-length responses"
    )
    case_desc = (
        "Speak predominantly in lowercase text to mimic casual instant messaging."
        if lowercase_rate > 0.6
        else "Use standard proper sentence capitalization."
    )
    punct_desc = []
    if ellipsis_rate > 0.2:
        punct_desc.append("Use trailing ellipses (...) naturally.")
    if exclamation_rate > 0.3:
        punct_desc.append("Express enthusiasm using exclamation marks.")
    if emoji_rate > 0.4:
        punct_desc.append("Incorporate expressive emojis occasionally.")

    punct_str = " ".join(punct_desc)

    directive = (
        f"[PERSONA STYLE DIRECTIVE]\n"
        f"- Message Length & Pace: Adopt {len_desc}.\n"
        f"- Vocabulary Complexity: Utilize {vocab_desc} (TTR: {ttr}).\n"
        f"- Capitalization & Syntax: {case_desc}\n"
        f"- Punctuation & Micro-habits: {punct_str}\n"
        f"- Characteristic Lexicon Keywords: {', '.join([w[0] for w in top_words])}."
    ).strip()

    return {
        "total_messages": total_msgs,
        "ttr": ttr,
        "avg_words": avg_words,
        "emoji_rate": emoji_rate,
        "lowercase_rate": lowercase_rate,
        "ellipsis_rate": ellipsis_rate,
        "exclamation_rate": exclamation_rate,
        "top_words": [w[0] for w in top_words],
        "style_directive": directive,
    }


# ─────────────────────────────────────────────────────────────
# Distillation Entrypoint & Persistence
# ─────────────────────────────────────────────────────────────

def distill_chat_export(
    file_content: str,
    platform: str,
    persona_id: str,
    target_sender: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Processes chat export, extracts style metrics, embeds style directive into LanceDB.
    """
    platform_lower = platform.lower()

    if platform_lower == "whatsapp":
        messages = parse_whatsapp_text(file_content, target_sender)
    elif platform_lower == "telegram":
        try:
            data = json.loads(file_content)
            messages = parse_telegram_json(data, target_sender)
        except Exception:
            messages = parse_whatsapp_text(file_content, target_sender)
    elif platform_lower == "discord":
        try:
            data = json.loads(file_content)
            messages = parse_discord_json(data, target_sender)
        except Exception:
            messages = parse_whatsapp_text(file_content, target_sender)
    else:
        messages = parse_generic_csv(file_content, target_sender)

    if not messages:
        # Fallback if parser returned empty
        messages = [line.strip() for line in file_content.splitlines() if line.strip() and ":" in line][:100]

    metrics = extract_style_metrics(messages)
    style_directive = metrics["style_directive"]

    # Embed style directive into LanceDB
    model = get_embed_model()
    vec = model.encode(style_directive).tolist()

    table = get_style_db_table()
    row = {
        "persona_id": persona_id,
        "platform": platform,
        "ttr": float(metrics["ttr"]),
        "avg_words": float(metrics["avg_words"]),
        "emoji_rate": float(metrics["emoji_rate"]),
        "lowercase_rate": float(metrics["lowercase_rate"]),
        "style_directive": style_directive,
        "vector": vec,
        "created_at": datetime.now().isoformat() if hasattr(datetime, "now") else "",
    }

    # Remove existing style entry for persona_id if present
    try:
        table.delete(f"persona_id = '{persona_id}'")
    except Exception:
        pass

    table.add([row])

    return {
        "persona_id": persona_id,
        "platform": platform,
        "total_messages": metrics["total_messages"],
        "ttr": metrics["ttr"],
        "avg_words": metrics["avg_words"],
        "emoji_rate": metrics["emoji_rate"],
        "lowercase_rate": metrics["lowercase_rate"],
        "style_directive": style_directive,
    }


def get_persona_style_directive(persona_id: str) -> Optional[str]:
    """Retrieves distilled style directive for a given persona_id."""
    try:
        table = get_style_db_table()
        res = table.search().where(f"persona_id = '{persona_id}'").limit(1).to_list()
        if res and len(res) > 0:
            return res[0].get("style_directive")
    except Exception as e:
        print(f"[persona_distillation] Style retrieval note: {e}")
    return None
