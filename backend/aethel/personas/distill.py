"""Style import: a chat export (WhatsApp .txt, Telegram or Discord .json, CSV) → one speaker's messages → style
metrics → a short directive in plain words. Ported from backend/persona_distillation.py without lancedb or
sentence-transformers: the directive is saved to the persona's style.md and the lines go to its own corpus."""
import csv
import io
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

MAX_LINES = 20_000
MAX_LINE_CHARS = 1_000
SLANG = ("lol", "lmao", "lmfao", "rofl", "haha", "hahaha", "hehe", "omg", "idk", "tbh", "ngl", "fr", "rn", "imo",
         "btw", "brb", "ikr", "smh", "bruh", "nah", "yeah", "yep", "ya", "u", "ur", "pls", "plz", "thx", "ty", "k",
         "kk", "wtf", "gonna", "wanna", "gotta", "kinda", "sorta", "lowkey", "highkey", "deadass", "bestie")
SKIP = ("<media omitted>", "this message was deleted", "you deleted this message", "<attached:", "image omitted",
        "sticker omitted", "missed voice call", "missed video call")
_WHATSAPP = re.compile(
    r"^‎?\[?(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap]\.?[Mm]\.?)?)\]?"
    r"\s*(?:-\s*)?([^:]{1,60}):\s(.*)$")


@dataclass
class Line:
    speaker: str
    text: str
    ts: str | None = None


class ExportError(ValueError):
    """The file isn't an export this understands."""


def _keep(text: str) -> bool:
    low = text.strip().lower()
    return bool(low) and not any(low.startswith(s) or s in low[:40] for s in SKIP)


def parse_whatsapp(content: str) -> list[Line]:
    out: list[Line] = []
    for raw in content.splitlines():
        m = _WHATSAPP.match(raw.strip())
        if m:
            out.append(Line(m.group(2).strip(), m.group(3).strip(), m.group(1)))
        elif out and raw.strip() and not raw.startswith("‎") and len(out[-1].text) < MAX_LINE_CHARS:
            out[-1].text += "\n" + raw.strip()  # a message that goes on for several lines (capped as it grows)
    return [l for l in out if _keep(l.text)]


def _telegram_text(value) -> str:
    if isinstance(value, list):  # Telegram splits formatted text into entities
        return "".join(t.get("text", "") if isinstance(t, dict) else str(t) for t in value)
    return str(value or "")


def parse_telegram(data: dict) -> list[Line]:
    out = []
    for item in data.get("messages", []):
        if isinstance(item, dict) and item.get("type") == "message":
            text = _telegram_text(item.get("text")).strip()
            if text:
                out.append(Line(str(item.get("from") or "Unknown"), text, item.get("date")))
    return out


def parse_discord(data: dict) -> list[Line]:
    out = []
    for item in data.get("messages", []):
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        text = str(item.get("content") or "").strip()
        if text:
            out.append(Line(str(author.get("nickname") or author.get("name") or "Unknown"), text, item.get("timestamp")))
    return out


def parse_csv(content: str) -> list[Line]:
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    named = any(h in head for h in ("message", "text", "content"))
    msg_col = next((head.index(h) for h in ("message", "text", "content") if h in head), 1)
    who_col = next((head.index(h) for h in ("sender", "author", "from", "user", "name") if h in head), 0)
    ts_col = next((head.index(h) for h in ("timestamp", "date", "time") if h in head), None)
    out = []
    for row in rows[1 if named else 0:]:
        if len(row) > max(msg_col, who_col) and row[msg_col].strip():
            ts = row[ts_col] if ts_col is not None and len(row) > ts_col else None
            out.append(Line(row[who_col].strip() or "Unknown", row[msg_col].strip(), ts))
    return [l for l in out if _keep(l.text)]


def parse_export(content: str, filename: str = "") -> list[Line]:
    """Picks the parser from the file (JSON → Telegram or Discord; a .csv → CSV; else WhatsApp's text format)."""
    stripped = content.lstrip("﻿").strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError as exc:
            raise ExportError("That JSON file couldn't be read.") from exc
        msgs = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(msgs, list):
            raise ExportError("That JSON file isn't a Telegram or Discord export.")
        is_discord = any(isinstance(m, dict) and isinstance(m.get("author"), dict) for m in msgs[:50])
        lines = parse_discord(data) if is_discord else parse_telegram(data)
    elif filename.lower().endswith(".csv"):
        try:
            lines = parse_csv(stripped)
        except csv.Error as exc:
            raise ExportError("That CSV file couldn't be read.") from exc
    else:
        lines = parse_whatsapp(stripped)
    if not lines:
        raise ExportError("No messages were found in that file.")
    return [Line(l.speaker[:60], l.text[:MAX_LINE_CHARS], l.ts) for l in lines[-MAX_LINES:]]


def speakers(lines: list[Line]) -> list[tuple[str, int]]:
    return Counter(l.speaker for l in lines).most_common()


def of_speaker(lines: list[Line], speaker: str) -> list[Line]:
    want = speaker.strip().lower()
    return [l for l in lines if l.speaker.lower() == want]


def _is_emoji(ch: str) -> bool:
    return unicodedata.category(ch) == "So" or 0x1F300 <= ord(ch) <= 0x1FAFF


@dataclass
class StyleMetrics:
    messages: int = 0
    avg_words: float = 0.0
    burst: float = 1.0                 # messages in a row, on average, before someone else speaks
    lowercase_rate: float = 0.0        # messages with no capital letters at all
    question_rate: float = 0.0
    exclamation_rate: float = 0.0
    ellipsis_rate: float = 0.0
    emoji_rate: float = 0.0            # emoji per message
    top_emoji: list[str] = field(default_factory=list)
    slang: list[str] = field(default_factory=list)
    openers: list[str] = field(default_factory=list)
    closers: list[str] = field(default_factory=list)


def extract_style_metrics(lines: list[Line], speaker: str) -> StyleMetrics:
    """`lines` is the whole export (for the bursts), `speaker` whose style it is."""
    mine = [l.text for l in of_speaker(lines, speaker)]
    if not mine:
        return StyleMetrics()
    n = len(mine)
    words = [re.findall(r"[\w']+", t.lower()) for t in mine]
    runs, current = [], 0
    for l in lines:
        if l.speaker.lower() == speaker.strip().lower():
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    emoji = Counter(ch for t in mine for ch in t if _is_emoji(ch))
    used = Counter(w for ws in words for w in set(ws))
    slang = [s for s in SLANG if used[s] >= max(2, n * 0.03)]
    slang.sort(key=lambda s: -used[s])
    first = Counter(ws[0] for ws in words if ws)
    last = Counter(ws[-1] for ws in words if len(ws) > 1)
    habit = max(3, n * 0.05)
    return StyleMetrics(
        messages=n,
        avg_words=round(sum(map(len, words)) / n, 1),
        burst=round(sum(runs) / max(1, len(runs)), 1),
        lowercase_rate=round(sum(1 for t in mine if not any(c.isupper() for c in t)) / n, 2),
        question_rate=round(sum(1 for t in mine if "?" in t) / n, 2),
        exclamation_rate=round(sum(1 for t in mine if "!" in t) / n, 2),
        ellipsis_rate=round(sum(1 for t in mine if "..." in t or "…" in t) / n, 2),
        emoji_rate=round(sum(emoji.values()) / n, 2),
        top_emoji=[e for e, _ in emoji.most_common(3)],
        slang=slang[:5],
        openers=[w for w, c in first.most_common(3) if c >= habit],
        closers=[w for w, c in last.most_common(3) if c >= habit],
    )


def _quoted(items: list[str]) -> str:
    q = [f"'{i}'" for i in items]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


def directive(m: StyleMetrics, name: str) -> str:
    """A few plain sentences: "Writes in short lowercase bursts, often 2–3 messages in a row; uses 'lol' and '😭';
    rarely asks questions back." Empty when there's nothing to go on."""
    if m.messages == 0:
        return ""
    length = ("very short" if m.avg_words < 5 else "short" if m.avg_words < 10
              else "medium-length" if m.avg_words < 22 else "long")
    case = " lowercase" if m.lowercase_rate >= 0.6 else ""
    first = f"{name} writes in {length}{case} messages (about {m.avg_words:g} words)"
    if m.burst >= 1.6:
        first += f", often {round(m.burst)}–{round(m.burst) + 1} messages in a row"
    parts = [first]
    habits = m.slang + m.top_emoji if m.emoji_rate >= 0.15 else list(m.slang)
    if habits:
        parts.append(f"uses {_quoted(habits)}")
    elif m.emoji_rate < 0.05:
        parts.append("rarely uses emoji")
    if m.question_rate < 0.12:
        parts.append("rarely asks questions back")
    elif m.question_rate > 0.35:
        parts.append("asks a lot of questions")
    punct = []
    if m.exclamation_rate > 0.3:
        punct.append("exclamation marks")
    if m.ellipsis_rate > 0.15:
        punct.append("trailing ellipses…")
    if punct:
        parts.append("likes " + " and ".join(punct))
    if m.openers:
        parts.append(f"often starts with {_quoted(m.openers)}")
    if m.closers:
        parts.append(f"often ends with {_quoted(m.closers)}")
    return "; ".join(parts) + "."
