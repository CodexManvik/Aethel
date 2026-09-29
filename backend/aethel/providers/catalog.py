import os
from dataclasses import dataclass

from ..settings import AppSettings

LOCAL_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1")


@dataclass(frozen=True)
class ProviderMeta:
    id: str
    label: str
    needs_key: bool
    base_url: str  # empty = comes from settings (custom)


PROVIDERS: dict[str, ProviderMeta] = {
    "groq": ProviderMeta("groq", "Groq", True, "https://api.groq.com/openai/v1"),
    "gemini": ProviderMeta(
        "gemini", "Google Gemini", True, "https://generativelanguage.googleapis.com/v1beta/openai/"
    ),
    "openrouter": ProviderMeta("openrouter", "OpenRouter", True, "https://openrouter.ai/api/v1"),
    "custom": ProviderMeta("custom", "Custom (OpenAI-compatible)", True, ""),
    "local": ProviderMeta("local", "Local (llama.cpp)", False, LOCAL_BASE_URL),
}


def base_url_for(provider: str, settings: AppSettings) -> str:
    if provider == "custom":
        return settings.custom_base_url
    return PROVIDERS[provider].base_url


# Custom endpoints are often local servers (llama.cpp, LM Studio, Ollama) that take
# no key; the OpenAI client still needs a non-empty one.
KEY_OPTIONAL = {"custom"}
NO_KEY = "no-key"


def api_key_for(provider: str, keys, settings: AppSettings) -> tuple[str | None, str | None]:
    """(key to send, None) or (None, why this provider can't be used)."""
    if provider == "custom" and not settings.custom_base_url:
        return None, "no custom endpoint URL set (Settings → Providers)"
    key = keys.get(provider)
    if key:
        return key, None
    if provider in KEY_OPTIONAL:
        return NO_KEY, None
    return None, "no API key"
