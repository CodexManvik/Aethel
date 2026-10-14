"""Azure Speech credentials and endpoints. The key is a secret held in the KeyStore ("azure_speech"); the region
(e.g. "uksouth") is an ordinary setting, or the AZURE_SPEECH_REGION environment variable for headless dev.

The key is sent to {region}.stt/tts.speech.microsoft.com only, so the region is checked as a plain name before it
becomes part of a host: a region like "evil.example/x" must never decide where the key goes."""
import os
import re
from dataclasses import dataclass

REGION = re.compile(r"^[a-z][a-z0-9]{2,29}$")  # uksouth, westeurope, eastus2, southcentralus...


def valid_region(region: str) -> bool:
    return bool(REGION.fullmatch(region))


@dataclass(frozen=True)
class AzureSpeech:
    key: str = ""
    region: str = ""

    def __repr__(self) -> str:  # never print the key (logs, tracebacks)
        return f"AzureSpeech(region={self.region!r}, key={'set' if self.key else 'unset'})"

    @property
    def stt_url(self) -> str:
        return f"https://{self.region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"

    @property
    def tts_url(self) -> str:
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"


def azure_from(settings, keys) -> AzureSpeech | None:
    """The credentials as they are now (the user can change them at any time), or None if either is missing or the
    region isn't a plain region name."""
    key = keys.get("azure_speech")
    region = (settings.voice.azure_region or os.environ.get("AZURE_SPEECH_REGION", "")).strip().lower()
    if not key or not valid_region(region):
        return None
    return AzureSpeech(key=key, region=region)
