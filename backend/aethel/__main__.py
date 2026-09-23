import os

import uvicorn

from .parent_watch import watch_parent


def main() -> None:
    parent = os.environ.get("AETHEL_PARENT_PID")
    if parent and parent.isdigit():
        watch_parent(int(parent))
    uvicorn.run(
        "aethel.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=int(os.environ.get("AETHEL_PORT", "8765")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
