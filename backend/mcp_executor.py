"""
mcp_executor.py

MCP Tool Execution Engine for AETHEL AI Agent.
Implements filesystem_mcp, shell_mcp, and browser_mcp tools with mandatory permission
manifest validation and transaction logging/rollback support.
"""

import os
import re
import base64
import asyncio
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from permission_manifest import is_path_allowed, is_command_allowed, is_domain_allowed
from transaction_log import log_transaction, generate_text_diff


# ─────────────────────────────────────────────────────────────
# Filesystem MCP Tools
# ─────────────────────────────────────────────────────────────

async def execute_read_file(path: str) -> Tuple[bool, str]:
    """Reads a file if permitted by filesystem manifest."""
    allowed, reason = is_path_allowed(path, mode="read")
    if not allowed:
        return False, f"Permission Denied: {reason}"

    if not os.path.exists(path):
        return False, f"File not found: '{path}'"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return True, content
    except Exception as e:
        return False, f"Error reading file '{path}': {str(e)}"


async def execute_write_file(path: str, content: str) -> Tuple[bool, str]:
    """Writes or creates a file with diff & rollback tracking."""
    allowed, reason = is_path_allowed(path, mode="write")
    if not allowed:
        return False, f"Permission Denied: {reason}"

    abs_path = os.path.abspath(os.path.expanduser(path))
    file_was_created = not os.path.exists(abs_path)
    old_text = ""

    if not file_was_created:
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                old_text = f.read()
        except Exception:
            old_text = ""

    # Generate unified diff
    diff_str = generate_text_diff(old_text, content, filename=os.path.basename(path))

    # Base64 encode old content for rollback
    old_bytes = old_text.encode("utf-8")
    prev_b64 = base64.b64encode(old_bytes).decode("utf-8") if old_text else None

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Log transaction
        log_transaction(
            tool="filesystem.write_file",
            target=abs_path,
            operation="write",
            diff=diff_str,
            rollback_payload={
                "previous_content_b64": prev_b64,
                "file_was_created": file_was_created,
            },
        )

        return True, f"File successfully written to '{abs_path}'"
    except Exception as e:
        return False, f"Error writing file '{abs_path}': {str(e)}"


async def execute_list_dir(path: str) -> Tuple[bool, Any]:
    """Lists files and directories if permitted."""
    allowed, reason = is_path_allowed(path, mode="read")
    if not allowed:
        return False, f"Permission Denied: {reason}"

    if not os.path.exists(path):
        return False, f"Directory not found: '{path}'"

    if not os.path.isdir(path):
        return False, f"Path is not a directory: '{path}'"

    try:
        entries = []
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            is_dir = os.path.isdir(item_path)
            size = os.path.getsize(item_path) if not is_dir else 0
            entries.append({
                "name": item,
                "is_dir": is_dir,
                "size_bytes": size,
            })
        return True, entries
    except Exception as e:
        return False, f"Error listing directory '{path}': {str(e)}"


async def execute_delete_file(path: str) -> Tuple[bool, str]:
    """Deletes a file with rollback payload tracking."""
    allowed, reason = is_path_allowed(path, mode="write")
    if not allowed:
        return False, f"Permission Denied: {reason}"

    abs_path = os.path.abspath(os.path.expanduser(path))

    if not os.path.exists(abs_path):
        return False, f"File not found: '{abs_path}'"

    try:
        with open(abs_path, "rb") as f:
            deleted_bytes = f.read()

        del_b64 = base64.b64encode(deleted_bytes).decode("utf-8")

        os.remove(abs_path)

        # Log transaction
        log_transaction(
            tool="filesystem.delete_file",
            target=abs_path,
            operation="delete",
            diff=f"- DELETED FILE: {abs_path}",
            rollback_payload={
                "deleted_content_b64": del_b64,
            },
        )

        return True, f"File '{abs_path}' deleted successfully."
    except Exception as e:
        return False, f"Error deleting file '{abs_path}': {str(e)}"


# ─────────────────────────────────────────────────────────────
# Shell MCP Tools
# ─────────────────────────────────────────────────────────────

async def execute_run_command(command: str, timeout: int = 30) -> Tuple[bool, str]:
    """Executes a shell command if permitted by shell manifest."""
    allowed, reason = is_command_allowed(command)
    if not allowed:
        return False, f"Permission Denied: {reason}"

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"Command execution timed out after {timeout} seconds."

        out_str = stdout.decode("utf-8", errors="replace").strip()
        err_str = stderr.decode("utf-8", errors="replace").strip()

        full_output = out_str
        if err_str:
            full_output += f"\n[STDERR]\n{err_str}" if full_output else err_str

        # Log transaction
        log_transaction(
            tool="shell.run_command",
            target=command,
            operation="exec",
            diff=f"$ {command}\n\n{full_output[:500]}",
            rollback_payload={"command": command, "exit_code": proc.returncode},
        )

        return (proc.returncode == 0), full_output
    except Exception as e:
        return False, f"Shell execution error: {str(e)}"


# ─────────────────────────────────────────────────────────────
# Browser MCP Tools
# ─────────────────────────────────────────────────────────────

async def execute_fetch_url(url: str) -> Tuple[bool, str]:
    """Fetches text content from a web URL if domain is permitted."""
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.split(":")[0]

    allowed, reason = is_domain_allowed(domain)
    if not allowed:
        return False, f"Permission Denied: {reason}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AETHEL-AI-Agent/1.0 (Local-Privacy-Agent)"},
        )

        def _fetch():
            with urllib.request.urlopen(req, timeout=10) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")

        html_content = await asyncio.to_thread(_fetch)

        # Basic HTML to text conversion
        clean_text = re.sub(r"<script.*?>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        clean_text = re.sub(r"<style.*?>.*?</style>", "", clean_text, flags=re.DOTALL | re.IGNORECASE)
        clean_text = re.sub(r"<.*?>", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return True, clean_text[:4000]  # Cap to 4k chars
    except Exception as e:
        return False, f"Error fetching URL '{url}': {str(e)}"
