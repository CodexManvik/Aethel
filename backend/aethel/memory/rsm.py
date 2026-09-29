"""Procedural memory (RSM, spec §6.3): app notes and skills as Markdown with
YAML frontmatter under ~/.aethel/knowledge. The files are the source of
truth and the user may edit them; vectors are a cache rebuilt on demand.

Ported from v1 knowledge_store.py: Laplace-smoothed outcome tracking,
reward-weighted retrieval, duplicate merging with reinforcement, and
auto-deprecation below a 34% success rate after 3 runs."""
import hashlib
import re
import threading
from datetime import date
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

Embed = Callable[[list[str]], np.ndarray]
STATUSES = ("quarantined", "approved", "deprecated")
DUPLICATE_SIM = 0.9
MIN_EFFECTIVE_CONFIDENCE = 0.3
DEPRECATE_MIN_RUNS = 3
DEPRECATE_RATE = 0.34
REINFORCE = 0.05
MAX_FACTS = 40
MAX_DURATIONS = 50
_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "untitled"


def _parse(path: Path) -> dict | None:
    try:
        m = _FRONT_RE.match(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(m.group(1)) if m else None
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(meta, dict):
        return None
    return {**meta, "body": m.group(2).strip(), "path": str(path)}


def _write(path: Path, meta: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump({k: v for k, v in meta.items() if k not in ("body", "path")}, sort_keys=False,
                           allow_unicode=True)
    path.write_text(f"---\n{front}---\n\n{body.strip()}\n", encoding="utf-8")


def _section(body: str, name: str) -> list[str]:
    m = re.search(rf"^##\s*{re.escape(name)}\s*\n(.*?)(?=^##\s|\Z)", body, re.DOTALL | re.MULTILINE | re.IGNORECASE)
    if not m:
        return []
    return [re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line).strip()
            for line in m.group(1).splitlines() if line.strip()]


def _skill_body(steps: list[str], pitfalls: list[str]) -> str:
    out = "## Steps\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    if pitfalls:
        out += "\n\n## Pitfalls\n" + "\n".join(f"- {p}" for p in pitfalls)
    return out


def _normalise_skill(doc: dict) -> dict:
    """Fill spec §6.3 fields, reading v1 files (times_succeeded, ...) tolerantly."""
    succ = int(doc.get("successes", doc.get("times_succeeded", 0)) or 0)
    runs = int(doc.get("runs", succ + int(doc.get("times_failed", 0) or 0)) or 0)
    title = str(doc.get("title") or doc.get("intent") or doc.get("id"))
    when = _section(doc["body"], "When to use")
    return {**doc, "type": "skill", "title": title, "intent": str(doc.get("intent") or (when[0] if when else title)),
            "apps": list(doc.get("apps") or []), "params": dict(doc.get("params") or {}),
            "status": doc.get("status") if doc.get("status") in STATUSES else "quarantined",
            "runs": runs, "successes": succ, "duration_history": list(doc.get("duration_history") or []),
            "avg_duration_s": doc.get("avg_duration_s"), "confidence": float(doc.get("confidence", 0.5) or 0.5),
            "last_used": doc.get("last_used"), "macro": doc.get("macro") or "none",
            "steps": _section(doc["body"], "Steps"), "pitfalls": _section(doc["body"], "Pitfalls")}


def success_rate(doc: dict) -> float:
    return (doc["successes"] + 1) / (doc["runs"] + 2)  # Laplace-smoothed, 0.5 prior


def effective_confidence(doc: dict) -> float:
    """Reinforcement (how often the lesson was rediscovered) weighted by how well it worked."""
    return doc["confidence"] * (0.5 + success_rate(doc))


class KnowledgeStore:
    def __init__(self, root: Path, embed: Embed):
        self.root = root
        self.embed = embed
        self._lock = threading.RLock()
        self._vectors: dict[str, np.ndarray] = {}  # sha1(text) -> vector

    # ---- reading ----------------------------------------------------------------
    def skills(self, status: str | None = None) -> list[dict]:
        docs = [_normalise_skill(d) for d in (_parse(p) for p in sorted((self.root / "skills").glob("*.md")))
                if d is not None]
        return [d for d in docs if status is None or d["status"] == status]

    def notes(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "apps").glob("*.md")):
            d = _parse(p)
            if d is not None:
                out.append({**d, "type": "app_note", "app": str(d.get("app") or p.stem),
                            "facts": [ln[2:].strip() for ln in d["body"].splitlines() if ln.startswith("- ")]})
        return out

    def get(self, doc_id: str) -> dict | None:
        return next((d for d in self.skills() + self.notes() if d.get("id") == doc_id), None)

    # ---- retrieval ----------------------------------------------------------------
    def _vec(self, texts: list[str]) -> np.ndarray:
        keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
        missing = [t for t, k in zip(texts, keys) if k not in self._vectors]
        if missing:
            for t, v in zip(missing, self.embed(missing)):
                self._vectors[hashlib.sha1(t.encode("utf-8")).hexdigest()] = v
        return np.stack([self._vectors[k] for k in keys])

    @staticmethod
    def skill_text(doc: dict) -> str:
        return "\n".join([doc["title"], doc["intent"], " ".join(doc["apps"]), *doc["steps"]])

    @staticmethod
    def note_text(doc: dict) -> str:
        return "\n".join([doc["app"], *doc["facts"]])

    def _rank(self, query: str, docs: list[dict], text: Callable[[dict], str]) -> list[tuple[float, dict]]:
        if not docs:
            return []
        sims = self._vec([text(d) for d in docs]) @ self._vec([query])[0]
        return sorted(zip(map(float, sims), docs), key=lambda t: t[0], reverse=True)

    def retrieve_skills(self, query: str, k: int = 5) -> list[dict]:
        """Approved skills by similarity × effective confidence (v1's reward-weighted retrieval)."""
        live = [d for d in self.skills("approved") if effective_confidence(d) >= MIN_EFFECTIVE_CONFIDENCE]
        scored = [(sim * effective_confidence(d), d) for sim, d in self._rank(query, live, self.skill_text)]
        return [d for _, d in sorted(scored, key=lambda t: t[0], reverse=True)[:k]]

    # bge puts unrelated text near 0.5, so a note must clear 0.6 to count as relevant.
    def retrieve_notes(self, query: str, k: int = 3, min_sim: float = 0.6) -> list[dict]:
        return [d for sim, d in self._rank(query, self.notes(), self.note_text)[:k] if sim >= min_sim]

    # ---- writing ----------------------------------------------------------------
    def upsert_skill(self, fields: dict, status: str) -> tuple[dict, bool]:
        """(skill, created). A near-duplicate of an existing skill reinforces it and
        merges its pitfalls instead of adding a second copy (v1 find_duplicate)."""
        steps = [str(s) for s in fields.get("steps") or []]
        pitfalls = [str(p) for p in fields.get("pitfalls") or []]
        draft = {"title": str(fields["title"]), "intent": str(fields.get("intent") or fields["title"]),
                 "apps": [str(a).lower() for a in fields.get("apps") or []], "steps": steps}
        with self._lock:
            candidates = [d for d in self.skills() if d["status"] != "deprecated"]
            ranked = self._rank(self.skill_text(draft), candidates, self.skill_text)
            if ranked and ranked[0][0] >= DUPLICATE_SIM:
                doc = ranked[0][1]
                merged = doc["pitfalls"] + [p for p in pitfalls if p not in doc["pitfalls"]]
                self._save_skill({**doc, "confidence": min(1.0, doc["confidence"] + REINFORCE),
                                  "apps": sorted(set(doc["apps"]) | set(draft["apps"]))}, doc["steps"], merged)
                return self.get(doc["id"]), False
            doc_id = base = f"skill-{slug(draft['title'])}"
            n = 2
            while (self.root / "skills" / f"{doc_id}.md").exists():
                doc_id, n = f"{base}-{n}", n + 1
            meta = {"id": doc_id, "type": "skill", "title": draft["title"], "apps": draft["apps"],
                    "intent": draft["intent"], "params": dict(fields.get("params") or {}),
                    "preconditions": list(fields.get("preconditions") or []), "status": status, "runs": 0,
                    "successes": 0, "avg_duration_s": None, "duration_history": [], "confidence": 0.5,
                    "last_used": None, "macro": "none", "created": date.today().isoformat()}
            _write(self.root / "skills" / f"{doc_id}.md", meta, _skill_body(steps, pitfalls))
            return self.get(doc_id), True

    def _save_skill(self, doc: dict, steps: list[str], pitfalls: list[str]) -> None:
        keep = ("id", "type", "title", "apps", "intent", "params", "preconditions", "status", "runs", "successes",
                "avg_duration_s", "duration_history", "confidence", "last_used", "macro", "created")
        _write(Path(doc["path"]), {k: doc[k] for k in keep if k in doc}, _skill_body(steps, pitfalls))

    def upsert_note(self, app: str, facts: list[str]) -> dict:
        """Add facts to an app's note. Notes are observations, so they're always live."""
        app = str(app).strip().lower()
        path = self.root / "apps" / f"{slug(app)}.md"
        with self._lock:
            existing = next((d for d in self.notes() if d["path"] == str(path)), None)
            current = existing["facts"] if existing else []
            seen = {f.lower().rstrip(".") for f in current}
            for f in facts:
                f = str(f).strip()
                if f and f.lower().rstrip(".") not in seen:
                    current.append(f)
                    seen.add(f.lower().rstrip("."))
            current = current[-MAX_FACTS:]
            meta = {"id": f"app-{slug(app)}", "type": "app_note", "app": app, "updated": date.today().isoformat()}
            _write(path, meta, "\n".join(f"- {f}" for f in current))
        return next(d for d in self.notes() if d["path"] == str(path))

    def save_note_body(self, app: str, body: str) -> dict:
        """The user edited the note by hand."""
        path = self.root / "apps" / f"{slug(app)}.md"
        with self._lock:
            meta = {"id": f"app-{slug(app)}", "type": "app_note", "app": str(app).lower(),
                    "updated": date.today().isoformat()}
            _write(path, meta, body)
        return next(d for d in self.notes() if d["path"] == str(path))

    def set_status(self, doc_id: str, status: str) -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self._lock:
            doc = next((d for d in self.skills() if d["id"] == doc_id), None)
            if doc is None:
                return None
            self._save_skill({**doc, "status": status}, doc["steps"], doc["pitfalls"])
        return self.get(doc_id)

    def record_outcome(self, doc_ids: list[str], success: bool, duration_s: float | None) -> None:
        """Credit or debit the skills a task used, by its verified outcome (spec §6.3 step 2)."""
        with self._lock:
            for doc in self.skills():
                if doc["id"] not in doc_ids:
                    continue
                runs, succ = doc["runs"] + 1, doc["successes"] + (1 if success else 0)
                history = doc["duration_history"]
                if duration_s is not None:
                    history = (history + [round(float(duration_s), 1)])[-MAX_DURATIONS:]
                status = doc["status"]
                if status == "approved" and runs >= DEPRECATE_MIN_RUNS and (succ + 1) / (runs + 2) < DEPRECATE_RATE:
                    status = "deprecated"
                recent = history[-5:]
                self._save_skill({**doc, "runs": runs, "successes": succ, "duration_history": history,
                                  "avg_duration_s": round(sum(recent) / len(recent), 1) if recent else None,
                                  "last_used": date.today().isoformat(), "status": status},
                                 doc["steps"], doc["pitfalls"])
