"""The user's permission manifest (~/.aethel/permissions.yaml, same format as
v1). Every filesystem and shell action is checked here first."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..paths import PROJECT_ROOT, aethel_home

Verdict = Literal["allow", "ask", "deny"]


@dataclass
class Decision:
    verdict: Verdict
    reason: str


class FilesystemPermissions(BaseModel):
    allowed_read_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)


class ShellPermissions(BaseModel):
    allowed_commands: list[str] = Field(default_factory=list)
    forbidden_patterns: list[str] = Field(default_factory=list)
    forbidden_arguments: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    filesystem: FilesystemPermissions = Field(default_factory=FilesystemPermissions)
    shell: ShellPermissions = Field(default_factory=ShellPermissions)


FORBIDDEN_ARGUMENTS = ["-c", "-e", "-m", "--exec", "-command", "-encodedcommand",
                       "|", ";", "&&", "&", "`", "$(", ">", ">>", "<"]


def default_manifest() -> dict:
    home = Path.home()
    return {
        "filesystem": {
            "allowed_read_paths": [str(home / "Documents"), str(home / "Downloads"), str(home / "Desktop"),
                                   str(PROJECT_ROOT)],
            "allowed_write_paths": [str(home / "Documents" / "Aethel"), str(aethel_home() / "scratch")],
            "forbidden_paths": ["C:/Windows", "C:/Program Files", "C:/Program Files (x86)",
                                str(home / ".ssh"), str(home / ".aws")],
        },
        "shell": {
            "allowed_commands": ["git status", "git log", "git diff", "dir", "echo", "type", "where"],
            "forbidden_patterns": ["rm -rf", "format ", "del /f", "del /s", "rd /s", "mkfs", "dd if=",
                                   "shutdown", "reg delete", "icacls", "takeown", "bcdedit"],
            "forbidden_arguments": FORBIDDEN_ARGUMENTS,
        },
    }


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(p)))


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


_DB_FILES = ("aethel.db", "aethel.db-wal", "aethel.db-shm", "aethel.db-journal")


def _protected(target: str, mode: Literal["read", "write"]) -> Decision | None:
    """Hard rules the manifest can't override: the agent may never change its
    own configuration, database or source, drop a .env, or add a startup
    program, and it may never read the database or a .env file."""
    home = aethel_home()
    if os.path.basename(target) == ".env":
        return Decision("deny", ".env files hold secrets and are off-limits.")
    if mode == "read":
        if any(target == _norm(str(home / name)) for name in _DB_FILES):
            return Decision("deny", "Aethel's own database is off-limits.")
        return None
    if _inside(target, _norm(str(home))) and not _inside(target, _norm(str(home / "scratch"))):
        return Decision("deny", "Aethel's own settings and data can't be changed by a task.")
    if _inside(target, _norm(str(PROJECT_ROOT))):
        return Decision("deny", "Aethel's own program files can't be changed by a task.")
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        if _inside(target, _norm(startup)):
            return Decision("deny", "Programs that run at sign-in can't be added by a task.")
    return None


class Permissions:
    def __init__(self, path: Path):
        self.path = path
        self.manifest = self._load()

    def _load(self) -> Manifest:
        if not self.path.is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(yaml.safe_dump(default_manifest(), sort_keys=False), encoding="utf-8")
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return Manifest.model_validate({k: v for k, v in data.items() if k in ("filesystem", "shell")})

    def reload(self) -> None:
        self.manifest = self._load()

    def check_path(self, path: str, mode: Literal["read", "write"]) -> Decision:
        if not path:
            return Decision("deny", "No path given.")
        target = _norm(path)
        protected = _protected(target, mode)
        if protected is not None:
            return protected
        fs = self.manifest.filesystem
        for forbidden in fs.forbidden_paths:
            if _inside(target, _norm(forbidden)):
                return Decision("deny", f"{forbidden} is off-limits.")
        allowed = fs.allowed_write_paths if mode == "write" else fs.allowed_read_paths
        if "*" in allowed or any(_inside(target, _norm(a)) for a in allowed):
            return Decision("allow", f"Inside your allowed {mode} folders.")
        return Decision("ask", f"Outside the folders I'm allowed to {mode} without asking.")

    def check_command(self, command: str) -> Decision:
        cmd = command.strip()
        low = cmd.lower()
        if not cmd:
            return Decision("deny", "Empty command.")
        sh = self.manifest.shell
        for pattern in sh.forbidden_patterns:
            if pattern.lower() in low:
                return Decision("deny", f"Commands containing '{pattern.strip()}' are blocked.")
        # Normalize quotes and escape chars before tokenizing, but keep original for substring checks
        plain = re.sub(r"[\"'^`]", "", low)
        tokens = plain.split()
        for bad in sh.forbidden_arguments or FORBIDDEN_ARGUMENTS:
            b = bad.lower()
            if b.startswith("-"):
                # Flag-style: match exact token or token prefix (on normalized text)
                if any(t == b or t.startswith(b) for t in tokens):
                    return Decision("deny", f"'{bad}' would let a command run arbitrary code or chain commands.")
            else:
                # Non-flag: substring match on original to catch literal |, backticks, etc.
                if b in low:
                    return Decision("deny", f"'{bad}' would let a command run arbitrary code or chain commands.")
        for allowed in sh.allowed_commands:
            a = allowed.strip().lower()
            if a == "*" or low == a or low.startswith(a + " "):
                return Decision("allow", f"Matches allowed command '{allowed}'.")
        return Decision("ask", "Not on the list of commands I may run without asking.")
