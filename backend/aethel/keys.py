"""API keys, held in memory only. The Tauri shell pushes them from the OS
credential store at startup; env vars are a fallback for headless dev."""
import os
import re

KEY_PROVIDERS = ("groq", "gemini", "openrouter", "custom", "azure_speech")
ENV_VARS = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_API_KEY",
    "azure_speech": "AZURE_SPEECH_KEY",
}


ENDPOINT_KEY = re.compile(r"^endpoint:[a-z][a-z0-9-]{1,30}$")  # a user endpoint's key (providers/catalog.key_name)


def valid_key_name(name: str) -> bool:
    return name in KEY_PROVIDERS or bool(ENDPOINT_KEY.fullmatch(name))


class KeyStore:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def set_many(self, keys: dict[str, str | None]) -> None:
        unknown = {k for k in keys if not valid_key_name(k)}
        if unknown:
            raise ValueError(f"unknown providers: {sorted(unknown)}")
        for provider, value in keys.items():
            if value:
                self._keys[provider] = value
            else:
                self._keys.pop(provider, None)

    def get(self, provider: str) -> str | None:
        return self._keys.get(provider) or os.environ.get(ENV_VARS.get(provider, ""), "") or None

    def status(self) -> dict[str, bool]:
        return {**{p: self.get(p) is not None for p in KEY_PROVIDERS},
                **{k: True for k in self._keys if ENDPOINT_KEY.fullmatch(k)}}
