"""
permission_manifest.py

Manages and enforces security permission constraints for agentic tool execution (MCP).
Permission manifests are stored at ~/.aethel/permissions.yaml.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, List, Tuple
from pydantic import BaseModel, Field

PERMISSIONS_PATH = Path.home() / ".aethel" / "permissions.yaml"

DEFAULT_PERMISSIONS = {
    "filesystem": {
        "allowed_read_paths": [
            str(Path.home() / "Documents"),
            str(Path.home() / "Downloads"),
            "C:/Project/persona-ai",
        ],
        "allowed_write_paths": [
            str(Path.home() / "Documents" / "ai-tasks"),
            str(Path.home() / ".aethel" / "scratch"),
            "C:/Project/persona-ai/scratch",
        ],
        "forbidden_paths": [
            "C:/Windows",
            "C:/Program Files",
            "C:/Program Files (x86)",
            str(Path.home() / ".ssh"),
            str(Path.home() / ".aws"),
        ],
    },
    "shell": {
        "allowed_commands": [
            "git status",
            "git add",
            "git commit",
            "git log",
            "git diff",
            "python",
            "pytest",
            "dir",
            "ls",
            "echo",
            "type",
            "cat",
        ],
        "forbidden_patterns": [
            "rm -rf",
            "format",
            "del /f",
            "del /s",
            "rd /s",
            "mkfs",
            "dd if=",
            "shutdown",
            "reg delete",
            "icacls",
        ],
        # Interpreter/shell-metacharacter guard. Even an allowlisted command
        # (e.g. "python", "git") becomes arbitrary code execution once these
        # appear, so they are rejected regardless of the allowlist. This closes
        # the `python -c "..."` sandbox escape.
        "forbidden_arguments": [
            "-c",
            "-e",
            "-m",
            "--exec",
            "-command",
            "-encodedcommand",
            "|",
            ";",
            "&&",
            "&",
            "`",
            "$(",
            ">",
            ">>",
            "<",
        ],
    },
    "browser": {
        "allowed_domains": ["*"],
    },
}


class FilesystemPermissions(BaseModel):
    allowed_read_paths: List[str] = Field(default_factory=list)
    allowed_write_paths: List[str] = Field(default_factory=list)
    forbidden_paths: List[str] = Field(default_factory=list)


class ShellPermissions(BaseModel):
    allowed_commands: List[str] = Field(default_factory=list)
    forbidden_patterns: List[str] = Field(default_factory=list)
    forbidden_arguments: List[str] = Field(default_factory=list)


class BrowserPermissions(BaseModel):
    allowed_domains: List[str] = Field(default_factory=list)


class PermissionManifest(BaseModel):
    filesystem: FilesystemPermissions = Field(default_factory=FilesystemPermissions)
    shell: ShellPermissions = Field(default_factory=ShellPermissions)
    browser: BrowserPermissions = Field(default_factory=BrowserPermissions)


def ensure_permissions_file() -> Path:
    """Ensures permissions.yaml exists with sensible defaults."""
    PERMISSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PERMISSIONS_PATH.exists():
        with open(PERMISSIONS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_PERMISSIONS, f, default_flow_style=False, sort_keys=False)
    return PERMISSIONS_PATH


def load_permission_manifest() -> PermissionManifest:
    """Loads the permission manifest from disk, falling back to defaults if corrupt."""
    path = ensure_permissions_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return PermissionManifest(**data)
    except Exception as e:
        print(f"[permission_manifest] Warning: Failed to load manifest ({e}). Using defaults.")
        return PermissionManifest(**DEFAULT_PERMISSIONS)


def save_permission_manifest(manifest_data: Dict[str, Any]) -> bool:
    """Saves updated permission manifest data to disk."""
    path = ensure_permissions_file()
    try:
        # Validate through model
        model = PermissionManifest(**manifest_data)
        dump = model.model_dump() if hasattr(model, "model_dump") else model.dict()
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(dump, f, default_flow_style=False, sort_keys=False)
        return True
    except Exception as e:
        print(f"[permission_manifest] Error saving manifest: {e}")
        return False


def _normalize_path(p: str) -> str:
    """Normalizes path for cross-platform comparison."""
    try:
        abs_p = os.path.abspath(os.path.expanduser(p))
        # Windows case normalization
        return os.path.normcase(abs_p)
    except Exception:
        return p.lower()


def is_path_allowed(target_path: str, mode: str = "read") -> Tuple[bool, str]:
    """
    Validates if a target filesystem path is permitted for read or write.
    Returns (allowed: bool, reason: str).
    """
    manifest = load_permission_manifest()
    norm_target = _normalize_path(target_path)

    # 1. Check explicit forbidden paths first
    for fpath in manifest.filesystem.forbidden_paths:
        norm_f = _normalize_path(fpath)
        if norm_target == norm_f or norm_target.startswith(norm_f + os.sep):
            return False, f"Path '{target_path}' matches forbidden path policy '{fpath}'"

    # 2. Select mode list
    allowed_list = (
        manifest.filesystem.allowed_write_paths
        if mode == "write"
        else manifest.filesystem.allowed_read_paths
    )

    # If wildcard is set
    if "*" in allowed_list:
        return True, "Allowed by wildcard policy"

    # 3. Check if target is inside an allowed path
    for apath in allowed_list:
        norm_a = _normalize_path(apath)
        if norm_target == norm_a or norm_target.startswith(norm_a + os.sep):
            return True, f"Path allowed by policy '{apath}'"

    return False, f"Path '{target_path}' is not in allowed_{mode}_paths"


def is_command_allowed(command: str) -> Tuple[bool, str]:
    """
    Validates if a shell command is allowed by the policy manifest.
    Returns (allowed: bool, reason: str).
    """
    manifest = load_permission_manifest()
    cmd_strip = command.strip()
    cmd_lower = cmd_strip.lower()

    # 1. Check forbidden patterns
    for pat in manifest.shell.forbidden_patterns:
        if pat.lower() in cmd_lower:
            return False, f"Command contains forbidden pattern '{pat}'"

    # 1b. Check forbidden arguments / shell metacharacters. These turn an
    # otherwise-allowlisted binary into arbitrary code execution or chained
    # commands, so they are rejected before the allowlist is even consulted.
    # Fall back to the default list when an older manifest on disk predates
    # this field, so existing installs stay protected without editing YAML.
    forbidden_args = (
        manifest.shell.forbidden_arguments
        or DEFAULT_PERMISSIONS["shell"]["forbidden_arguments"]
    )
    tokens = cmd_lower.split()
    for bad in forbidden_args:
        bad_lower = bad.lower()
        # Exact-token match for flag-style args (-c, -m, …); substring match
        # for metacharacters that need no surrounding whitespace (|, ;, $(, …).
        if bad_lower.startswith("-") or bad_lower.startswith("--"):
            if bad_lower in tokens:
                return False, f"Command contains forbidden argument '{bad}'"
        elif bad_lower in cmd_lower:
            return False, f"Command contains forbidden shell operator '{bad}'"

    # 2. Check wildcard
    if "*" in manifest.shell.allowed_commands:
        return True, "Allowed by command wildcard policy"

    # 3. Check allowed commands (prefix or exact match)
    for allowed_cmd in manifest.shell.allowed_commands:
        ac_lower = allowed_cmd.strip().lower()
        if cmd_lower == ac_lower or cmd_lower.startswith(ac_lower + " ") or cmd_lower.startswith(ac_lower + "&&"):
            return True, f"Command matches allowed command rule '{allowed_cmd}'"

    return False, f"Command '{command}' is not in allowed_commands manifest"


def is_domain_allowed(domain: str) -> Tuple[bool, str]:
    """
    Validates if a domain or URL is permitted for web fetch.
    Returns (allowed: bool, reason: str).
    """
    manifest = load_permission_manifest()
    dom_lower = domain.lower().strip()

    if "*" in manifest.browser.allowed_domains:
        return True, "Allowed by browser wildcard policy"

    for allowed_dom in manifest.browser.allowed_domains:
        ad_lower = allowed_dom.lower().strip()
        if dom_lower == ad_lower or dom_lower.endswith("." + ad_lower):
            return True, f"Domain matches allowed domain policy '{allowed_dom}'"

    return False, f"Domain '{domain}' is not in allowed_domains manifest"
