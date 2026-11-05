"""One way to turn a path the model wrote into the path that gets touched, so
the permission check, the handler and the task's checks all agree."""
import os
from pathlib import Path


def is_network_path(path: str) -> bool:
    return len(path) > 1 and path[0] in "\\/" and path[1] in "\\/"


def real_path(path: str) -> str:
    """os.path.realpath, except that a network path isn't opened: resolving \\\\host\\share connects to that host,
    which on Windows offers it this user's credentials, and this runs before any approval. It also raises for a host
    it can't reach, and a checked path must never crash the check."""
    if os.name == "nt" and is_network_path(path):
        return os.path.normpath(path)
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return os.path.normpath(path)


def resolve_user_path(raw: str) -> Path | None:
    """Expand ~ and %VAR%, then resolve links and '..'. Relative paths (and, on
    Windows, paths without a drive) return None: there is no sensible folder
    to resolve them against."""
    if not isinstance(raw, str) or not raw:
        return None
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(expanded) or (os.name == "nt" and not os.path.splitdrive(expanded)[0]):
        return None
    return Path(real_path(expanded))
