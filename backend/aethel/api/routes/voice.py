"""Settings -> Voice: what is available, a preview of a voice blend, and a check of the Azure Speech credentials."""
import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ...services import Services
from ...voice.azure import azure_from
from ...voice.stt import pcm16_to_wav
from ...voice.tts import KOKORO_LANG, AzureTTS, VoiceBlend, speakable
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/voice", dependencies=[Depends(require_auth)])


class SttStatus(BaseModel):
    name: str        # what would hear the next call: "azure:uksouth" or "local:faster-whisper-small"
    azure: bool      # Azure Speech has a key and a region
    loaded: bool     # the engine needs no loading (Azure), or the local model is in memory


class TtsStatus(BaseModel):
    kokoro: bool     # Kokoro's model files are on this PC
    voices: int
    azure: bool


class VoiceStatus(BaseModel):
    stt: SttStatus
    tts: TtsStatus
    last_turn_ms: int | None  # the last call turn: end of speech to first audio


class PreviewIn(BaseModel):
    blend: dict[str, float]
    text: str = Field(min_length=1, max_length=200)


@router.get("/status")
def voice_status(svc: Services = Depends(get_services)) -> VoiceStatus:
    settings = svc.settings.get()
    stt = svc.voice.stt()
    configured = azure_from(settings, svc.keys) is not None
    return VoiceStatus(
        stt=SttStatus(name=stt.name, azure=configured,
                      loaded=stt.name.startswith("azure:") or getattr(stt, "_model", None) is not None),
        tts=TtsStatus(kokoro=svc.voice.kokoro.available, voices=len(svc.voice.kokoro.voices()),
                      azure=configured and not settings.private_mode),
        last_turn_ms=svc.voice.last_turn_ms())


@router.get("/voices")
def voices(svc: Services = Depends(get_services)) -> dict[str, list[str]]:
    """Kokoro's voices grouped by language (the first letter of a name says which)."""
    grouped: dict[str, list[str]] = {}
    for name in svc.voice.kokoro.voices():
        grouped.setdefault(KOKORO_LANG.get(name[0], "other"), []).append(name)
    return grouped


@router.post("/preview")
async def preview(body: PreviewIn, svc: Services = Depends(get_services)) -> Response:
    try:
        blend = VoiceBlend.parse(body.blend, svc.voice.kokoro.voices() or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    spoken = speakable(body.text)
    if not spoken:
        raise HTTPException(status_code=422, detail="There's nothing to say in that text.")
    speech = await svc.voice.tts().synthesize(spoken, blend, svc.settings.get().private_mode)
    if speech is None:
        raise HTTPException(status_code=503, detail="No voice is available right now.")
    return Response(pcm16_to_wav(speech.audio, speech.sample_rate), media_type="audio/wav")


class AzureCheck(BaseModel):
    ok: bool
    message: str


@router.post("/azure-check")
async def azure_check(svc: Services = Depends(get_services)) -> AzureCheck:
    """Asks Azure Speech for one word of speech: proves the key and the region work, without recording anything."""
    settings = svc.settings.get()
    if settings.private_mode:
        return AzureCheck(ok=False, message="Private mode is on: nothing is sent to Azure.")
    azure = azure_from(settings, svc.keys)
    if azure is None:
        return AzureCheck(ok=False, message="Add the Azure Speech key and its region first.")
    try:
        await anyio.to_thread.run_sync(AzureTTS(azure, settings.voice.azure_voice).synthesize, "OK.")
    except Exception as exc:
        return AzureCheck(ok=False, message=f"Azure Speech didn't accept that: {exc}.")
    return AzureCheck(ok=True, message=f"Azure Speech works in {azure.region}.")
