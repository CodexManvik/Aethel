import os
from dataclasses import dataclass

from ..settings import AppSettings

LOCAL_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1")


@dataclass(frozen=True)
class ProviderMeta:
    id: str
    label: str
    needs_key: bool
    base_url: str
    endpoint: bool = False  # one of the user's own endpoints (Settings → Providers → Add endpoint)


BUILTINS: dict[str, ProviderMeta] = {
    "groq": ProviderMeta("groq", "Groq", True, "https://api.groq.com/openai/v1"),
    "gemini": ProviderMeta(
        "gemini", "Google Gemini", True, "https://generativelanguage.googleapis.com/v1beta/openai/"
    ),
    "openrouter": ProviderMeta("openrouter", "OpenRouter", True, "https://openrouter.ai/api/v1"),
    "local": ProviderMeta("local", "Local (llama.cpp)", False, LOCAL_BASE_URL),
}
PROVIDERS = BUILTINS  # kept for imports from before endpoints

# Endpoints are often local servers (llama.cpp, LM Studio, Ollama) that take no key; the OpenAI client still needs a
# non-empty one.
NO_KEY = "no-key"


def key_name(provider: str) -> str:
    """Where a provider's key is kept (KeyStore, the OS credential store): the built-ins and the endpoint migrated
    from the old single custom URL by their own name, every other endpoint as "endpoint:<id>"."""
    return provider if provider in BUILTINS or provider == "custom" else f"endpoint:{provider}"


def providers(settings: AppSettings) -> list[ProviderMeta]:
    return [*BUILTINS.values(),
            *[ProviderMeta(e.id, e.name, True, e.base_url, endpoint=True) for e in settings.endpoints]]


def meta_for(provider: str, settings: AppSettings) -> ProviderMeta | None:
    return next((m for m in providers(settings) if m.id == provider), None)


def base_url_for(provider: str, settings: AppSettings) -> str:
    meta = meta_for(provider, settings)
    return meta.base_url if meta else ""


def key_optional(provider: str, settings: AppSettings) -> bool:
    meta = meta_for(provider, settings)
    return meta is not None and meta.endpoint


def api_key_for(provider: str, keys, settings: AppSettings) -> tuple[str | None, str | None]:
    """(key to send, None) or (None, why this provider can't be used)."""
    meta = meta_for(provider, settings)
    if meta is None:
        return None, f"no provider or endpoint called {provider!r}"
    if meta.endpoint and not meta.base_url:
        return None, f"the endpoint {meta.label!r} has no URL (Settings → Providers)"
    key = keys.get(key_name(provider))
    if key:
        return key, None
    if meta.endpoint:
        return NO_KEY, None
    return None, "no API key"
