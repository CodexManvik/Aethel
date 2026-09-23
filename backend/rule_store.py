# rule_store.py
"""
Compatibility layer over knowledge_store (RSM Phase 2).

Behavioral rules used to live in a LanceDB table; they are now markdown files
under ~/.aethel/knowledge/rules/ managed by knowledge_store.py. This module
keeps the original rule_store API surface so rule_engine.py, prompt_builder.py
and the server admin endpoints work unchanged.

On first import it performs a one-time, best-effort migration of any legacy
LanceDB rules into markdown (marked by a .migrated_lancedb sentinel file).
"""

from pathlib import Path
from typing import Optional

import knowledge_store as ks

# ─────────────────────────────────────────────
# One-time LanceDB → markdown migration
# ─────────────────────────────────────────────

_LEGACY_DB_PATH = Path(__file__).parent.parent / "lancedb_data" / "rules_db"
_MIGRATION_MARKER = ks.KNOWLEDGE_DIR / ".migrated_lancedb"


def _migrate_legacy_rules() -> None:
    if _MIGRATION_MARKER.exists() or not _LEGACY_DB_PATH.exists():
        return
    try:
        import lancedb

        db = lancedb.connect(str(_LEGACY_DB_PATH))
        if "behavioral_rules" not in db.table_names():
            _MIGRATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _MIGRATION_MARKER.write_text("no legacy table\n", encoding="utf-8")
            return

        rows = db.open_table("behavioral_rules").to_pandas().to_dict("records")
        migrated = 0
        for r in rows:
            status = r.get("status", ks.STATUS_QUARANTINED)
            if status not in (ks.STATUS_QUARANTINED, ks.STATUS_APPROVED, ks.STATUS_DEPRECATED):
                status = ks.STATUS_QUARANTINED
            ks.insert_rule(
                category=str(r.get("category", "task_behavior")),
                confidence=float(r.get("confidence", 0.5)),
                scope=str(r.get("scope", "global")),
                body=str(r.get("body", "")),
                rationale=str(r.get("rationale", "")),
                examples_before=str(r.get("examples_before", "")),
                examples_after=str(r.get("examples_after", "")),
                source_interaction_id=str(r.get("source_interaction_id", "legacy_lancedb")),
                status=status,
            )
            migrated += 1

        _MIGRATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _MIGRATION_MARKER.write_text(f"migrated {migrated} rules\n", encoding="utf-8")
        print(f"[rule_store] Migrated {migrated} legacy LanceDB rules to markdown.")
    except Exception as e:
        # Never block startup on migration problems; legacy data stays put.
        print(f"[rule_store] Legacy rule migration skipped: {e}")


_migrate_legacy_rules()


# ─────────────────────────────────────────────
# Legacy API surface (delegates to knowledge_store)
# ─────────────────────────────────────────────

def insert_rule(
    category: str,
    confidence: float,
    scope: str,
    body: str,
    rationale: str,
    examples_before: str,
    examples_after: str,
    source_interaction_id: str,
) -> str:
    return ks.insert_rule(
        category=category,
        confidence=confidence,
        scope=scope,
        body=body,
        rationale=rationale,
        examples_before=examples_before,
        examples_after=examples_after,
        source_interaction_id=source_interaction_id,
    )


def approve_rule(rule_id: str) -> bool:
    return ks.approve_doc(rule_id)


def reject_rule(rule_id: str) -> bool:
    return ks.reject_doc(rule_id)


def deprecate_rule(rule_id: str) -> bool:
    return ks.reject_doc(rule_id)


def update_rule_body(rule_id: str, body: str, rationale: str) -> bool:
    return ks.update_rule_content(rule_id, body, rationale)


def retrieve_relevant_rules(query: str, top_k: Optional[int] = None) -> list[dict]:
    from config import RULE_ENGINE_TOP_K_RULES

    k = top_k or RULE_ENGINE_TOP_K_RULES
    return ks.retrieve(query, doc_type=ks.TYPE_RULE, top_k=k)


def _public(doc: dict) -> dict:
    """Strips internal underscore-prefixed keys before serving over the API."""
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def get_quarantined_rules() -> list[dict]:
    return [_public(d) for d in ks.get_by_status(ks.STATUS_QUARANTINED, doc_type=ks.TYPE_RULE)]


def get_all_rules(status: Optional[str] = None) -> list[dict]:
    return [_public(d) for d in ks.get_by_status(status, doc_type=ks.TYPE_RULE)]


def get_rule_by_id(rule_id: str) -> Optional[dict]:
    doc = ks.get_by_id(rule_id)
    return _public(doc) if doc else None


def get_rule_count_by_status() -> dict[str, int]:
    return ks.count_by_status(doc_type=ks.TYPE_RULE)
