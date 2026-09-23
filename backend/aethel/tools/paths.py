"""One way to turn a path the model wrote into the path that gets touched, so
the permission check, the handler and the task's checks all agree."""
import os
from pathlib import Path


def resolve_user_path(raw: str) -> Path | None:
    """Expand ~ and %VAR%, then resolve links and '..'. Relative paths (and, on
    Windows, paths without a drive) return None: there is no sensible folder
    to resolve them against."""
    if not isinstance(raw, str) or not raw:
        return None
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(expanded) or (os.name == "nt" and not os.path.splitdrive(expanded)[0]):
        return None
    return Path(os.path.realpath(expanded))
