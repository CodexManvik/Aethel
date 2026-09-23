"""
knowledge_store.py

RSM (Reflective Skill Memory) — the file-based knowledge store.

Design principle: **markdown files on disk are the single source of truth.**
The vector index is a rebuildable in-memory cache, never authoritative. A
corrupted index is a rebuild, not data loss; the user can open, edit, delete,
or git-version any piece of learned knowledge as a plain `.md` file.

Two document types share one storage philosophy:
  - rule  : a small declarative behavioral instruction (was the old DGBA rule).
  - skill : a procedural how-to the agent writes after completing a task and
            retrieves before attempting a similar one (Voyager-style).

Layout:
  ~/.aethel/knowledge/rules/<id>.md
  ~/.aethel/knowledge/skills/<id>.md

Each file is YAML frontmatter + a markdown body. Frontmatter carries the
lifecycle status (quarantined → approved → deprecated) and outcome statistics
(times_retrieved / times_succeeded / times_failed) that drive reward-weighted
retrieval and automatic deprecation.

The embedding model is loaded lazily so the module imports cleanly in
environments without the model downloaded (e.g. unit tests, which inject a
fake encoder via `set_encoder_for_testing`).
"""

from __future__ import annotations

import os
import re
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import yaml

# ─────────────────────────────────────────────────────────────
# Paths & constants
# ─────────────────────────────────────────────────────────────

KNOWLEDGE_DIR = Path(os.getenv("RSM_KNOWLEDGE_DIR", str(Path.home() / ".aethel" / "knowledge")))
RULES_DIR = KNOWLEDGE_DIR / "rules"
SKILLS_DIR = KNOWLEDGE_DIR / "skills"

TYPE_RULE = "rule"
TYPE_SKILL = "skill"

STATUS_QUARANTINED = "quarantined"
STATUS_APPROVED = "approved"
STATUS_DEPRECATED = "deprecated"

# Reward-weighted retrieval / auto-deprecation tuning.
_MIN_CONFIDENCE_FLOOR = 0.3      # rules/skills below this are not retrieved
_AUTO_DEPRECATE_MIN_TRIALS = 3   # need at least this many uses before pruning
_AUTO_DEPRECATE_RATE = 0.34      # deprecate if success-rate drops below this

_lock = threading.RLock()

# ─────────────────────────────────────────────────────────────
# Embedding (lazy — tests inject a fake via set_encoder_for_testing)
# ─────────────────────────────────────────────────────────────

_encoder: Optional[Callable[[str], np.ndarray]] = None
_embedding_dim: Optional[int] = None


def set_encoder_for_testing(fn: Callable[[str], np.ndarray], dim: int) -> None:
    """Injects a deterministic fake encoder so tests run without the model."""
    global _encoder, _embedding_dim
    _encoder = fn
    _embedding_dim = dim
    rebuild_index()


def _get_encoder() -> Callable[[str], np.ndarray]:
    global _encoder, _embedding_dim
    if _encoder is not None:
        return _encoder

    # Delegate to the shared backend-wide embedding singleton so this module
    # never loads a private copy of the model.
    import embeddings as shared_embeddings

    def _encode(text: str) -> np.ndarray:
        return shared_embeddings.encode(text, normalize=True)

    _embedding_dim = None  # resolved lazily by the shared module
    _encoder = _encode
    return _encoder


def _embed(text: str) -> np.ndarray:
    vec = _get_encoder()(text)
    vec = np.asarray(vec, dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


# ─────────────────────────────────────────────────────────────
# In-memory index (rebuildable cache)
# ─────────────────────────────────────────────────────────────

# Parallel arrays: _index_ids[i] ↔ _index_matrix[i]
_index_ids: list[str] = []
_index_matrix: Optional[np.ndarray] = None
_index_built = False


def _dir_for_type(doc_type: str) -> Path:
    return RULES_DIR if doc_type == TYPE_RULE else SKILLS_DIR


def _ensure_dirs() -> None:
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def rebuild_index() -> None:
    """
    Rebuilds the in-memory vector index by scanning every markdown file.
    Only approved documents are indexed for retrieval; quarantined and
    deprecated docs are searchable via the read APIs but never injected.
    """
    global _index_ids, _index_matrix, _index_built
    with _lock:
        _ensure_dirs()
        ids: list[str] = []
        vectors: list[np.ndarray] = []
        for doc in _iter_all_docs():
            if doc["status"] != STATUS_APPROVED:
                continue
            ids.append(doc["id"])
            vectors.append(_embed(doc["_embed_text"]))
        _index_ids = ids
        _index_matrix = np.vstack(vectors) if vectors else None
        _index_built = True


def _ensure_index() -> None:
    if not _index_built:
        rebuild_index()


# ─────────────────────────────────────────────────────────────
# Frontmatter (de)serialization
# ─────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(meta: dict, body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return f"---\n{fm}---\n\n{body.strip()}\n"


def _parse_file(path: Path) -> Optional[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    body = m.group(2).strip()
    return _hydrate(meta, body)


def _hydrate(meta: dict, body: str) -> dict:
    """Normalizes a parsed record into the dict shape the rest of the app uses."""
    doc = dict(meta)
    doc["body"] = _extract_section(body, "body_default", fallback=body)
    doc["_raw_body"] = body

    # Text used for embedding: body + rationale/preconditions so retrieval
    # captures both the instruction and its reasoning.
    embed_parts = [str(meta.get("title", "")), body]
    doc["_embed_text"] = "\n\n".join(p for p in embed_parts if p).strip() or body

    # Ensure outcome fields exist.
    doc.setdefault("times_retrieved", 0)
    doc.setdefault("times_succeeded", 0)
    doc.setdefault("times_failed", 0)

    # Expose the structured sections as flat fields for the admin UI /
    # legacy rule_store API.
    if meta.get("type") == TYPE_RULE:
        doc["rationale"] = _grab(body, "Rationale")
        doc["examples_before"] = _grab(body, "Example — Before")
        doc["examples_after"] = _grab(body, "Example — After")
    return doc


def _extract_section(body: str, _key: str, fallback: str) -> str:
    # Rules store their instruction as the first paragraph after the "# Rule"
    # heading (if present); otherwise the whole body is the instruction.
    m = re.search(r"#\s*Rule\s*\n+(.+?)(?:\n#|\Z)", body, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return fallback.strip()


def _iter_all_docs():
    _ensure_dirs()
    for d in (RULES_DIR, SKILLS_DIR):
        for path in sorted(d.glob("*.md")):
            doc = _parse_file(path)
            if doc:
                doc["_path"] = str(path)
                yield doc


def _path_for(doc_id: str, doc_type: str) -> Path:
    return _dir_for_type(doc_type) / f"{doc_id}.md"


def _find_path(doc_id: str) -> Optional[Path]:
    for d in (RULES_DIR, SKILLS_DIR):
        p = d / f"{doc_id}.md"
        if p.exists():
            return p
    return None


# ─────────────────────────────────────────────────────────────
# Body builders
# ─────────────────────────────────────────────────────────────

def _build_rule_body(body: str, rationale: str, ex_before: str, ex_after: str) -> str:
    parts = [f"# Rule\n\n{body.strip()}"]
    if rationale.strip():
        parts.append(f"## Rationale\n\n{rationale.strip()}")
    if ex_before.strip():
        parts.append(f"## Example — Before\n\n{ex_before.strip()}")
    if ex_after.strip():
        parts.append(f"## Example — After\n\n{ex_after.strip()}")
    return "\n\n".join(parts)


def _build_skill_body(
    title: str, when_to_use: str, steps: str, tools: str, failure_modes: str
) -> str:
    parts = [f"# {title.strip()}"]
    if when_to_use.strip():
        parts.append(f"## When to use\n\n{when_to_use.strip()}")
    if steps.strip():
        parts.append(f"## Steps\n\n{steps.strip()}")
    if tools.strip():
        parts.append(f"## Tools used\n\n{tools.strip()}")
    if failure_modes.strip():
        parts.append(f"## Known failure modes\n\n{failure_modes.strip()}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Write operations
# ─────────────────────────────────────────────────────────────

def insert_rule(
    category: str,
    confidence: float,
    scope: str,
    body: str,
    rationale: str,
    examples_before: str,
    examples_after: str,
    source_interaction_id: str,
    status: str = STATUS_QUARANTINED,
) -> str:
    """Creates a rule .md file (quarantined by default). Returns its id."""
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    meta = {
        "id": rule_id,
        "type": TYPE_RULE,
        "category": category,
        "status": status,
        "confidence": float(confidence),
        "scope": scope,
        "source_interaction_id": source_interaction_id,
        "created_at": _now(),
        "approved_at": "",
        "times_retrieved": 0,
        "times_succeeded": 0,
        "times_failed": 0,
    }
    md_body = _build_rule_body(body, rationale, examples_before, examples_after)
    with _lock:
        _ensure_dirs()
        _path_for(rule_id, TYPE_RULE).write_text(_serialize(meta, md_body), encoding="utf-8")
    if status == STATUS_APPROVED:
        rebuild_index()
    return rule_id


def insert_skill(
    title: str,
    category: str,
    confidence: float,
    scope: str,
    when_to_use: str,
    steps: str,
    tools_used: str,
    failure_modes: str,
    source_interaction_id: str,
    status: str = STATUS_QUARANTINED,
) -> str:
    """Creates a skill .md file (quarantined by default). Returns its id."""
    skill_id = f"skill_{uuid.uuid4().hex[:12]}"
    meta = {
        "id": skill_id,
        "type": TYPE_SKILL,
        "title": title,
        "category": category,
        "status": status,
        "confidence": float(confidence),
        "scope": scope,
        "source_interaction_id": source_interaction_id,
        "created_at": _now(),
        "approved_at": "",
        "times_retrieved": 0,
        "times_succeeded": 0,
        "times_failed": 0,
    }
    md_body = _build_skill_body(title, when_to_use, steps, tools_used, failure_modes)
    with _lock:
        _ensure_dirs()
        _path_for(skill_id, TYPE_SKILL).write_text(_serialize(meta, md_body), encoding="utf-8")
    if status == STATUS_APPROVED:
        rebuild_index()
    return skill_id


def _update_meta(doc_id: str, changes: dict) -> bool:
    with _lock:
        path = _find_path(doc_id)
        if not path:
            return False
        doc = _parse_file(path)
        if not doc:
            return False
        meta = {k: v for k, v in doc.items() if not k.startswith("_") and k != "body"}
        meta.update(changes)
        path.write_text(_serialize(meta, doc["_raw_body"]), encoding="utf-8")
    return True


def approve_doc(doc_id: str) -> bool:
    ok = _update_meta(doc_id, {"status": STATUS_APPROVED, "approved_at": _now()})
    if ok:
        rebuild_index()
    return ok


def reject_doc(doc_id: str) -> bool:
    ok = _update_meta(doc_id, {"status": STATUS_DEPRECATED})
    if ok:
        rebuild_index()
    return ok


def update_rule_content(doc_id: str, body: str, rationale: str) -> bool:
    """Edits a rule's instruction + rationale, preserving examples & metadata."""
    with _lock:
        path = _find_path(doc_id)
        if not path:
            return False
        doc = _parse_file(path)
        if not doc or doc.get("type") != TYPE_RULE:
            return False
        # Preserve existing examples if present.
        ex_before = _grab(doc["_raw_body"], "Example — Before")
        ex_after = _grab(doc["_raw_body"], "Example — After")
        meta = {k: v for k, v in doc.items() if not k.startswith("_") and k != "body"}
        new_body = _build_rule_body(body, rationale, ex_before, ex_after)
        path.write_text(_serialize(meta, new_body), encoding="utf-8")
    if doc.get("status") == STATUS_APPROVED:
        rebuild_index()
    return True


def _grab(body: str, section: str) -> str:
    m = re.search(rf"##\s*{re.escape(section)}\s*\n+(.+?)(?:\n##|\Z)", body, re.DOTALL)
    return m.group(1).strip() if m else ""


def record_outcome(doc_ids: list[str], success: bool) -> None:
    """
    Attributes a turn outcome to the docs that were retrieved for it.
    Increments success/failure counters and auto-deprecates docs whose
    success-rate falls below threshold after enough trials.
    """
    changed = False
    for doc_id in doc_ids:
        with _lock:
            path = _find_path(doc_id)
            if not path:
                continue
            doc = _parse_file(path)
            if not doc:
                continue
            succ = int(doc.get("times_succeeded", 0)) + (1 if success else 0)
            fail = int(doc.get("times_failed", 0)) + (0 if success else 1)
            trials = succ + fail
            meta = {k: v for k, v in doc.items() if not k.startswith("_") and k != "body"}
            meta["times_succeeded"] = succ
            meta["times_failed"] = fail

            # Auto-deprecate proven-bad knowledge.
            rate = (succ + 1) / (trials + 2)  # Laplace-smoothed
            if (
                doc.get("status") == STATUS_APPROVED
                and trials >= _AUTO_DEPRECATE_MIN_TRIALS
                and rate < _AUTO_DEPRECATE_RATE
            ):
                meta["status"] = STATUS_DEPRECATED
                changed = True
            path.write_text(_serialize(meta, doc["_raw_body"]), encoding="utf-8")
    if changed:
        rebuild_index()


def _bump_retrieved(doc_ids: list[str]) -> None:
    for doc_id in doc_ids:
        with _lock:
            path = _find_path(doc_id)
            if not path:
                continue
            doc = _parse_file(path)
            if not doc:
                continue
            meta = {k: v for k, v in doc.items() if not k.startswith("_") and k != "body"}
            meta["times_retrieved"] = int(doc.get("times_retrieved", 0)) + 1
            path.write_text(_serialize(meta, doc["_raw_body"]), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# Read / retrieval operations
# ─────────────────────────────────────────────────────────────

def _effective_confidence(doc: dict) -> float:
    """
    Combines the declared confidence with the observed success-rate so
    proven knowledge outranks unproven, and repeatedly-failing knowledge
    sinks. This is the reward-driven curation signal.
    """
    base = float(doc.get("confidence", 0.5))
    succ = int(doc.get("times_succeeded", 0))
    fail = int(doc.get("times_failed", 0))
    success_rate = (succ + 1) / (succ + fail + 2)  # Laplace-smoothed, 0.5 prior
    return base * (0.5 + success_rate)  # range ≈ [0, 1.5] * base


def retrieve(query: str, doc_type: Optional[str] = None, top_k: int = 5) -> list[dict]:
    """
    Reward-weighted semantic retrieval over approved docs.
    Final score = cosine_similarity × effective_confidence.
    Optionally filter by doc_type ('rule' | 'skill').
    Bumps times_retrieved on the returned docs.
    """
    _ensure_index()
    with _lock:
        if _index_matrix is None or not _index_ids:
            return []
        qv = _embed(query)
        sims = _index_matrix @ qv  # cosine (all vectors normalized)
        id_to_sim = {_index_ids[i]: float(sims[i]) for i in range(len(_index_ids))}

    scored: list[tuple[float, dict]] = []
    for doc in _iter_all_docs():
        if doc["status"] != STATUS_APPROVED:
            continue
        if doc_type and doc.get("type") != doc_type:
            continue
        sim = id_to_sim.get(doc["id"])
        if sim is None:
            continue
        eff = _effective_confidence(doc)
        if eff < _MIN_CONFIDENCE_FLOOR:
            continue
        scored.append((sim * eff, doc))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = [doc for _, doc in scored[:top_k]]
    _bump_retrieved([d["id"] for d in top])
    return top


def skill_embed_text(
    title: str, when_to_use: str, steps: str,
    tools_used: str = "", failure_modes: str = "",
) -> str:
    """
    Builds the exact text a skill would be embedded as, so duplicate checks
    compare like with like (the stored embedding includes the markdown
    structure, not just the raw fields).
    """
    body = _build_skill_body(title, when_to_use, steps, tools_used, failure_modes)
    return "\n\n".join(p for p in [title, body] if p).strip()


def rule_embed_text(
    body: str, rationale: str, ex_before: str = "", ex_after: str = "",
) -> str:
    """Canonical embed text for a rule (mirrors how rules are stored)."""
    return _build_rule_body(body, rationale, ex_before, ex_after).strip()


def find_duplicate(
    text: str,
    doc_type: Optional[str] = None,
    threshold: float = 0.90,
) -> Optional[dict]:
    """
    Finds an existing document that is a near-duplicate of `text`.

    Unlike retrieve(), this searches every status (including quarantined), so
    the reflection passes don't keep re-writing the same skill/rule each time a
    similar task occurs — which would bloat the library and dilute retrieval.

    Returns the most similar doc above `threshold`, or None.
    """
    query_vec = _embed(text)
    best: Optional[dict] = None
    best_sim = threshold
    for doc in _iter_all_docs():
        if doc_type and doc.get("type") != doc_type:
            continue
        if doc.get("status") == STATUS_DEPRECATED:
            continue  # a rejected doc shouldn't block writing a better one
        sim = float(np.dot(query_vec, _embed(doc["_embed_text"])))
        if sim >= best_sim:
            best, best_sim = doc, sim
    if best is not None:
        best = dict(best)
        best["_similarity"] = round(best_sim, 4)
    return best


def reinforce(doc_id: str, amount: float = 0.05) -> bool:
    """
    Nudges a document's confidence upward (capped at 1.0) when the same lesson
    is independently rediscovered — evidence it generalizes.
    """
    doc = get_by_id(doc_id)
    if not doc:
        return False
    new_conf = min(1.0, float(doc.get("confidence", 0.5)) + amount)
    ok = _update_meta(doc_id, {"confidence": new_conf})
    if ok and doc.get("status") == STATUS_APPROVED:
        rebuild_index()
    return ok


def get_by_status(status: Optional[str] = None, doc_type: Optional[str] = None) -> list[dict]:
    out = []
    for doc in _iter_all_docs():
        if status and doc["status"] != status:
            continue
        if doc_type and doc.get("type") != doc_type:
            continue
        out.append(doc)
    return out


def get_by_id(doc_id: str) -> Optional[dict]:
    path = _find_path(doc_id)
    return _parse_file(path) if path else None


def count_by_status(doc_type: Optional[str] = None) -> dict[str, int]:
    counts = {STATUS_QUARANTINED: 0, STATUS_APPROVED: 0, STATUS_DEPRECATED: 0}
    for doc in _iter_all_docs():
        if doc_type and doc.get("type") != doc_type:
            continue
        s = doc.get("status", STATUS_QUARANTINED)
        counts[s] = counts.get(s, 0) + 1
    return counts
