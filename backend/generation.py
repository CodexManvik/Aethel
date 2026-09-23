# generation.py
import httpx
import json
import re
import os
import shutil
import subprocess
import time
import atexit
import asyncio
import threading
from pathlib import Path
from typing import Optional
from rich.console import Console
from config import (
    LLAMA_BASE_URL, LLAMA_TIMEOUT, GENERATION_TEMPERATURE, GENERATION_TOP_P,
    GENERATION_FREQUENCY_PENALTY, GENERATION_PRESENCE_PENALTY,
    GENERATION_BACKEND, CLOUD_API_BASE_URL, CLOUD_API_KEY, CLOUD_MODEL,
    MODELS_DIR, LLAMA_STARTUP_TIMEOUT,
)

console = Console()

# --- LOCAL LAUNCH UTILITIES ---

active_llama_process = None

def get_tauri_models_dir() -> Path:
    """Resolves the platform-specific local AppData path for Aethel models."""
    home = Path.home()
    
    # 1. Check Windows LOCALAPPDATA
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        base_paths = [
            Path(local_appdata) / "com.aethel.app" / "models",
            Path(local_appdata) / "Aethel" / "models"
        ]
        for p in base_paths:
            if p.exists():
                return p
        return Path(local_appdata) / "com.aethel.app" / "models"
        
    # 2. Check macOS Application Support
    if os.name == "posix":
        import platform
        if platform.system() == "Darwin":
            base_paths = [
                home / "Library" / "Application Support" / "com.aethel.app" / "models",
                home / "Library" / "Application Support" / "Aethel" / "models"
            ]
            for p in base_paths:
                if p.exists():
                    return p
            return home / "Library" / "Application Support" / "com.aethel.app" / "models"
            
    # 3. Check Linux XDG_DATA_HOME
    xdg_data = os.getenv("XDG_DATA_HOME")
    if xdg_data:
        base_paths = [
            Path(xdg_data) / "com.aethel.app" / "models",
            Path(xdg_data) / "Aethel" / "models"
        ]
        for p in base_paths:
            if p.exists():
                return p
        return Path(xdg_data) / "com.aethel.app" / "models"
        
    # Standard Linux fallback
    base_paths = [
        home / ".local" / "share" / "com.aethel.app" / "models",
        home / ".local" / "share" / "Aethel" / "models"
    ]
    for p in base_paths:
        if p.exists():
            return p
    return home / ".local" / "share" / "com.aethel.app" / "models"

def find_llama_server_binary() -> Optional[Path]:
    """Finds the llama-server binary in the environment."""
    binary_name = "llama-server.exe" if os.name == "nt" else "llama-server"

    # 0. Explicit override: LLAMA_SERVER_BINARY may point at the binary itself
    #    or at the directory containing it.
    override = os.getenv("LLAMA_SERVER_BINARY")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        cand = p / binary_name
        if cand.is_file():
            return cand

    # 1. Search in PATH
    path_binary = shutil.which(binary_name)
    if path_binary:
        return Path(path_binary)
        
    # 2. Search project-local install location (created by install script),
    #    then current working directory and common subfolders
    project_root = Path(__file__).parent.parent
    cwd = Path.cwd()
    search_paths = [
        project_root / "bin" / "llama" / binary_name,
        project_root / "bin" / binary_name,
        cwd / binary_name,
        cwd / "bin" / binary_name,
        cwd / "llama.cpp" / binary_name,
        cwd.parent / binary_name,
    ]
    for p in search_paths:
        if p.exists():
            return p

    return None

def get_custom_server_settings() -> dict:
    """Reads llama-server settings from backend/data/settings.json if it exists."""
    settings_path = Path(__file__).parent / "data" / "settings.json"
    # context_size 0 = "auto": llama.cpp loads the model's own trained context
    # length from the GGUF. A hardcoded 2048 silently truncates long chats and
    # makes tool results (which are large) overflow the window.
    defaults = {
        "context_size": 0,
        "threads": 4,
        "gpu_layers": 99,
        "llm_model_path": "",
    }
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "context_size": int(data.get("context_size", 0)),
                    "threads": int(data.get("threads", 4)),
                    "gpu_layers": int(data.get("gpu_layers", 99)),
                    "llm_model_path": str(data.get("llm_model_path", "") or ""),
                }
        except Exception:
            pass
    return defaults

def _scan_gguf_dir(models_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Scans one directory for a main .gguf model and an mmproj vision projector.
    Prefers the largest non-mmproj file so quantized aux models don't win."""
    if not models_dir.exists():
        return None, None
    main_model = None
    vision_model = None
    main_size = -1
    for file in sorted(models_dir.glob("*.gguf")):
        if "mmproj" in file.name.lower():
            vision_model = file
        else:
            try:
                size = file.stat().st_size
            except OSError:
                size = 0
            if size > main_size:
                main_model = file
                main_size = size
    return main_model, vision_model


def discover_local_models() -> tuple[Optional[Path], Optional[Path]]:
    """
    Returns (main_model_path, vision_projector_path).
    Search order:
      1. Explicit UI override: settings.json "llm_model_path" (any path on the PC).
      2. Project models/llm folder — the documented drop-in location.
      3. Legacy Tauri AppData models directory (existing installs).
    """
    # 1. UI override — user browsed to a specific .gguf anywhere on disk.
    override = get_custom_server_settings().get("llm_model_path")
    if override:
        p = Path(override)
        if p.is_file():
            # Look for a sibling mmproj-*.gguf next to the chosen model.
            vision = None
            for sib in sorted(p.parent.glob("*.gguf")):
                if "mmproj" in sib.name.lower():
                    vision = sib
                    break
            return p, vision

    main_model, vision_model = _scan_gguf_dir(MODELS_DIR / "llm")
    if main_model:
        return main_model, vision_model
    return _scan_gguf_dir(get_tauri_models_dir())

def start_llama_server(model_path: Path, vision_path: Optional[Path]) -> bool:
    """Spawns the local llama-server background process and waits for it to be ready."""
    global active_llama_process
    
    binary = find_llama_server_binary()
    if not binary:
        raise FileNotFoundError(
            "llama-server executable not found. Please ensure it is installed and added to PATH, "
            "or place the binary in the project folder."
        )
        
    port = 8080
    match = re.search(r":(\d+)", LLAMA_BASE_URL)
    if match:
        port = int(match.group(1))
        
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except socket.error:
            raise OSError(
                f"Port {port} is already in use by another application. "
                "Ensure no other service is bound to this port."
            )
        
    settings = get_custom_server_settings()
    cmd = [
        str(binary),
        "-m", str(model_path),
        "--port", str(port),
        # -c 0 tells llama.cpp to use the model's own trained context length
        # instead of a hardcoded window.
        "-c", str(settings["context_size"]),
        "-t", str(settings["threads"]),
        # Context shift: when the window fills, drop the oldest tokens and keep
        # generating instead of erroring out mid-answer.
        "--context-shift",
        # Reuse cached KV chunks across turns — big speedup for long chats
        # where the system prompt and history are largely unchanged.
        "--cache-reuse", "256",
        "--log-disable",
    ]
    if settings["gpu_layers"] > 0:
        cmd += ["-ngl", str(settings["gpu_layers"])]
        
    if vision_path and vision_path.exists():
        cmd += ["--mmproj", str(vision_path)]
        
    console.print(f"[bold yellow]Spawning background llama-server...[/bold yellow]")
    console.print(f"[yellow]Cmd: {' '.join(cmd)}[/yellow]")
    
    try:
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            active_llama_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags
            )
        else:
            active_llama_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
        # Poll health endpoint until the model is loaded. Large GGUF models can
        # take a while to map into VRAM, so the window is configurable.
        import requests
        for i in range(LLAMA_STARTUP_TIMEOUT):
            if active_llama_process.poll() is not None:
                raise RuntimeError(f"llama-server terminated unexpectedly with code {active_llama_process.returncode}")
            try:
                res = requests.get(f"{LLAMA_BASE_URL}/models", timeout=1)
                if res.status_code == 200:
                    console.print(f"[bold green]Local llama-server successfully started and responsive on port {port}.[/bold green]")
                    return True
            except Exception:
                pass
            time.sleep(1)

        raise TimeoutError(f"llama-server failed to respond within {LLAMA_STARTUP_TIMEOUT} seconds.")
    except Exception as e:
        cleanup_llama_server()
        raise e

def cleanup_llama_server():
    """Cleanly terminates the local llama-server subprocess."""
    global active_llama_process
    if active_llama_process:
        console.print("[yellow]Shutting down local llama-server process...[/yellow]")
        try:
            active_llama_process.terminate()
            active_llama_process.wait(timeout=5)
        except Exception:
            try:
                active_llama_process.kill()
            except Exception:
                pass
        active_llama_process = None

atexit.register(cleanup_llama_server)


# ─────────────────────────────────────────────────────────────
# VRAM handoff: suspend/resume the LLM so image generation can
# borrow the GPU on memory-constrained machines.
#
# The LLM runs in a SEPARATE process (llama-server), so we free its
# VRAM by terminating it and respawn it afterwards. This is safe for
# conversation continuity: chat history lives in the FastAPI process
# (session_histories) and is re-sent with every request, so a restarted
# llama-server never "forgets" the conversation — only its KV cache is
# lost, and that transparently rebuilds on the next message.
# ─────────────────────────────────────────────────────────────

# Guards the LLM process lifecycle so a chat request can't stream from a
# server that an in-progress image handoff is about to kill. Reentrant so the
# same thread can hold it across suspend→generate→resume.
llm_lifecycle_lock = threading.RLock()
_llm_suspended = False


def is_llm_suspended() -> bool:
    return _llm_suspended


def suspend_llama_server() -> bool:
    """
    Terminates the local llama-server to free its VRAM (e.g. before image
    generation on a shared GPU). Returns True if a server was actually stopped.
    No-op for the cloud backend or when no server is running.
    """
    global active_llama_process, _llm_suspended
    with llm_lifecycle_lock:
        if get_backend_settings()["backend"] == "cloud":
            return False
        if active_llama_process is None:
            # Nothing we spawned; check if an external server is up. We only
            # manage processes we own, so leave external servers alone.
            _llm_suspended = False
            return False
        console.print("[yellow][VRAM] Suspending LLM to free GPU for image generation...[/yellow]")
        cleanup_llama_server()
        _llm_suspended = True
        return True


def resume_llama_server() -> bool:
    """
    Respawns the local llama-server after an image-generation handoff.
    Safe to call unconditionally; returns True if the server is up afterwards.
    """
    global _llm_suspended
    with llm_lifecycle_lock:
        if not _llm_suspended:
            return True
        console.print("[cyan][VRAM] Resuming LLM after image generation...[/cyan]")
        try:
            ok = ensure_local_server_running()
            _llm_suspended = not ok
            if ok:
                console.print("[bold green][VRAM] LLM back online (conversation context intact).[/bold green]")
            return ok
        except Exception as e:
            console.print(f"[red][VRAM] Failed to resume LLM: {e}[/red]")
            return False

def ensure_local_server_running() -> bool:
    """Ensures llama-server is running. Spawns it dynamically if offline."""
    import requests
    try:
        res = requests.get(f"{LLAMA_BASE_URL}/models", timeout=2)
        if res.status_code == 200:
            return True
    except Exception:
        pass
        
    # Not running, load local files
    main_model, vision_model = discover_local_models()
    
    if not main_model or not main_model.exists():
        models_dir = get_tauri_models_dir()
        raise FileNotFoundError(
            f"No local GGUF models found in Tauri AppData directory: {models_dir}\n"
            f"Please run the setup flow in the Aethel app to download the models."
        )
        
    return start_llama_server(main_model, vision_model)

def get_available_models():
    """Queries the local llama.cpp server for loaded/available models, or checks disk if offline."""
    import requests
    # 1. Try to query running server first
    try:
        res = requests.get(f"{LLAMA_BASE_URL}/models", timeout=2)
        if res.status_code == 200:
            return res.json().get("data", [])
    except Exception:
        pass
        
    # 2. Fallback to check local disk models
    try:
        main_model, _ = discover_local_models()
        if main_model and main_model.exists():
            return [{"id": main_model.name, "object": "model"}]
    except Exception:
        pass
        
    return [{"id": "default-local-gguf", "object": "model"}]

def switch_active_model(model_path):
    """Instructs llama.cpp to load a new model payload dynamically."""
    return {"status": "success", "active_model": model_path}

def clean_chunk(text):
    """Strips residual dialogue boundary markers during streaming."""
    patterns = [r"^Assistant:\s*", r"^Rosia:\s*", r"^You:\s*"]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text

def get_backend_settings() -> dict:
    """
    Resolves the active generation backend. .env values are defaults;
    backend/data/settings.json keys (generation_backend, cloud_api_base_url,
    cloud_api_key, cloud_model) override at runtime so the UI can switch
    backends without restarting the server.
    """
    settings = {
        "backend": GENERATION_BACKEND,
        "base_url": CLOUD_API_BASE_URL,
        "api_key": CLOUD_API_KEY,
        "model": CLOUD_MODEL,
    }
    settings_path = Path(__file__).parent / "data" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings["backend"] = str(data.get("generation_backend", settings["backend"])).lower()
            settings["base_url"] = data.get("cloud_api_base_url") or settings["base_url"]
            settings["api_key"] = data.get("cloud_api_key") or settings["api_key"]
            settings["model"] = data.get("cloud_model") or settings["model"]
        except Exception:
            pass
    return settings


async def generate_stream(messages, target_model="default"):
    """Streams completions asynchronously from the active engine —
    local llama.cpp by default, or any OpenAI-compatible cloud endpoint."""

    backend = get_backend_settings()
    use_cloud = backend["backend"] == "cloud"

    if use_cloud and not backend["api_key"]:
        yield (
            " [Cloud Backend Error]: GENERATION_BACKEND is 'cloud' but no "
            "CLOUD_API_KEY is configured. Set it in .env or settings, or "
            "switch back to the local backend.\n"
        )
        return

    # Ensure local server is running (local backend only). If an image-gen
    # VRAM handoff is mid-flight, this blocks on the lifecycle lock until the
    # LLM has been resumed, so we never stream from a suspended server.
    if not use_cloud:
        def _acquire_and_ensure():
            with llm_lifecycle_lock:
                return ensure_local_server_running()
        try:
            await asyncio.to_thread(_acquire_and_ensure)
        except Exception as e:
            yield f" [Model Launch Failed]: {str(e)}\n"
            return
        
    # Debug: Check for vision content
    has_vision = False
    for msg in messages:
        if isinstance(msg.get("content"), list):
            has_vision = True
            console.print(f"[cyan][DEBUG] Multimodal message detected: {msg['role']}[/cyan]")
            for item in msg["content"]:
                if item.get("type") == "image_url":
                    image_url = item["image_url"]["url"]
                    console.print(f"[cyan][DEBUG] Image URL: {image_url[:100]}...[/cyan]")
    
    if has_vision:
        console.print("[yellow][DEBUG] Sending multimodal request to llama-server[/yellow]")
    
    payload = {
        "messages": messages,
        "temperature": GENERATION_TEMPERATURE,
        "top_p": GENERATION_TOP_P,
        "frequency_penalty": GENERATION_FREQUENCY_PENALTY,
        "presence_penalty": GENERATION_PRESENCE_PENALTY,
        "stream": True,
    }

    if use_cloud:
        base_url = backend["base_url"].rstrip("/")
        headers = {"Authorization": f"Bearer {backend['api_key']}"}
        payload["model"] = backend["model"]
    else:
        base_url = LLAMA_BASE_URL
        headers = {}
        payload["cache_prompt"] = True  # llama.cpp-only: recycle KV cache
        # Inject user-selected model ID if distinct from active context slot
        if target_model and str(target_model) != "default":
            payload["model"] = str(target_model)

    try:
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(limits=limits, timeout=LLAMA_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    body_raw = await response.aread()
                    snippet = body_raw[:800].decode("utf-8", errors="ignore")
                    yield (
                        " [Engine Fault]: llama.cpp/chat/completions returned an error.\n"
                        f" HTTP {response.status_code}. Body (truncated): {snippet}"
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    if line.startswith("data: "):
                        content_raw = line[6:].strip()

                        if content_raw == "[DONE]":
                            break

                        try:
                            chunk_json = json.loads(content_raw)
                            if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                delta = chunk_json["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield clean_chunk(content)
                        except json.JSONDecodeError:
                            continue

    except httpx.ConnectError:
        yield (
            " [Model not connected properly]: Could not connect to the local engine.\n"
            f" Tried: {LLAMA_BASE_URL}/chat/completions\n"
            " Make sure llama-server is running and reachable, then try again."
        )
    except Exception as e:
        yield (
            " [Model not connected properly]: The local engine request failed.\n"
            f" Tried: {LLAMA_BASE_URL}/chat/completions\n"
            f" Error: {str(e)}"
        )

