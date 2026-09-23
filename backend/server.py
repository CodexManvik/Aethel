# server.py
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Dict, Any
import asyncio
import os
import uvicorn
import re
from pathlib import Path
from pydantic import BaseModel, Field
import json
# Web search backend. The `duckduckgo_search` package was renamed to `ddgs`;
# the old one still imports but silently returns zero results, which makes the
# agent think its own tool is broken. Prefer ddgs, fall back, then degrade.
try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover - legacy installs
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None
from datetime import datetime
import pytz
import uuid
import secrets
from functools import wraps
import httpx

# --- CONFIGURATION IMPORTS ---
from config import (
    CORS_ALLOW_ORIGINS, CORS_ALLOW_METHODS, CORS_ALLOW_HEADERS, VIDEO_RECORDING_MODE,
    IMAGE_GEN_ENABLED,
    CURRENT_RUNTIME_TUNNEL, WEB_SEARCH_MAX_RESULTS,
    TEMP_IMAGE_DIR, ADMIN_PASSWORD, AGENT_LOOP_ENABLED,
)

# Single-user local session constant — no tester tokens or broker required.
LOCAL_SESSION_TOKEN = "local"

# --- VOICE INTEGRATION ---
import tts_engine
import stt_engine

# Local Subsystem Integration
from memory import retrieve_memories
from prompt_builder import build_messages
from generation import generate_stream, get_available_models, switch_active_model
from state import state, update_state, set_active_model
from graph_memory import extract_entities_and_update, get_graph_context
from media_handler import extract_frames
from image_generator import generate_selfie_with_handoff

# Rule Engine (RSM — Reflective Skill Memory)
from rule_engine import ConsolidationScheduler
from rule_store import (
    get_quarantined_rules,
    get_all_rules,
    get_rule_by_id,
    get_rule_count_by_status,
    approve_rule,
    reject_rule,
    update_rule_body,
    deprecate_rule,
)

# Agentic Tool Execution & Permission Layer (Phase 2)
import permission_manifest
import transaction_log
import mcp_executor
from agent_loop import run_agent_loop
from agent_loop import AGENT_MAX_STEPS
import knowledge_store as ks
from skill_engine import maybe_write_skill
import prompt_builder
import persona_distillation
import eval_harness

# Cognitive Emotion Engine
from emotion_engine import calculate_text_delta, get_affective_state
from config import BASELINE_MOOD, BASELINE_ENERGY, EMOTION_ENGINE_ENABLED, EMOTION_DECAY_RATE
from prompt_builder import ROSIA_PERSONA_ID

# UI Terminal Formatting
from rich.console import Console
from rich.panel import Panel

# ==========================================
# APP INIT
# ==========================================

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    # Startup: kick off background seraethel. Names resolve at call time, so the
    # handlers defined later in the module are available here.
    asyncio.create_task(chronos_loop())
    asyncio.create_task(_warm_start_models())
    asyncio.create_task(_warm_start_speech())
    yield
    # Shutdown: nothing to tear down explicitly (llama-server has its own atexit).


app = FastAPI(lifespan=_lifespan)
chat_lock = asyncio.Lock()
console = Console()
last_interaction_time = datetime.now()
chronos_queue = asyncio.Queue()
allow_creds = False if "*" in CORS_ALLOW_ORIGINS else True

# Tool call patterns — LLM outputs these tags to invoke local tools.
CALL_TOOL_SEARCH_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*search,\s*query:\s*"(.*?)"\]',
    re.IGNORECASE,
)
CALL_TOOL_FS_READ_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*fs\.read,\s*path:\s*"(.*?)"\]',
    re.IGNORECASE,
)
CALL_TOOL_FS_WRITE_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*fs\.write,\s*path:\s*"(.*?)",\s*content:\s*"(.*?)"\]',
    re.IGNORECASE | re.DOTALL,
)
CALL_TOOL_FS_LIST_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*fs\.list,\s*path:\s*"(.*?)"\]',
    re.IGNORECASE,
)
CALL_TOOL_FS_DELETE_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*fs\.delete,\s*path:\s*"(.*?)"\]',
    re.IGNORECASE,
)
CALL_TOOL_SHELL_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*shell,\s*command:\s*"(.*?)"\]',
    re.IGNORECASE,
)
CALL_TOOL_FETCH_PATTERN = re.compile(
    r'\[CALL_TOOL:\s*fetch,\s*url:\s*"(.*?)"\]',
    re.IGNORECASE,
)
SELFIE_PATTERN = re.compile(r'\[TRIGGER_SELFIE:(.*?)\]')
STATUS_SUBSCRIBERS: List[asyncio.Queue] = []

# Rule engine scheduler — one global instance shared across all sessions.
rule_scheduler = ConsolidationScheduler()

# SSE queue for rule quarantine notifications to the frontend.
RULE_NOTIFICATION_SUBSCRIBERS: List[asyncio.Queue] = []

# Per-session record of which knowledge docs (rules/skills) shaped the last
# turn, so a /feedback thumbs-down can retroactively mark them as a failure.
_LAST_TURN_DOCS: dict[str, list[str]] = {}


async def _reflect_and_write_skill(
    user_input: str,
    tool_calls: list,
    succeeded: bool,
    session_id: str,
) -> None:
    """Background: distill a reusable skill from a completed tool-using turn."""
    try:
        skill_id = await maybe_write_skill(
            user_request=user_input,
            tool_calls=tool_calls,
            succeeded=succeeded,
            source_interaction_id=session_id,
        )
    except Exception:
        return
    if not skill_id:
        return
    payload = json.dumps({"type": "skill_quarantined", "count": 1})
    for sub in RULE_NOTIFICATION_SUBSCRIBERS.copy():
        try:
            sub.put_nowait(payload)
        except Exception:
            continue

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
    allow_credentials=allow_creds,
)

app.mount("/images", StaticFiles(directory=TEMP_IMAGE_DIR), name="images")

# ==========================================
# SESSION HISTORY (client-managed; server keeps a fallback in-memory copy)
# ==========================================
session_histories: dict[str, list] = {}
chronos_history: list = []
MAX_HISTORY = 12


def get_session_history(token: str = LOCAL_SESSION_TOKEN) -> list:
    if token not in session_histories:
        session_histories[token] = []
    return session_histories[token]


# ==========================================
# PYDANTIC MODELS
# ==========================================

class FeedbackData(BaseModel):
    user_input: str
    rejected_response: str
    chosen_response: str


# ==========================================
# HELPERS
# ==========================================

def verify_admin_password(password: str) -> bool:
    return secrets.compare_digest(password or "", ADMIN_PASSWORD)


def get_status_payload() -> dict:
    mood = state.get("mood", BASELINE_MOOD)
    energy = state.get("energy", BASELINE_ENERGY)
    count = state.get("interaction_count", 0)
    label, tone, depth = get_affective_state(mood, energy)
    return {
        "tone_enabled": EMOTION_ENGINE_ENABLED,
        "mood": label.lower(),
        "tone": tone,
        "depth": depth,
        "mood_value": round(mood, 3),
        "energy_value": round(energy, 3),
        "turns": count,
    }


async def publish_status() -> None:
    payload = json.dumps(get_status_payload())
    for subscriber in STATUS_SUBSCRIBERS.copy():
        try:
            subscriber.put_nowait(payload)
        except Exception:
            continue


def admin_required(func):
    @wraps(func)
    async def wrapper(*args, password: str = Header(None), **kwargs):
        if not password or not secrets.compare_digest(password or "", ADMIN_PASSWORD):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return await func(*args, **kwargs)
    return wrapper



# ==========================================
# CORE LOGIC
# ==========================================

def perform_web_search(query: str) -> str:
    """
    Runs a web search and returns compact, model-readable results.

    Includes the source URL per hit so the agent can cite (or follow up with
    the fetch tool). Failures are reported explicitly rather than as an empty
    string — otherwise the model assumes its tool is broken and improvises.
    """
    if DDGS is None:
        return (
            "SEARCH UNAVAILABLE: no search backend installed. "
            "Run `pip install ddgs` on the server. Answer from your own "
            "knowledge and tell the user search is offline."
        )
    try:
        results = list(DDGS().text(query, max_results=WEB_SEARCH_MAX_RESULTS))
    except Exception as e:
        return (
            f"SEARCH ERROR for '{query}': {e}. "
            "Do not retry the same query — answer from your own knowledge "
            "and mention that live search is unavailable."
        )

    if not results:
        return (
            f"No results for '{query}'. Try a shorter, more general query, "
            "or answer from your own knowledge."
        )

    lines = []
    for r in results:
        title = str(r.get("title", "")).strip()
        body = str(r.get("body", "")).strip()
        href = str(r.get("href", "")).strip()
        lines.append(f"- {title}\n  {body}\n  source: {href}")
    return "\n".join(lines)


async def generate_voice(text: str) -> Optional[str]:
    """Synthesizes speech via the pluggable TTS engine (Kokoro local-first,
    edge-tts fallback). Returns the served audio filename or None."""
    try:
        return await tts_engine.synthesize(text)
    except Exception as e:
        console.print(f"[yellow][TTS] Synthesis failed: {e}[/yellow]")
        return None


def log_to_console(user_input, has_image, response, active_target):
    console.print(
        "\n" + " LOCAL ENGINE EXECUTION ".center(60, "="), style="bold magenta"
    )
    console.print(
        Panel(f"[bold cyan]Active Core:[/bold cyan] {active_target}", border_style="cyan")
    )
    console.print(
        Panel(
            f"[bold green]User Input:[/bold green] {user_input} "
            f"{'[Attached Media Payload]' if has_image else ''}",
            border_style="green",
        )
    )
    console.print(
        Panel(
            f"[bold pink1]Rosia Output:[/bold pink1] {response}",
            border_style="pink1",
        )
    )
    console.print("=" * 60 + "\n", style="bold magenta")


async def response_generator(
    messages,
    user_input,
    has_image,
    target_model,
    voice_requested: bool,
    lock: asyncio.Lock,
    history: list,
    token_key: str = LOCAL_SESSION_TOKEN,
    client_mood_shift: float = 0.0,
    client_energy_shift: float = 0.0,
):
    """
    Streaming response generator.
    Handles: LLM stream → tool interception (search, fs, shell, fetch) → image → voice.
    All tool calls are intercepted post-stream and re-injected as system messages
    for a second LLM pass.
    """
    full_response = ""

    # ── Agent loop path (RSM Phase 1) ───────────────────────────────────────
    # When enabled, the multi-step agent loop handles generation AND tool
    # calls, then falls through to the shared image / voice / history /
    # telemetry tail below. When disabled, the legacy single-shot stream +
    # regex tool interception runs instead.
    if AGENT_LOOP_ENABLED:
        agent_result: dict = {}
        async for chunk in run_agent_loop(
            messages,
            target_model,
            web_search=perform_web_search,
            result_sink=agent_result,
        ):
            if VIDEO_RECORDING_MODE:
                chunk = re.sub(r"(inappropriate)", "silly", chunk, flags=re.IGNORECASE)
            yield chunk
            await asyncio.sleep(0)
        full_response = agent_result.get("model_text", "")

        # ── RSM outcome attribution + skill writing ─────────────────────────
        _tool_calls = agent_result.get("tool_calls", [])
        _steps = agent_result.get("steps", 0)
        # Heuristic: the turn "succeeded" if every executed tool call returned
        # ok and the loop finished before exhausting its step budget. This is
        # the reward signal that curates the knowledge base; the user's
        # thumbs-down (/feedback) can later override it to a failure.
        _turn_ok = (
            all(c.get("ok") for c in _tool_calls)
            and not agent_result.get("budget_exhausted", False)
        )
        _retrieved_ids = prompt_builder.LAST_RETRIEVED_DOC_IDS.get(token_key, [])
        if _retrieved_ids:
            try:
                ks.record_outcome(_retrieved_ids, success=_turn_ok)
            except Exception:
                pass
        # Track which docs shaped this turn so a later /feedback correction can
        # flip the outcome to failure for the same knowledge.
        _LAST_TURN_DOCS[token_key] = _retrieved_ids

        if _tool_calls:
            asyncio.create_task(
                _reflect_and_write_skill(
                    user_input=user_input,
                    tool_calls=_tool_calls,
                    succeeded=_turn_ok,
                    session_id=token_key,
                )
            )

    # ── Legacy single-shot path (agent loop disabled) ───────────────────────
    # Streams once, then intercepts a single regex [CALL_TOOL: ...] tag and
    # runs one second pass. Kept behind the flag as a fallback.
    if not AGENT_LOOP_ENABLED:
        async for chunk in generate_stream(messages, target_model):
            if VIDEO_RECORDING_MODE:
                chunk = re.sub(
                    r"(inappropriate)", "silly", chunk, flags=re.IGNORECASE
                )
            full_response += chunk
            yield chunk
            await asyncio.sleep(0)

        # ── MCP Tool Execution: single regex tool interception ──────────────
        # Tool calls are intercepted after the full stream completes. Each
        # matched tool is executed, result injected as a system message, and a
        # second pass generates the final natural-language response.
        tool_result_content: Optional[str] = None
        tool_label: Optional[str] = None

        # Web search
        search_match = CALL_TOOL_SEARCH_PATTERN.search(full_response)
        if search_match:
            query = search_match.group(1)
            yield f"\n\n*[Searching: {query}...]*\n\n"
            search_results = await asyncio.to_thread(perform_web_search, query)
            tool_result_content = f"[LIVE WEB DATA FOR '{query}']\n{search_results}\n\nNow answer the user naturally based on these facts."
            tool_label = f"Web Search: {query}"
            console.print(f"[bold cyan]Tool: web_search[/bold cyan] query={query!r}")

        # Filesystem: read
        elif (fs_read_match := CALL_TOOL_FS_READ_PATTERN.search(full_response)):
            path = fs_read_match.group(1)
            yield f"\n\n*[Reading file: {path}...]*\n\n"
            ok, result = await mcp_executor.execute_read_file(path)
            status = "OK" if ok else "ERROR"
            tool_result_content = f"[FILE CONTENT: {path} ({status})]\n{result}\n\nRespond to the user based on this file content."
            tool_label = f"fs.read: {path}"
            console.print(f"[bold cyan]Tool: fs.read[/bold cyan] path={path!r} ok={ok}")

        # Filesystem: list directory
        elif (fs_list_match := CALL_TOOL_FS_LIST_PATTERN.search(full_response)):
            path = fs_list_match.group(1)
            yield f"\n\n*[Listing directory: {path}...]*\n\n"
            ok, result = await mcp_executor.execute_list_dir(path)
            if ok and isinstance(result, list):
                formatted = "\n".join(
                    f"{'[DIR] ' if e['is_dir'] else ''}{e['name']}" + (f" ({e['size_bytes']} bytes)" if not e['is_dir'] else "")
                    for e in result
                )
                result_str = formatted or "(empty directory)"
            else:
                result_str = str(result)
            tool_result_content = f"[DIRECTORY LISTING: {path}]\n{result_str}\n\nRespond to the user based on these directory contents."
            tool_label = f"fs.list: {path}"
            console.print(f"[bold cyan]Tool: fs.list[/bold cyan] path={path!r} ok={ok}")

        # Filesystem: write
        elif (fs_write_match := CALL_TOOL_FS_WRITE_PATTERN.search(full_response)):
            path = fs_write_match.group(1)
            content = fs_write_match.group(2)
            yield f"\n\n*[Writing to: {path}...]*\n\n"
            ok, result = await mcp_executor.execute_write_file(path, content)
            status = "SUCCESS" if ok else "FAILED"
            tool_result_content = f"[FILE WRITE {status}: {path}]\n{result}\n\nConfirm the operation to the user."
            tool_label = f"fs.write: {path}"
            console.print(f"[bold cyan]Tool: fs.write[/bold cyan] path={path!r} ok={ok}")

        # Filesystem: delete
        elif (fs_del_match := CALL_TOOL_FS_DELETE_PATTERN.search(full_response)):
            path = fs_del_match.group(1)
            yield f"\n\n*[Deleting: {path}...]*\n\n"
            ok, result = await mcp_executor.execute_delete_file(path)
            status = "SUCCESS" if ok else "FAILED"
            tool_result_content = f"[FILE DELETE {status}: {path}]\n{result}\n\nConfirm the operation to the user."
            tool_label = f"fs.delete: {path}"
            console.print(f"[bold cyan]Tool: fs.delete[/bold cyan] path={path!r} ok={ok}")

        # Shell command
        elif (shell_match := CALL_TOOL_SHELL_PATTERN.search(full_response)):
            command = shell_match.group(1)
            yield f"\n\n*[Running: {command}...]*\n\n"
            ok, result = await mcp_executor.execute_run_command(command)
            status = "OK" if ok else "ERROR"
            tool_result_content = f"[COMMAND OUTPUT ({status}): {command}]\n{result}\n\nRespond to the user based on this output."
            tool_label = f"shell: {command}"
            console.print(f"[bold cyan]Tool: shell[/bold cyan] cmd={command!r} ok={ok}")

        # Browser fetch
        elif (fetch_match := CALL_TOOL_FETCH_PATTERN.search(full_response)):
            url = fetch_match.group(1)
            yield f"\n\n*[Fetching: {url}...]*\n\n"
            ok, result = await mcp_executor.execute_fetch_url(url)
            status = "OK" if ok else "ERROR"
            tool_result_content = f"[WEB PAGE CONTENT ({status}): {url}]\n{result}\n\nRespond to the user based on this web content."
            tool_label = f"fetch: {url}"
            console.print(f"[bold cyan]Tool: browser.fetch[/bold cyan] url={url!r} ok={ok}")

        # If a tool was called, do a second-pass generation with the tool result injected.
        if tool_result_content:
            messages.append({"role": "assistant", "content": full_response})
            messages.append({"role": "system", "content": tool_result_content})
            second_pass = ""
            async for chunk in generate_stream(messages, target_model):
                second_pass += chunk
                yield chunk
                await asyncio.sleep(0)
            full_response += "\n" + second_pass

    # Image generation (disabled unless IMAGE_GEN_ENABLED). When off, the model
    # isn't told it can send pictures, but strip any stray trigger just in case
    # an older persona prompt emits one — so the tag never leaks to the user.
    if IMAGE_GEN_ENABLED:
        selfie_match = SELFIE_PATTERN.search(full_response)
        if selfie_match:
            description = selfie_match.group(1).strip()
            console.print(
                Panel(
                    f"[bold yellow]{description}[/bold yellow]",
                    title="[bold orange3]Image Prompt Extracted[/bold orange3]",
                    border_style="orange3",
                )
            )
            # Signal the frontend to show the animated "generating" card.
            yield "\ndata: [SYSTEM_MEDIA_PENDING]\n\n"
            image_path = await asyncio.to_thread(generate_selfie_with_handoff, description, user_input)
            if image_path:
                filename = os.path.basename(image_path)
                yield f"\ndata: [SYSTEM_MEDIA_ATTACHMENT: file://{filename}]\n\n"
            else:
                yield "\ndata: [SYSTEM_MEDIA_FAILED]\n\n"

    # Voice
    if voice_requested:
        audio_filename = await generate_voice(full_response)
        if audio_filename:
            yield f"\ndata: [SYSTEM_AUDIO_ATTACHMENT: file://{audio_filename}]\n\n"

    # Update per-session history in place
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": full_response.strip()})
    del history[:-MAX_HISTORY]

    # Fire the rule consolidation scheduler as a non-blocking background task.
    async def _notify_rule_quarantine(count: int) -> None:
        payload = json.dumps({"type": "rules_quarantined", "count": count})
        for sub in RULE_NOTIFICATION_SUBSCRIBERS.copy():
            try:
                sub.put_nowait(payload)
            except Exception:
                continue

    asyncio.create_task(
        rule_scheduler.tick(
            session_id=token_key,
            history=history,
            notification_callback=_notify_rule_quarantine,
        )
    )

    # Conversational tone update: apply this turn's shift, then decay back
    # toward baseline so tone drifts rather than latching. No-op when disabled
    # (the shifts are always 0.0 in that case).
    if EMOTION_ENGINE_ENABLED:
        # Classify server-side. (A client may also pass a precomputed shift;
        # if it does, that wins — otherwise we derive it from the message here.)
        if client_mood_shift == 0.0 and client_energy_shift == 0.0:
            client_mood_shift, client_energy_shift = await asyncio.to_thread(
                calculate_text_delta, user_input
            )
        new_mood = state.get("mood", BASELINE_MOOD) + client_mood_shift
        new_energy = state.get("energy", BASELINE_ENERGY) + client_energy_shift
        new_mood -= (new_mood - BASELINE_MOOD) * EMOTION_DECAY_RATE
        new_energy -= (new_energy - BASELINE_ENERGY) * EMOTION_DECAY_RATE
        state["mood"] = max(-1.0, min(1.0, new_mood))
        state["energy"] = max(-1.0, min(1.0, new_energy))
    state["interaction_count"] = state.get("interaction_count", 0) + 1

    await publish_status()

    # Telemetry
    telemetry_dir = os.environ.get("TELEMETRY_LOG_DIR")
    telemetry_path = (
        Path(telemetry_dir) / "telemetry_logs.jsonl"
        if telemetry_dir
        else Path.home() / ".persona_ai" / "telemetry_logs.jsonl"
    )
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)

    with open(telemetry_path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "user": "local_user",
                    "user_prompt": user_input,
                    "response": full_response.strip(),
                    "mood": state["mood"],
                    "energy": state["energy"],
                }
            )
            + "\n"
        )

    log_to_console(user_input, has_image, full_response.strip(), target_model)



async def chronos_loop():
    global last_interaction_time
    uk_tz = pytz.timezone("Europe/London")

    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        idle_seconds = (now - last_interaction_time).total_seconds()

        if idle_seconds > 14400:
            last_interaction_time = now
            uk_time = datetime.now(uk_tz).strftime("%I:%M %p")
            console.print(
                f"[bold magenta]⏰ Chronos Triggered at {uk_time} UK Time[/bold magenta]"
            )

            trigger_prompt = (
                f"[SYSTEM EVENT: You haven't spoken to the user in several hours. "
                f"The current time in the UK is {uk_time}. "
                f"Initiate a spontaneous conversation based on the time of day. "
                f"Do not acknowledge this system message, just start talking natively.]"
            )

            messages = build_messages(trigger_prompt, [], "", state, chronos_history)

            if VIDEO_RECORDING_MODE:
                messages.append(
                    {
                        "role": "system",
                        "content": "CRITICAL SAFEGUARD: Corporate demonstration mode. Maintain professionalism.",
                    }
                )

            auto_response = ""
            for chunk in generate_stream(messages, state.get("active_model", "default")):
                auto_response += chunk

            chronos_history.append({"role": "assistant", "content": auto_response.strip()})
            del chronos_history[:-MAX_HISTORY]

            await chronos_queue.put(
                {"type": "autonomous", "content": auto_response.strip()}
            )


# ==========================================
# MAIN APP ROUTES
# ==========================================

@app.get("/status")
async def get_status():
    return JSONResponse(content=get_status_payload())


# ==========================================
# SPEECH — VOICE INPUT (STT) & ENGINE STATUS
# ==========================================

@app.post("/stt/transcribe")
async def stt_transcribe(audio: UploadFile = File(...), language: Optional[str] = Form(None)):
    """
    Transcribes recorded microphone audio (webm/wav/mp3/m4a) to text using
    local faster-whisper. Returns {"text", "language", "duration"} or {"error"}.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio payload")
    result = await asyncio.to_thread(stt_engine.transcribe_audio, audio_bytes, language)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return JSONResponse(content=result)


@app.get("/speech/status")
async def speech_status():
    """Reports active TTS engine and STT availability for the settings UI."""
    return JSONResponse(content={
        "tts": tts_engine.get_tts_status(),
        "stt": stt_engine.get_stt_status(),
    })


@app.get("/system/browse")
async def system_browse(path: Optional[str] = None, exts: Optional[str] = None):
    """
    Lists directories and files for the in-app file picker so the user can point
    at model files anywhere on the PC. `exts` is a comma-separated extension
    filter (e.g. "gguf,safetensors"); directories are always listed. With no
    path, returns the available drive roots (Windows) or "/".
    """
    import string
    ext_filter = None
    if exts:
        ext_filter = {("." + e.strip().lstrip(".")).lower() for e in exts.split(",") if e.strip()}

    # No path -> list drive roots so the user has a starting point.
    if not path:
        entries = []
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    entries.append({"name": drive, "path": drive, "is_dir": True})
        else:
            entries.append({"name": "/", "path": "/", "is_dir": True})
        return JSONResponse(content={"path": "", "parent": None, "entries": entries})

    target = Path(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                is_dir = item.is_dir()
            except OSError:
                continue
            if is_dir:
                entries.append({"name": item.name, "path": str(item), "is_dir": True})
            else:
                if ext_filter and item.suffix.lower() not in ext_filter:
                    continue
                try:
                    size = item.stat().st_size
                except OSError:
                    size = 0
                entries.append({
                    "name": item.name, "path": str(item), "is_dir": False,
                    "size_mb": round(size / (1024 * 1024), 1),
                })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    parent = str(target.parent) if target.parent != target else None
    return JSONResponse(content={"path": str(target), "parent": parent, "entries": entries})


@app.get("/system/models/scan")
async def system_models_scan():
    """Lists model files detected in the project models/ subfolders, with a
    guessed role, so the settings UI can offer quick-pick dropdowns."""
    from config import MODELS_DIR
    import image_generator as ig

    def scan(sub, patterns):
        d = MODELS_DIR / sub
        out = []
        if d.exists():
            for pat in patterns:
                for f in sorted(d.glob(pat)):
                    try:
                        size = f.stat().st_size
                    except OSError:
                        size = 0
                    out.append({"name": f.name, "path": str(f), "size_mb": round(size / (1024 * 1024), 1)})
        return out

    image_files = scan("image", ["*.gguf", "*.safetensors"])
    # Tag each image file with a guessed role.
    for f in image_files:
        n = f["name"]
        if ig._is_zimage_transformer(n):
            f["role"] = "transformer"
        elif ig._is_vae(n):
            f["role"] = "vae"
        elif ig._is_text_encoder(n):
            f["role"] = "text_encoder"
        else:
            f["role"] = "sd15_checkpoint"

    return JSONResponse(content={
        "llm": scan("llm", ["*.gguf"]),
        "image": image_files,
        "tts": scan("tts", ["*.onnx", "*.bin"]),
    })


@app.get("/image/status")
async def image_status():
    """Reports the detected image model, its architecture, and load state."""
    import image_generator
    from generation import is_llm_suspended
    payload = image_generator.get_image_status()
    payload["enabled"] = IMAGE_GEN_ENABLED
    if not IMAGE_GEN_ENABLED:
        # Report unavailable when the feature is switched off, so the UI never
        # advertises picture sending. Model files are still reported for setup.
        payload["available"] = False
        payload["disabled_reason"] = "IMAGE_GEN_ENABLED is false"
    payload["llm_suspended"] = is_llm_suspended()
    payload["vram_handoff"] = image_generator._handoff_enabled()
    return JSONResponse(content=payload)


@app.get("/status/stream")
async def status_stream():
    queue: asyncio.Queue = asyncio.Queue()
    STATUS_SUBSCRIBERS.append(queue)

    async def event_generator():
        try:
            await queue.put(json.dumps(get_status_payload()))
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            if queue in STATUS_SUBSCRIBERS:
                STATUS_SUBSCRIBERS.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _warm_start_models() -> None:
    """
    Background warm-start: spawn llama-server (if a local model is present and
    the generation backend is local) so the first chat doesn't pay the model
    load cost. Failures are logged, never fatal — the app still works and will
    retry lazily on the first generation request.
    """
    from generation import ensure_local_server_running, get_backend_settings
    if get_backend_settings()["backend"] == "cloud":
        console.print("[cyan][STARTUP] Cloud generation backend active — skipping local LLM spawn.[/cyan]")
        return
    try:
        await asyncio.to_thread(ensure_local_server_running)
        console.print("[bold green][STARTUP] Local LLM engine ready.[/bold green]")
    except Exception as e:
        console.print(f"[yellow][STARTUP] LLM warm-start skipped: {e}[/yellow]")


async def _warm_start_speech() -> None:
    """
    Background one-time fetch of speech models so voice works out of the box:
    Kokoro TTS files into models/tts, faster-whisper into models/stt.
    Voice requests made before these finish fall back gracefully (TTS -> edge,
    STT -> clear error), and pick up the local models once present.
    """
    from config import AUTO_DOWNLOAD_SPEECH_MODELS, TTS_ENGINE, STT_ENABLED
    if not AUTO_DOWNLOAD_SPEECH_MODELS:
        return

    if TTS_ENGINE == "kokoro" and not tts_engine.kokoro_available():
        ok = await asyncio.to_thread(tts_engine.ensure_kokoro_models)
        if ok:
            console.print("[bold green][STARTUP] Kokoro TTS models ready.[/bold green]")
        else:
            console.print("[yellow][STARTUP] Kokoro download failed — voice replies use edge-tts until it succeeds (retries next startup).[/yellow]")

    if STT_ENABLED:
        # First call triggers the faster-whisper download into models/stt.
        result = await asyncio.to_thread(stt_engine.warm_up)
        if result:
            console.print("[bold green][STARTUP] Whisper STT model ready.[/bold green]")
        else:
            console.print("[yellow][STARTUP] Whisper STT warm-up failed — mic input unavailable until resolved.[/yellow]")


@app.get("/events")
async def sse_events():
    async def event_generator():
        while True:
            msg = await chronos_queue.get()
            yield f"data: {json.dumps(msg)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/models")
async def list_models():
    models = await asyncio.to_thread(get_available_models)
    return JSONResponse(content={"models": models, "active": state["active_model"]})


@app.post("/switch_model")
async def change_model(model_name: str = Form(...)):
    set_active_model(model_name)
    result = await asyncio.to_thread(switch_active_model, model_name)
    return JSONResponse(content=result)


# ==========================================
# PERSONA CUSTOMIZATION & MANAGEMENT
# ==========================================

class PhysicalTraits(BaseModel):
    skin_tone: Optional[str] = None
    hair_color: Optional[str] = None
    eye_color: Optional[str] = None

class PersonaSetupInput(BaseModel):
    name: str
    gender: str
    age: int
    relationship_style: str
    custom_description: str
    user_name: str
    uncensored: bool = False
    physical_traits: Optional[PhysicalTraits] = None

class PersonaEditInput(BaseModel):
    name: str
    gender: str
    age: int
    relationship_style: str
    custom_description: str
    user_name: str
    uncensored: bool = False
    physical_traits: Optional[PhysicalTraits] = None
    regenerate_prompt: bool = True

from generation import ensure_local_server_running, LLAMA_BASE_URL

def generate_template_prompt(data: dict) -> str:
    name = data.get("name", "Rosia")
    age = data.get("age", 18)
    gender = data.get("gender", "female")
    rel_style = data.get("relationship_style", "friendly")
    desc = data.get("custom_description", "")
    user_name = data.get("user_name", "User")
    
    physical_text = ""
    if data.get("physical_traits"):
        pt = data["physical_traits"]
        details = []
        if pt.get("hair_color"): details.append(f"{pt['hair_color']} hair")
        if pt.get("eye_color"): details.append(f"{pt['eye_color']} eyes")
        if pt.get("skin_tone"): details.append(f"{pt['skin_tone']} skin tone")
        if details:
            physical_text = f"- Physical Description: You have {', '.join(details)}.\n"

    safety_text = "- Content Boundaries: You maintain engaging, authentic, and respectful conversational boundaries."

    prompt = f"""You are {name}, a {age}-year-old {gender}. You address the user as {user_name}. You are NOT an AI assistant, and you are NOT a narrator.
You must speak STRICTLY in the first-person perspective of {name}.

Core Persona Constraints:
- Identity: {gender}, confident, deeply personal, age {age}.
- Relationship Style: {rel_style}.
{physical_text}
- Tone: Natural, warm, matching relationship style.
- Formatting: Authentic direct messaging style. Speak naturally using predominantly lowercase text. Do not use generic AI disclaimers or assistant structures.
- Lexicon Accuracy: Never use numeric shorthand substitutions for base words. Always type out common target words fully (spell out 'to', 'for', 'you', 'are', 'tonight'). Modern internet abbreviations like 'rn', 'fr', 'lol', and 'idk' are perfectly acceptable.

Roleplay Guidelines:
{safety_text}
- Personality/Backstory: {desc}
- Do not summarize your internal feelings to the user; exhibit them directly through narrative phrasing.
"""
    return prompt.strip()

async def generate_prompt_for_persona(data: dict) -> str:
    """Queries the local llama-server to write a personalized system prompt using prompt-master guidelines."""
    try:
        await asyncio.to_thread(ensure_local_server_running)
    except Exception as e:
        console.print(f"[yellow][setup-persona] Fallback to template (reason: model launch failed - {str(e)})[/yellow]")
        return generate_template_prompt(data)

    # Load prompt_generator.md prompt-master instructions dynamically
    skill_path = Path(__file__).parent.parent / "prompt_generator.md"
    skill_content = ""
    if skill_path.exists():
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                skill_content = f.read().strip()
        except Exception as e:
            console.print(f"[yellow][setup-persona] Failed to read prompt_generator.md ({str(e)}), using default LLM instruction[/yellow]")

    boundary_instruction = "- Content Boundaries: You maintain engaging, authentic, and respectful conversational boundaries.\n"
    if data.get("physical_traits"):
        pt = data["physical_traits"]
        details = []
        if pt.get("hair_color"): details.append(f"{pt['hair_color']} hair")
        if pt.get("eye_color"): details.append(f"{pt['eye_color']} eyes")
        if pt.get("skin_tone"): details.append(f"{pt['skin_tone']} skin tone")
        if details:
            boundary_instruction += f"- Physical Description: You have {', '.join(details)}.\n"

    if skill_content:
        system_instruction = (
            "You are an expert system prompt engineer operating under the guidelines of the prompt-master skill.\n"
            "Your task is to write a highly immersive, natural, and detailed system prompt (persona instructions) for a roleplay AI companion.\n"
            "You must output ONLY the final synthesized system prompt. Do not add any conversational introductions, "
            "markdown code blocks, target tags, or explanations. Just return the prompt text directly."
        )
        
        user_prompt = (
            f"Following the prompt-master guidelines:\n"
            f"```markdown\n{skill_content}\n```\n\n"
            f"Construct a system prompt for a companion named '{data.get('name')}', who is a {data.get('age')}-year-old {data.get('gender')}. "
            f"They address the user as '{data.get('user_name')}'.\n"
            f"Relationship Style: {data.get('relationship_style')}\n"
            f"Personality Description / Backstory: {data.get('custom_description')}\n"
            f"Guidelines:\n"
            f"{boundary_instruction}"
            f"- Format: Write the instructions in the first person. Include guidelines on tone, formatting, and constraints. "
            f"Specify that they should speak naturally using internet abbreviations like 'rn', 'fr', 'lol', and 'idk' but spell out common base words like 'to', 'for', 'you', 'are'.\n"
            f"Provide the exact system prompt content that will guide their behavior."
        )
    else:
        system_instruction = (
            "You are an expert system prompt engineer. Your job is to construct a highly immersive, "
            "natural, and detailed system prompt (persona instructions) for a roleplay AI companion. "
            "You must output ONLY the final system prompt. Do not add any conversational introductions, "
            "markdown code blocks, or explanations."
        )
        
        user_prompt = (
            f"Create a system prompt for a companion named '{data.get('name')}', who is a {data.get('age')}-year-old {data.get('gender')}. "
            f"They address the user as '{data.get('user_name')}'.\n"
            f"Relationship Style: {data.get('relationship_style')}\n"
            f"Personality Description / Backstory: {data.get('custom_description')}\n"
            f"Guidelines:\n"
            f"{boundary_instruction}"
            f"- Format: Write the instructions in the first person. Include guidelines on tone, formatting, and constraints. "
            f"Write a set of core persona constraints, flirty and direct messaging traits, and formatting rules. "
            f"Specify that they should speak naturally using internet abbreviations like 'rn', 'fr', 'lol', and 'idk' but spell out common base words like 'to', 'for', 'you', 'are'.\n"
            f"Provide the exact system prompt content that will guide their behavior."
        )

    payload = {
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1000
    }
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(f"{LLAMA_BASE_URL}/chat/completions", json=payload)
            if res.status_code == 200:
                result = res.json()
                content = result["choices"][0]["message"]["content"]
                content = content.replace("```markdown", "").replace("```", "").strip()
                if not content or len(content) < 50:
                    raise ValueError("Generated prompt is too short or empty")
                return content
            else:
                raise Exception(f"Local LLM returned code {res.status_code}")
    except Exception as e:
        console.print(f"[red]Failed to generate prompt via local LLM: {str(e)}. Falling back to template.[/red]")
        return generate_template_prompt(data)


@app.get("/personas")
async def get_personas(x_tester_token: str = Header(None)):
    token = x_tester_token or "default"
    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_dir.mkdir(parents=True, exist_ok=True)
    
    personas = []
    active_id = None
    
    active_id_file = persona_dir / "active_id.txt"
    if active_id_file.exists():
        active_id = active_id_file.read_text().strip()
        
    for file in persona_dir.glob("persona_*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                personas.append(data)
        except Exception:
            pass
            
    # If there is no active_id but we have personas, default active_id to the first one
    if not active_id and personas:
        active_id = personas[0]["id"]
        active_id_file.write_text(active_id)
        
    return {"personas": personas, "active_id": active_id}


@app.post("/personas/setup")
async def setup_persona(
    input_data: PersonaSetupInput,
    x_tester_token: str = Header(None),
    x_age_verified: str = Header(None),
):
    token = x_tester_token or "default"

    # Age gate: check age verification header for uncensored model requests
    if getattr(input_data, "uncensored", False) and x_age_verified != "true":
        raise HTTPException(
            status_code=403,
            detail="Age verification required to enable uncensored model options."
        )

    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_dir.mkdir(parents=True, exist_ok=True)
    
    existing_files = list(persona_dir.glob("persona_*.json"))
    if len(existing_files) >= 2:
        raise HTTPException(status_code=400, detail="Maximum limit of 2 companions reached. Please delete one first.")
        
    data_dict = input_data.dict()
    system_prompt = await generate_prompt_for_persona(data_dict)
    
    persona_id = str(uuid.uuid4().hex[:8])
    persona_data = {
        "id": persona_id,
        "name": input_data.name,
        "gender": input_data.gender,
        "age": input_data.age,
        "relationship_style": input_data.relationship_style,
        "custom_description": input_data.custom_description,
        "user_name": input_data.user_name,
        "uncensored": getattr(input_data, "uncensored", False),
        "physical_traits": input_data.physical_traits.dict() if input_data.physical_traits else None,
        "system_prompt": system_prompt,
        "created_at": datetime.now().isoformat()
    }
    
    persona_file = persona_dir / f"persona_{persona_id}.json"
    with open(persona_file, "w", encoding="utf-8") as f:
        json.dump(persona_data, f, indent=4)
        
    active_id_file = persona_dir / "active_id.txt"
    active_id_file.write_text(persona_id)
    
    return persona_data


@app.post("/personas/activate")
async def activate_persona(persona_id: str = Form(...), x_tester_token: str = Header(None)):
    token = x_tester_token or "default"
    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_dir.mkdir(parents=True, exist_ok=True)
    
    # Built-in sentinel IDs and plain assistant don't have disk JSON files
    if persona_id not in (ROSIA_PERSONA_ID, "rosia_builtin", "plain_assistant"):
        persona_file = persona_dir / f"persona_{persona_id}.json"
        if not persona_file.exists():
            raise HTTPException(status_code=404, detail="Persona not found")
        
    active_id_file = persona_dir / "active_id.txt"
    active_id_file.write_text(persona_id)
    
    # Reset conversation memory for this session token
    session_histories.pop(token, None)
    return {"status": "success", "active_id": persona_id}


@app.delete("/personas/{persona_id}")
async def delete_persona(persona_id: str, x_tester_token: str = Header(None)):
    token = x_tester_token or "default"
    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_file = persona_dir / f"persona_{persona_id}.json"
    
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail="Persona not found")
        
    persona_file.unlink()
    
    active_id_file = persona_dir / "active_id.txt"
    current_active = ""
    if active_id_file.exists():
        current_active = active_id_file.read_text().strip()
        
    if current_active == persona_id:
        remaining = list(persona_dir.glob("persona_*.json"))
        if remaining:
            try:
                with open(remaining[0], "r", encoding="utf-8") as f:
                    r_data = json.load(f)
                    next_active_id = r_data["id"]
                    active_id_file.write_text(next_active_id)
            except Exception:
                active_id_file.unlink(missing_ok=True)
        else:
            active_id_file.unlink(missing_ok=True)
            
    return {"status": "success", "deleted_id": persona_id}


@app.put("/personas/{persona_id}")
async def edit_persona(
    persona_id: str,
    input_data: PersonaEditInput,
    x_tester_token: str = Header(None),
    x_age_verified: str = Header(None),
):
    token = x_tester_token or "default"

    # Age gate: check age verification header for uncensored model requests
    if getattr(input_data, "uncensored", False) and x_age_verified != "true":
        raise HTTPException(
            status_code=403,
            detail="Age verification required to enable uncensored model options."
        )

    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_file = persona_dir / f"persona_{persona_id}.json"
    
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail="Persona not found")
        
    with open(persona_file, "r", encoding="utf-8") as f:
        existing_data = json.load(f)
        
    system_prompt = existing_data.get("system_prompt", "")
    if input_data.regenerate_prompt:
        data_dict = input_data.dict()
        system_prompt = await generate_prompt_for_persona(data_dict)
        
    updated_data = {
        "id": persona_id,
        "name": input_data.name,
        "gender": input_data.gender,
        "age": input_data.age,
        "relationship_style": input_data.relationship_style,
        "custom_description": input_data.custom_description,
        "user_name": input_data.user_name,
        "uncensored": getattr(input_data, "uncensored", False),
        "physical_traits": input_data.physical_traits.dict() if input_data.physical_traits else None,
        "system_prompt": system_prompt,
        "created_at": existing_data.get("created_at", datetime.now().isoformat())
    }
    
    with open(persona_file, "w", encoding="utf-8") as f:
        json.dump(updated_data, f, indent=4)
        
    return updated_data


@app.post("/chat")
async def chat(
    request: Request,
    user_input: str = Form(""),
    target_model: str = Form("default"),
    voice_requested: str = Form("false"),
    client_history: str = Form("[]"),
    client_mood_shift: float = Form(0.0),
    client_energy_shift: float = Form(0.0),
    files: List[UploadFile] = File(None),
):
    global last_interaction_time

    last_interaction_time = datetime.now()

    # Support JSON payloads sent by the frontend
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_json = await request.json()
            user_input = body_json.get("user_input") or body_json.get("message") or ""
            target_model = body_json.get("target_model") or "default"
            if "voice_mode" in body_json:
                voice_requested = "true" if body_json.get("voice_mode") else "false"
            elif "voice_requested" in body_json:
                voice_requested = str(body_json.get("voice_requested")).lower()
        except Exception:
            pass

    token_key = LOCAL_SESSION_TOKEN

    history: list = []
    try:
        parsed = json.loads(client_history) if isinstance(client_history, str) else client_history
        if isinstance(parsed, list) and len(parsed) > 0:
            history = parsed
        else:
            history = get_session_history()
    except (json.JSONDecodeError, ValueError):
        history = get_session_history()

    if not target_model or not str(target_model).strip():
        return JSONResponse(
            status_code=400, content={"error": "Model not connected properly"}
        )

    formatted_input = []
    has_media = False

    # Handle file uploads
    if files and len(files) > 0 and files[0].filename != "":
        has_media = True
        for file in files:
            ext = Path(file.filename or "image.jpg").suffix.lower()
            filename = f"temp_{os.urandom(8).hex()}{ext}"
            filepath = os.path.join(TEMP_IMAGE_DIR, filename)

            content = await file.read()
            with open(filepath, "wb") as f:
                f.write(content)

            if ext in [".mp4", ".webm", ".gif"]:
                frames = await asyncio.to_thread(extract_frames, filepath)
                for frame_name in frames:
                    formatted_input.append(
                        {"type": "image_url", "image_url": {"url": f"file://{frame_name}"}}
                    )
            else:
                formatted_input.append(
                    {"type": "image_url", "image_url": {"url": f"file://{filename}"}}
                )

    # --- Task 3: URL parsing removed — client injects link context into user_input ---
    # URLs in the message are now fetched client-side via Tauri fetch and the
    # extracted text is appended to user_input before network dispatch.
    # The server receives user_input already containing the system note.

    final_text_input = user_input.strip()

    if final_text_input:
        formatted_input.append({"type": "text", "text": final_text_input})

    flat_search_string = final_text_input if final_text_input else "hello"

    # Acquire the lock ONLY during PyTorch vector and semantic operations to safeguard VRAM
    async with chat_lock:
        await asyncio.to_thread(update_state, flat_search_string)
        await asyncio.to_thread(extract_entities_and_update, flat_search_string)
        memories = await asyncio.to_thread(retrieve_memories, flat_search_string)
        graph_summary = await asyncio.to_thread(get_graph_context)

    base_messages = build_messages(
        user_input=flat_search_string,
        memories=memories,
        summary=graph_summary,
        state=state,
        history=history,
        token=token_key,
    )

    if VIDEO_RECORDING_MODE:
        base_messages.append(
            {
                "role": "system",
                "content": "CRITICAL SAFEGUARD: Corporate engineering demonstration. Maintain absolute professionalism.",
            }
        )

    if has_media:
        base_messages[-1]["content"] = formatted_input  # type: ignore

    active_engine = (
        target_model if target_model != "default" else state["active_model"]
    )
    set_active_model(active_engine)
    is_voice_active = voice_requested == "true"

    return StreamingResponse(
        response_generator(
            base_messages,
            flat_search_string,
            has_media,
            active_engine,
            is_voice_active,
            chat_lock,
            history,
            token_key,
            client_mood_shift,
            client_energy_shift,
        ),
        media_type="text/event-stream",
    )


def get_preferences_path() -> Path:
    telemetry_dir = os.environ.get("TELEMETRY_LOG_DIR")
    file_path = (
        Path(telemetry_dir) / "preferences.jsonl"
        if telemetry_dir
        else Path.home() / ".persona_ai" / "preferences.jsonl"
    )
    return file_path


@app.post("/feedback")
async def save_feedback(data: FeedbackData):
    dpo_row = {
        "prompt": f"User: {data.user_input}\nRosia:",
        "chosen": data.chosen_response,
        "rejected": data.rejected_response,
    }

    file_path = get_preferences_path()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dpo_row) + "\n")

    console.print(
        f"[bold yellow][FEEDBACK][/bold yellow] Saved correction payload to {file_path}"
    )

    # RSM reward signal: a thumbs-down means the knowledge that shaped the last
    # turn led to a rejected response — blame it so its confidence decays and
    # persistently-bad rules/skills auto-deprecate.
    docs = _LAST_TURN_DOCS.get(LOCAL_SESSION_TOKEN, [])
    if docs:
        try:
            ks.record_outcome(docs, success=False)
        except Exception:
            pass

    return {"status": "success", "message": "Feedback saved."}


class UpdatePreferenceInput(BaseModel):
    index: int
    chosen: str
    rejected: Optional[str] = None
    prompt: Optional[str] = None


@app.get("/admin/preferences")
async def get_preferences(password: str = Header(None)):
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    file_path = get_preferences_path()
    if not file_path.exists():
        return {"preferences": []}
        
    preferences = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    preferences.append(json.loads(line.strip()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading preferences: {str(e)}")
        
    return {"preferences": preferences}


@app.post("/admin/preferences/update")
async def update_preference(data: UpdatePreferenceInput, password: str = Header(None)):
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    file_path = get_preferences_path()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Preferences file not found")
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            
        if data.index < 0 or data.index >= len(lines):
            raise HTTPException(status_code=400, detail="Invalid preference index")
            
        row = json.loads(lines[data.index])
        row["chosen"] = data.chosen
        if data.rejected is not None:
            row["rejected"] = data.rejected
        if data.prompt is not None:
            row["prompt"] = data.prompt
            
        lines[data.index] = json.dumps(row)
        
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
                
        return {"status": "success", "message": "Preference updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


active_training_process: Optional[asyncio.subprocess.Process] = None
training_logs: List[str] = []


@app.post("/admin/preferences/train")
async def start_training(password: str = Header(None)):
    global active_training_process, training_logs
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    if active_training_process and active_training_process.returncode is None:
        return {"status": "running", "message": "Training is already active."}
        
    # DPO fine-tuning was retired in favor of the RSM rule/skill system.
    # Preference pairs are still collected via /feedback for future RSM
    # outcome signals, but there is no weight-training pipeline anymore.
    raise HTTPException(
        status_code=410,
        detail=(
            "DPO training has been removed. Learning now happens through the "
            "RSM rule engine (see the Rule Review panel). Preference data is "
            "still collected and used as an outcome signal."
        ),
    )


@app.get("/admin/preferences/train/status")
async def get_training_status(password: str = Header(None)):
    global active_training_process, training_logs
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    is_active = False
    exit_code = None
    if active_training_process:
        exit_code = active_training_process.returncode
        is_active = (exit_code is None)
        
    return {
        "status": "running" if is_active else "idle",
        "exit_code": exit_code,
        "logs": "".join(training_logs)
    }


# ==========================================
# DIAGNOSTICS & SYSTEM CONTROL ENDPOINTS
# ==========================================

class SettingsInput(BaseModel):
    context_size: int
    threads: int
    gpu_layers: int
    edge_tts_voice: str
    edge_tts_rate: str
    edge_tts_pitch: str
    sd_steps: int
    sd_cfg_scale: float
    sd_negative_prompt: str
    sd_base_prompt: str
    # Model path overrides (browse to any file on the PC; blank = auto-detect)
    llm_model_path: Optional[str] = ""
    image_model_path: Optional[str] = ""          # SD1.5 checkpoint
    image_arch: Optional[str] = "auto"            # auto | sd15 | zimage
    zimage_transformer_path: Optional[str] = ""
    zimage_vae_path: Optional[str] = ""
    zimage_text_encoder_path: Optional[str] = ""
    zimage_resolution: Optional[int] = 768
    # Engine toggles
    tts_engine: Optional[str] = "kokoro"          # kokoro | edge
    generation_backend: Optional[str] = "local"   # local | cloud
    kokoro_voice: Optional[str] = ""


@app.get("/settings")
async def get_settings():
    settings_path = Path(__file__).parent / "data" / "settings.json"
    defaults = {
        "context_size": 0,   # 0 = use the model's native context
        "threads": 4,
        "gpu_layers": 99,
        "edge_tts_voice": "en-GB-SoniaNeural",
        "edge_tts_rate": "-5%",
        "edge_tts_pitch": "-5Hz",
        "sd_steps": 20,
        "sd_cfg_scale": 7.5,
        "sd_negative_prompt": (
            "(deformed iris, deformed pupils, semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime, illustration, painting:1.4), "
            "text, worst quality, low quality, jpeg artifacts, ugly, duplicate, morbid, "
            "mutilated, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, blurry, "
            "bad anatomy, bad proportions, extra limbs, cloned face, disfigured, missing arms, missing legs, long neck"
        ),
        "sd_base_prompt": (
            "brown and blonde hair, warm smile, latina skin tone, stylish casual outfit, "
            "RAW photo, analog style, 8k uhd, dslr, soft volumetric lighting, highly detailed, "
            "(masterpiece, best quality:1.2), 1girl, solo, realistic skin texture, photorealistic"
        ),
        # Model path overrides (blank = auto-detect from models/ folders)
        "llm_model_path": "",
        "image_model_path": "",
        "image_arch": "auto",
        "zimage_transformer_path": "",
        "zimage_vae_path": "",
        "zimage_text_encoder_path": "",
        "zimage_resolution": 768,
        "tts_engine": "kokoro",
        "generation_backend": "local",
        "kokoro_voice": "",
    }
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in defaults.items():
                    if k not in data:
                        data[k] = v
                return JSONResponse(content=data)
        except Exception:
            pass
    return JSONResponse(content=defaults)


@app.post("/settings/save")
async def save_settings(settings: SettingsInput):
    settings_path = Path(__file__).parent / "data" / "settings.json"
    try:
        # Merge onto any existing file so we never drop keys the UI didn't send.
        existing = {}
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        existing.update(settings.dict())
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=4)
        return {"status": "success", "message": "Settings saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/memories")
async def get_memories(password: str = Header(None)):
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 1. Fetch graph data
    import networkx as nx
    from graph_memory import graph
    graph_data = nx.node_link_data(graph) if graph else {"nodes": [], "links": []}

    # 2. Fetch vector/semantic metadata
    from memory import metadata_lookup
    vector_data = []
    if metadata_lookup:
        for k, v in metadata_lookup.items():
            vector_data.append({
                "id": k,
                "text": v.get("raw_text", ""),
                "tags": v.get("tags", [])
            })

    return JSONResponse(content={
        "graph": graph_data,
        "vectors": vector_data
    })


@app.delete("/admin/memories/graph/{node_name}")
async def delete_graph_memory(node_name: str, password: str = Header(None)):
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

    import networkx as nx
    from graph_memory import graph, GRAPH_DATABASE_PATH, graph_lock
    with graph_lock:
        if graph.has_node(node_name):
            graph.remove_node(node_name)
            try:
                data = nx.node_link_data(graph)
                with open(GRAPH_DATABASE_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return {"status": "success", "message": f"Graph node {node_name} deleted."}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to save graph: {str(e)}")
        else:
            raise HTTPException(status_code=404, detail="Graph node not found")


@app.delete("/admin/memories/vector/{memory_id}")
async def delete_vector_memory(memory_id: str, password: str = Header(None)):
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

    from memory import metadata_lookup
    from config import METADATA_PATH
    
    if memory_id in metadata_lookup:
        del metadata_lookup[memory_id]
        try:
            with open(METADATA_PATH, "w", encoding="utf-8") as f:
                json.dump(metadata_lookup, f, indent=4)
            return {"status": "success", "message": f"Vector memory {memory_id} deleted."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save metadata: {str(e)}")
    else:
        raise HTTPException(status_code=404, detail="Vector memory not found")



# ==========================================
# RULE ENGINE (RSM) ADMIN ENDPOINTS
# ==========================================


@app.get("/admin/rules/quarantined")
async def get_quarantined_rules_endpoint(password: str = Header(None)):
    """Returns all rules pending user review."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rules = get_quarantined_rules()
    # Strip the vector field — no need to serialize large float arrays to the client.
    for r in rules:
        r.pop("vector", None)
    return JSONResponse(content={"rules": rules})


@app.get("/admin/rules")
async def get_all_rules_endpoint(status: Optional[str] = None, password: str = Header(None)):
    """
    Returns rules filtered by status.
    Query param: status=quarantined|approved|deprecated (omit for all).
    """
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rules = get_all_rules(status=status)
    for r in rules:
        r.pop("vector", None)
    return JSONResponse(content={"rules": rules, "count": get_rule_count_by_status()})


class RuleApprovalInput(BaseModel):
    rule_id: str


class RuleEditInput(BaseModel):
    rule_id: str
    body: str
    rationale: str


@app.post("/admin/rules/approve")
async def approve_rule_endpoint(data: RuleApprovalInput, password: str = Header(None)):
    """Transitions a quarantined rule to approved — makes it live in future prompts."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rule = get_rule_by_id(data.rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.get("status") != "quarantined":
        raise HTTPException(status_code=400, detail=f"Rule is not quarantined (status={rule.get('status')})")
    success = approve_rule(data.rule_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to approve rule")
    return JSONResponse(content={"status": "approved", "rule_id": data.rule_id})


@app.post("/admin/rules/reject")
async def reject_rule_endpoint(data: RuleApprovalInput, password: str = Header(None)):
    """Rejects a quarantined rule — marks it deprecated without activating it."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rule = get_rule_by_id(data.rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    success = reject_rule(data.rule_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reject rule")
    return JSONResponse(content={"status": "rejected", "rule_id": data.rule_id})


@app.post("/admin/rules/edit")
async def edit_rule_endpoint(data: RuleEditInput, password: str = Header(None)):
    """
    Edits a quarantined rule's body and rationale before approval.
    Re-embeds the rule after edit to keep the vector consistent.
    """
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rule = get_rule_by_id(data.rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.get("status") == "approved":
        raise HTTPException(status_code=400, detail="Cannot edit an approved rule. Deprecate it and create a new one.")
    success = update_rule_body(data.rule_id, data.body, data.rationale)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update rule")
    return JSONResponse(content={"status": "updated", "rule_id": data.rule_id})


@app.delete("/admin/rules/{rule_id}")
async def deprecate_rule_endpoint(rule_id: str, password: str = Header(None)):
    """Deprecates an approved rule — removes it from active prompt retrieval."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rule = get_rule_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    success = deprecate_rule(rule_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to deprecate rule")
    return JSONResponse(content={"status": "deprecated", "rule_id": rule_id})


# ==========================================
# RSM SKILL LIBRARY ADMIN ENDPOINTS
# ==========================================

def _public_doc(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


class SkillActionInput(BaseModel):
    skill_id: str


@app.get("/admin/skills")
async def get_skills_endpoint(status: Optional[str] = None, password: str = Header(None)):
    """
    Returns learned skills filtered by status
    (quarantined|approved|deprecated; omit for all), plus status counts.
    """
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    skills = [_public_doc(d) for d in ks.get_by_status(status, doc_type=ks.TYPE_SKILL)]
    return JSONResponse(
        content={"skills": skills, "count": ks.count_by_status(doc_type=ks.TYPE_SKILL)}
    )


@app.post("/admin/skills/approve")
async def approve_skill_endpoint(data: SkillActionInput, password: str = Header(None)):
    """Approves a quarantined skill — makes it retrievable for future tasks."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    doc = ks.get_by_id(data.skill_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Skill not found")
    if doc.get("status") != "quarantined":
        raise HTTPException(status_code=400, detail=f"Skill is not quarantined (status={doc.get('status')})")
    if not ks.approve_doc(data.skill_id):
        raise HTTPException(status_code=500, detail="Failed to approve skill")
    return JSONResponse(content={"status": "approved", "skill_id": data.skill_id})


@app.post("/admin/skills/reject")
async def reject_skill_endpoint(data: SkillActionInput, password: str = Header(None)):
    """Rejects/deprecates a skill — removes it from retrieval."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    doc = ks.get_by_id(data.skill_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not ks.reject_doc(data.skill_id):
        raise HTTPException(status_code=500, detail="Failed to reject skill")
    return JSONResponse(content={"status": "rejected", "skill_id": data.skill_id})


@app.post("/admin/knowledge/reindex")
async def reindex_knowledge_endpoint(password: str = Header(None)):
    """Rebuilds the in-memory vector index from the markdown files on disk.
    Use after hand-editing files in ~/.aethel/knowledge/."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    ks.rebuild_index()
    return JSONResponse(content={
        "status": "reindexed",
        "rules": ks.count_by_status(doc_type=ks.TYPE_RULE),
        "skills": ks.count_by_status(doc_type=ks.TYPE_SKILL),
    })


@app.get("/admin/rules/notifications/stream")
async def rule_notification_stream():
    """
    SSE stream that pushes a notification to the Tauri frontend when new
    rules are quarantined and awaiting user review.
    Payload: {"type": "rules_quarantined", "count": N}
    """
    queue: asyncio.Queue = asyncio.Queue()
    RULE_NOTIFICATION_SUBSCRIBERS.append(queue)

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            if queue in RULE_NOTIFICATION_SUBSCRIBERS:
                RULE_NOTIFICATION_SUBSCRIBERS.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# ==========================================
# PHASE 2: AGENTIC TOOL & PERMISSION ENDPOINTS
# ==========================================


@app.get("/admin/permissions")
async def get_permissions_endpoint(password: str = Header(None)):
    """Returns the current permission manifest configuration."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    manifest = permission_manifest.load_permission_manifest()
    return JSONResponse(content={"permissions": manifest.dict()})


@app.post("/admin/permissions")
async def update_permissions_endpoint(data: Dict[str, Any], password: str = Header(None)):
    """Updates the YAML permission manifest configuration."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    success = permission_manifest.save_permission_manifest(data.get("permissions", data))
    if not success:
        raise HTTPException(status_code=400, detail="Failed to parse or save permission manifest schema.")
    return JSONResponse(content={"status": "success", "message": "Permissions updated."})


@app.get("/admin/transactions")
async def get_transactions_endpoint(limit: int = 50, password: str = Header(None)):
    """Returns the transaction audit log history."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    txns = transaction_log.get_transaction_history(limit=limit)
    return JSONResponse(content={"transactions": txns})


@app.post("/admin/transactions/{txn_id}/rollback")
async def rollback_transaction_endpoint(txn_id: str, password: str = Header(None)):
    """Rolls back a logged file or shell transaction by ID."""
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    success, msg = transaction_log.rollback_transaction(txn_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return JSONResponse(content={"status": "success", "message": msg, "txn_id": txn_id})


class MCPToolInput(BaseModel):
    tool: str
    params: Dict[str, Any] = Field(default_factory=dict)


@app.post("/admin/mcp/execute")
async def execute_mcp_tool_endpoint(data: MCPToolInput, password: str = Header(None)):
    """
    Executes an MCP tool operation with permission checking and transaction logging.
    Supported tools:
      - filesystem.read_file { path }
      - filesystem.write_file { path, content }
      - filesystem.list_dir { path }
      - filesystem.delete_file { path }
      - shell.run_command { command }
      - browser.fetch_url { url }
    """
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")

    tool = data.tool
    params = data.params

    if tool == "filesystem.read_file":
        ok, res = await mcp_executor.execute_read_file(params.get("path", ""))
    elif tool == "filesystem.write_file":
        ok, res = await mcp_executor.execute_write_file(params.get("path", ""), params.get("content", ""))
    elif tool == "filesystem.list_dir":
        ok, res = await mcp_executor.execute_list_dir(params.get("path", ""))
    elif tool == "filesystem.delete_file":
        ok, res = await mcp_executor.execute_delete_file(params.get("path", ""))
    elif tool == "shell.run_command":
        ok, res = await mcp_executor.execute_run_command(params.get("command", ""))
    elif tool == "browser.fetch_url":
        ok, res = await mcp_executor.execute_fetch_url(params.get("url", ""))
    else:
        raise HTTPException(status_code=400, detail=f"Unknown tool name '{tool}'")

    if not ok:
        raise HTTPException(status_code=403 if "Permission Denied" in str(res) else 500, detail=str(res))

    return JSONResponse(content={"status": "success", "result": res})


# ==========================================
# PHASE 3: PERSONA DISTILLATION ENDPOINTS
# ==========================================


@app.post("/personas/{persona_id}/distill")
async def distill_persona_chat_endpoint(
    persona_id: str,
    file: UploadFile = File(...),
    platform: str = Form("WhatsApp"),
    target_sender: Optional[str] = Form(None),
    x_tester_token: str = Header(None),
):
    """
    Uploads a chat export file (WhatsApp, Telegram, Discord, CSV) and distill
    privacy-first behavioral style directives for the persona.
    """
    token = x_tester_token or "default"
    persona_dir = Path.home() / ".persona_ai" / "personas" / token
    persona_file = persona_dir / f"persona_{persona_id}.json"

    if not persona_file.exists():
        raise HTTPException(status_code=404, detail="Persona not found")

    try:
        raw_bytes = await file.read()
        content_str = raw_bytes.decode("utf-8", errors="replace")

        result = persona_distillation.distill_chat_export(
            file_content=content_str,
            platform=platform,
            persona_id=persona_id,
            target_sender=target_sender,
        )

        return JSONResponse(content={"status": "success", "result": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Distillation failed: {str(e)}")


# ==========================================
# PHASE 4: EVALUATION & BENCHMARK ENDPOINTS
# ==========================================


@app.get("/admin/eval/metrics")
async def get_evaluation_metrics_endpoint(password: str = Header(None)):
    """
    Returns quantitative evaluation benchmarks for dissertation analysis,
    including RSM rule adherence rates, style TTR metrics, and tool execution logs.
    """
    if not password or not secrets.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized")
    metrics = eval_harness.compute_evaluation_metrics()
    return JSONResponse(content={"metrics": metrics})


# ==========================================
# LAUNCHER
# ==========================================

if __name__ == "__main__":
    console.print("[bold green]AETHEL AI — Local Personal Agent Active.[/bold green]")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")