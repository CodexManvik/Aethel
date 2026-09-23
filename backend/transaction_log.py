"""
transaction_log.py

Audit log & rollback engine for agentic tool operations.
Records every file write, file deletion, and shell execution with unified diffs
and base64/raw rollback payloads stored at ~/.aethel/transactions.json.
"""

import os
import json
import uuid
import difflib
import base64
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

TRANSACTIONS_PATH = Path.home() / ".aethel" / "transactions.json"


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: f"txn_{uuid.uuid4().hex[:10]}")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    tool: str
    target: str
    operation: str  # "write" | "delete" | "exec"
    diff: str = ""
    rollback_payload: Dict[str, Any] = Field(default_factory=dict)
    status: str = "completed"  # "completed" | "rolled_back"


def _load_all_transactions() -> List[Dict[str, Any]]:
    """Loads all logged transactions from disk."""
    TRANSACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_PATH.exists():
        return []
    try:
        with open(TRANSACTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[transaction_log] Error reading transaction log ({e}). Resetting.")
        return []


def _save_all_transactions(txns: List[Dict[str, Any]]) -> bool:
    """Saves transaction list to disk."""
    TRANSACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(TRANSACTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(txns, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[transaction_log] Error saving transaction log: {e}")
        return False


def generate_text_diff(old_text: str, new_text: str, filename: str = "file") -> str:
    """Generates a standard unified diff between old and new text."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    return "".join(diff)


def log_transaction(
    tool: str,
    target: str,
    operation: str,
    diff: str = "",
    rollback_payload: Optional[Dict[str, Any]] = None,
) -> Transaction:
    """
    Logs a new tool transaction.
    """
    txn = Transaction(
        tool=tool,
        target=target,
        operation=operation,
        diff=diff,
        rollback_payload=rollback_payload or {},
        status="completed",
    )

    txns = _load_all_transactions()
    txn_data = txn.model_dump() if hasattr(txn, "model_dump") else txn.dict()
    txns.insert(0, txn_data)  # Newest first

    # Keep last 500 transactions to prevent infinite growth
    if len(txns) > 500:
        txns = txns[:500]

    _save_all_transactions(txns)
    return txn


def get_transaction_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns recent transactions."""
    txns = _load_all_transactions()
    return txns[:limit]


def get_transaction_by_id(txn_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single transaction by ID."""
    txns = _load_all_transactions()
    for t in txns:
        if t.get("id") == txn_id:
            return t
    return None


def rollback_transaction(txn_id: str) -> Tuple[bool, str]:
    """
    Rolls back a logged transaction.
    - Write: Restores previous content (or deletes file if created new).
    - Delete: Re-creates file with previous content.
    - Exec: Updates transaction status to rolled_back.
    """
    txns = _load_all_transactions()
    target_txn = None
    target_index = -1

    for idx, t in enumerate(txns):
        if t.get("id") == txn_id:
            target_txn = t
            target_index = idx
            break

    if not target_txn:
        return False, f"Transaction '{txn_id}' not found."

    if target_txn.get("status") == "rolled_back":
        return False, f"Transaction '{txn_id}' has already been rolled back."

    tool = target_txn.get("tool", "")
    target = target_txn.get("target", "")
    op = target_txn.get("operation", "")
    payload = target_txn.get("rollback_payload", {})

    try:
        if op == "write":
            prev_content_b64 = payload.get("previous_content_b64")
            was_created = payload.get("file_was_created", False)

            if was_created:
                # File was newly created by this transaction -> remove it
                if os.path.exists(target):
                    os.remove(target)
                msg = f"Rollback successful: Deleted newly created file '{target}'."
            elif prev_content_b64 is not None:
                # Restore previous file content
                raw_bytes = base64.b64decode(prev_content_b64)
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(raw_bytes)
                msg = f"Rollback successful: Restored previous content of '{target}'."
            else:
                return False, "No rollback payload available for file write."

        elif op == "delete":
            deleted_content_b64 = payload.get("deleted_content_b64")
            if deleted_content_b64 is not None:
                raw_bytes = base64.b64decode(deleted_content_b64)
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(raw_bytes)
                msg = f"Rollback successful: Restored deleted file '{target}'."
            else:
                return False, "No rollback payload available for deleted file."

        elif op == "exec":
            msg = f"Shell command transaction '{txn_id}' marked as rolled back (command execution cannot be automatically undone)."

        else:
            return False, f"Unknown operation '{op}' for rollback."

        # Mark as rolled back
        txns[target_index]["status"] = "rolled_back"
        _save_all_transactions(txns)

        # Log an explicit rollback event
        log_transaction(
            tool="system.rollback",
            target=target,
            operation="rollback",
            diff=f"Rolled back transaction {txn_id}",
            rollback_payload={"original_txn_id": txn_id},
        )

        return True, msg

    except Exception as e:
        return False, f"Rollback execution error: {str(e)}"
