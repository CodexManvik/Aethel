import logging
import os
import secrets

log = logging.getLogger("aethel.auth")

# Browser origins allowed to talk to the backend (CORS for REST, Origin check
# for the WebSocket). Non-browser clients send no Origin and are allowed.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def origin_allowed(origin: str | None) -> bool:
    return origin is None or origin in ALLOWED_ORIGINS


class AuthConfig:
    def __init__(self, token: str | None, dev: bool):
        self.token = token
        self.dev = dev

    @classmethod
    def from_env(cls) -> "AuthConfig":
        token = os.environ.get("AETHEL_TOKEN") or None
        dev = os.environ.get("AETHEL_DEV") == "1"
        if token is None and not dev:
            token = secrets.token_urlsafe(32)
            log.warning("AETHEL_TOKEN not set; generated a random token (clients must be given it).")
        if dev and token is None:
            log.warning("AETHEL_DEV=1 with no token: authentication is DISABLED.")
        return cls(token=token, dev=dev)

    def check(self, presented: str | None) -> bool:
        if self.token is None:
            return self.dev
        return presented is not None and secrets.compare_digest(presented, self.token)
