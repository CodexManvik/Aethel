"""
Configuration loader for Persona-AI
Handles all environment variables with sensible defaults
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from root directory
load_dotenv(Path(__file__).parent.parent / ".env")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _resolve_data_path(env_var: str, default_filename: str) -> str:
    val = os.getenv(env_var)
    if not val:
        return str(DATA_DIR / default_filename)
    p = Path(val)
    if p.is_absolute() and p.exists():
        return str(p)
    if p.exists():
        return str(p.resolve())
    clean_parts = [part for part in p.parts if part not in ("backend", "data")]
    if clean_parts:
        candidate = DATA_DIR / Path(*clean_parts)
        if candidate.exists():
            return str(candidate.resolve())
    return str(DATA_DIR / default_filename)

# ============================================
# 1. MODEL & ENGINE CONFIGURATION
# ============================================
# Project-local models directory — the one place users drop model files to get
# the app working end-to-end. Subfolders: llm/ tts/ stt/ image/
# Override with MODELS_DIR env var if models live elsewhere.
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(Path(__file__).parent.parent / "models")))
for _sub in ("llm", "tts", "stt", "image"):
    (MODELS_DIR / _sub).mkdir(parents=True, exist_ok=True)

LLAMA_BASE_URL = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1")
LLAMA_TIMEOUT = int(os.getenv("LLAMA_TIMEOUT", "120"))
LLAMA_STARTUP_TIMEOUT = int(os.getenv("LLAMA_STARTUP_TIMEOUT", "120"))

MODEL_PATH = os.getenv("MODEL_PATH", r"C:\AI\models\realismByStableYogi_sd15V9.safetensors")
IMAGE_GEN_TIMEOUT = int(os.getenv("IMAGE_GEN_TIMEOUT", "60"))

EMOTION_MODEL_DIR = os.getenv("EMOTION_MODEL_DIR", r"C:\AI\models\emotion_roberta_onnx")
EMOTION_TOKENIZER_NAME = os.getenv("EMOTION_TOKENIZER_NAME", "j-hartmann/emotion-english-distilroberta-base")
EMOTION_CPU_ONLY = os.getenv("EMOTION_CPU_ONLY", "true").lower() == "true"
EMOTION_DELTA_MULTIPLIER = float(os.getenv("EMOTION_DELTA_MULTIPLIER", "0.35"))

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")

# ============================================
# 2. MEMORY & DATABASE PATHS
# ============================================
TV_INDEX_PATH = _resolve_data_path("TV_INDEX_PATH", "turbovec_memory.tvim")
METADATA_PATH = _resolve_data_path("METADATA_PATH", "memory_metadata.json")
GRAPH_DATABASE_PATH = _resolve_data_path("GRAPH_DATABASE_PATH", "lancedb_data/relational_graph.json")
CLASSIFIED_MEMORIES_PATH = _resolve_data_path("CLASSIFIED_MEMORIES_PATH", "classified_memories.json")
PERSONALITY_DATASET_PATH = _resolve_data_path("PERSONALITY_DATASET_PATH", "personality_dataset.json")
PREFERENCES_PATH = _resolve_data_path("PREFERENCES_PATH", "preferences.jsonl")
MEMORY_TELEMETRY_PATH = _resolve_data_path("MEMORY_TELEMETRY_PATH", "telemetry_logs.jsonl")

# ============================================
# 3. RETRIEVAL & GRAPH ENGINE PARAMETERS
# ============================================
RETRIEVAL_INITIAL_K = int(os.getenv("RETRIEVAL_INITIAL_K", "8"))
RETRIEVAL_MAX_DOCS = int(os.getenv("RETRIEVAL_MAX_DOCS", "3"))
RETRIEVAL_FINAL_RETURN_COUNT = int(os.getenv("RETRIEVAL_FINAL_RETURN_COUNT", "3"))
TURBOVEC_BIT_WIDTH = int(os.getenv("TURBOVEC_BIT_WIDTH", "4"))

GRAPH_ENTITY_CAREER = os.getenv("GRAPH_ENTITY_CAREER", "career,work,job,company,project,office,boss,manager,salary,promotion")
GRAPH_ENTITY_FAMILY = os.getenv("GRAPH_ENTITY_FAMILY", "family,mom,dad,brother,sister,partner,wife,husband,son,daughter,child,parent")
GRAPH_ENTITY_LIFESTYLE = os.getenv("GRAPH_ENTITY_LIFESTYLE", "hobby,game,sport,music,movie,book,travel,vacation,food,restaurant,pet,dog,cat")
GRAPH_MIN_WEIGHT_THRESHOLD = float(os.getenv("GRAPH_MIN_WEIGHT_THRESHOLD", "1.0"))

# ============================================
# 4. GENERATION PARAMETERS
# ============================================
DEFAULT_ACTIVE_MODEL = os.getenv("DEFAULT_ACTIVE_MODEL", "uncensored")
DEFAULT_SYSTEM_PROMPT = os.getenv(
    "DEFAULT_SYSTEM_PROMPT",
    "You are Rosia, a highly intelligent, empathetic, and witty AI companion."
)
SYSTEM_PROMPT_MAX_LENGTH = int(os.getenv("SYSTEM_PROMPT_MAX_LENGTH", "1500"))
GEN_MAX_TOKENS = int(os.getenv("GEN_MAX_TOKENS", "512"))
GEN_TEMPERATURE = float(os.getenv("GEN_TEMPERATURE", "0.85"))
GEN_TOP_P = float(os.getenv("GEN_TOP_P", "0.9"))
GEN_REPETITION_PENALTY = float(os.getenv("GEN_REPETITION_PENALTY", "1.15"))
SUMMARY_MESSAGE_WINDOW = int(os.getenv("SUMMARY_MESSAGE_WINDOW", "10"))
HISTORY_WINDOW_SIZE = int(os.getenv("HISTORY_WINDOW_SIZE", "12"))

GENERATION_TEMPERATURE = GEN_TEMPERATURE
GENERATION_TOP_P = GEN_TOP_P
GENERATION_FREQUENCY_PENALTY = float(os.getenv("GENERATION_FREQUENCY_PENALTY", "0.0"))
GENERATION_PRESENCE_PENALTY = float(os.getenv("GENERATION_PRESENCE_PENALTY", "0.0"))

# ============================================
# 5. PERSONA & EMOTION CONFIGURATION
# ============================================
TIMEZONE = os.getenv("TIMEZONE", "Europe/London")
BASE_PERSONA_NAME = os.getenv("BASE_PERSONA_NAME", "Rosia")
BASE_PERSONA_AGE = os.getenv("BASE_PERSONA_AGE", "21")
BASE_PERSONA_LOCATION = os.getenv("BASE_PERSONA_LOCATION", "London, UK")
BASE_PERSONA_VOICE_PLATFORM = os.getenv("BASE_PERSONA_VOICE_PLATFORM", "Edge-TTS")

# Conversational tone tracking (optional feature — see emotion_engine.py).
# Off by default: the agent works identically without it and nothing loads.
EMOTION_ENGINE_ENABLED = os.getenv("EMOTION_ENGINE_ENABLED", "true").lower() == "true"

# Two dimensions: mood (unpleasant↔pleasant), energy (low↔high activation).
# BASELINE_VALENCE / BASELINE_AROUSAL are read as fallbacks so existing .env
# files keep working after the rename.
BASELINE_MOOD = float(os.getenv("BASELINE_MOOD", os.getenv("BASELINE_VALENCE", "0.0")))
BASELINE_ENERGY = float(os.getenv("BASELINE_ENERGY", os.getenv("BASELINE_AROUSAL", "0.0")))
MOOD_DESCRIPTION = os.getenv("MOOD_DESCRIPTION", "Balanced, receptive, and grounded.")
# How far each turn nudges tone, and how fast it decays back to baseline.
EMOTION_DECAY_RATE = float(os.getenv("EMOTION_DECAY_RATE", "0.05"))

# ============================================
# 5b. AGENT LOOP (RSM Phase 1)
# ============================================
# When enabled, chat requests run through the multi-step agent loop
# (agent_loop.py): generate → tool call → observe → repeat, with structured
# JSON tool calls. When disabled, the legacy single-shot regex tool
# interception in server.py handles tool calls unchanged.
AGENT_LOOP_ENABLED = os.getenv("AGENT_LOOP_ENABLED", "true").lower() == "true"
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))
AGENT_TOOL_RESULT_MAX_CHARS = int(os.getenv("AGENT_TOOL_RESULT_MAX_CHARS", "6000"))

# ============================================
# 5c. GENERATION BACKEND (local llama.cpp or cloud API)
# ============================================
# GENERATION_BACKEND: "local" (default) uses llama.cpp; "cloud" streams from
# any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, Groq,
# Anthropic's compatibility endpoint, a remote llama.cpp, ...).
# These are defaults; backend/data/settings.json keys of the same (lowercase)
# names override them at runtime so the UI can switch without a restart.
GENERATION_BACKEND = os.getenv("GENERATION_BACKEND", "local").lower()
CLOUD_API_BASE_URL = os.getenv("CLOUD_API_BASE_URL", "https://api.openai.com/v1")
CLOUD_API_KEY = os.getenv("CLOUD_API_KEY", "")
CLOUD_MODEL = os.getenv("CLOUD_MODEL", "gpt-4o-mini")

# ============================================
# 5d. SPEECH — TTS (voice out) & STT (voice in)
# ============================================
# TTS_ENGINE: "kokoro" (local, models/tts) | "edge" (cloud fallback)
# Kokoro needs models/tts/kokoro-v1.0.onnx + models/tts/voices-v1.0.bin;
# if they are missing the engine falls back to edge-tts automatically.
TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro").lower()
TTS_KOKORO_VOICE = os.getenv("TTS_KOKORO_VOICE", "af_heart")
TTS_KOKORO_SPEED = float(os.getenv("TTS_KOKORO_SPEED", "1.0"))

# Auto-fetch speech models on startup: Kokoro TTS files (~340 MB, one-time)
# and the faster-whisper STT model (~460 MB for 'small'). Runs in the
# background; voice features come online when each finishes. Set false to
# manage model files manually.
AUTO_DOWNLOAD_SPEECH_MODELS = os.getenv("AUTO_DOWNLOAD_SPEECH_MODELS", "true").lower() == "true"

# STT via faster-whisper; model auto-downloads into models/stt on first use.
STT_ENABLED = os.getenv("STT_ENABLED", "true").lower() == "true"
STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "small")
STT_DEVICE = os.getenv("STT_DEVICE", "cpu")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")

# ============================================
# 6. RULE ENGINE CONFIGURATION
# ============================================
RULE_ENGINE_TOP_K_RULES = int(os.getenv("RULE_ENGINE_TOP_K_RULES", "5"))
RULE_ENGINE_CONSOLIDATION_INTERVAL = int(os.getenv("RULE_ENGINE_CONSOLIDATION_INTERVAL", "5"))
RULE_ENGINE_MAX_RULES_PER_PASS = int(os.getenv("RULE_ENGINE_MAX_RULES_PER_PASS", "3"))
RULE_ENGINE_REFLECTION_TEMPERATURE = float(os.getenv("RULE_ENGINE_REFLECTION_TEMPERATURE", "0.35"))

# ============================================
# 6b. IMAGE GENERATION
# ============================================
# IMAGE_MODEL_ARCH: "auto" detects from the checkpoint (sd15 | zimage);
# override to force one. Z-Image Turbo may be a .gguf or .safetensors file;
# SD 1.5 is a .safetensors checkpoint.
# Master switch for image (selfie) generation. Off by default while the local
# GPU can't comfortably host an image model alongside the LLM. When false, the
# model is never told it can send pictures and any [TRIGGER_SELFIE] is ignored.
IMAGE_GEN_ENABLED = os.getenv("IMAGE_GEN_ENABLED", "false").lower() == "true"

IMAGE_MODEL_ARCH = os.getenv("IMAGE_MODEL_ARCH", "auto").lower()
# Z-Image base repo supplying the VAE/text-encoder/tokenizer that pair with a
# local GGUF/safetensors transformer (only the transformer is loaded locally).
ZIMAGE_BASE_REPO = os.getenv("ZIMAGE_BASE_REPO", "Tongyi-MAI/Z-Image-Turbo")
ZIMAGE_STEPS = int(os.getenv("ZIMAGE_STEPS", "8"))
ZIMAGE_GUIDANCE = float(os.getenv("ZIMAGE_GUIDANCE", "1.0"))  # 1.0 = effectively no CFG (Turbo)
ZIMAGE_WIDTH = int(os.getenv("ZIMAGE_WIDTH", "1024"))
ZIMAGE_HEIGHT = int(os.getenv("ZIMAGE_HEIGHT", "1024"))

# VRAM handoff: on a shared/small GPU, suspend the LLM (free its VRAM), run
# image generation, then resume the LLM. Conversation context survives the
# restart (history is held in the API process, not the LLM subprocess).
# "auto" enables the handoff only when MAX_VRAM_ALLOCATION <= the threshold.
IMAGE_GEN_VRAM_HANDOFF = os.getenv("IMAGE_GEN_VRAM_HANDOFF", "auto").lower()
IMAGE_GEN_HANDOFF_VRAM_THRESHOLD = int(os.getenv("IMAGE_GEN_HANDOFF_VRAM_THRESHOLD", "8"))

# ============================================
# 7. MEDIA & VISUAL CONFIGURATION
# ============================================
MEDIA_FRAMES_TO_EXTRACT = int(os.getenv("MEDIA_FRAMES_TO_EXTRACT", "5"))
MEDIA_MAX_TEXT_LENGTH = int(os.getenv("MEDIA_MAX_TEXT_LENGTH", "400"))
MEDIA_EXTENSIONS = set(os.getenv("MEDIA_EXTENSIONS", ".mp4,.webm,.gif,.jpg,.jpeg,.png").split(","))

# ============================================
# 8. SAFETY & CONTENT GUARDRAILS
# ============================================
ENABLE_CONTENT_GUARDRAILS = os.getenv("ENABLE_CONTENT_GUARDRAILS", "false").lower() == "true"
EXPLICIT_WORDS = set()
VIDEO_RECORDING_MODE = os.getenv("VIDEO_RECORDING_MODE", "false").lower() == "true"

# ============================================
# 9. PERFORMANCE & HARDWARE SAFEGUARDS
# ============================================
MAX_VRAM_ALLOCATION = int(os.getenv("MAX_VRAM_ALLOCATION", "4"))
CPU_THREAD_LIMIT = int(os.getenv("CPU_THREAD_LIMIT", "4"))
IMAGE_INFERENCE_STEPS = int(os.getenv("IMAGE_INFERENCE_STEPS", "20"))
IMAGE_GUIDANCE_SCALE = float(os.getenv("IMAGE_GUIDANCE_SCALE", "7.5"))
IMAGE_BASE_PROMPT = os.getenv(
    "IMAGE_BASE_PROMPT",
    "brown and blonde hair, warm smile, latina skin tone, stylish casual outfit, "
    "RAW photo, analog style, 8k uhd, dslr, soft volumetric lighting, highly detailed, "
    "(masterpiece, best quality:1.2), 1girl, solo, realistic skin texture, photorealistic"
)

IMAGE_NEGATIVE_PROMPT = os.getenv(
    "IMAGE_NEGATIVE_PROMPT",
    "(deformed iris, deformed pupils, semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime, illustration, painting:1.4), "
    "text, worst quality, low quality, jpeg artifacts, ugly, duplicate, morbid, "
    "mutilated, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, blurry, "
    "bad anatomy, bad proportions, extra limbs, cloned face, disfigured, missing arms, missing legs, long neck"
)
NORMALIZE_SLANG = os.getenv("NORMALIZE_SLANG", "true").lower() == "true"

# ============================================
# 10. SYSTEM INFRASTRUCTURE & BROKER CONFIG
# ============================================
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "*").split(",")
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "*").split(",")
CURRENT_RUNTIME_TUNNEL = os.getenv("CURRENT_RUNTIME_TUNNEL", "http://localhost:8000")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "3"))

TEMP_IMAGE_DIR = str(DATA_DIR / "temp_images")
os.makedirs(TEMP_IMAGE_DIR, exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "aethel_admin_password")
ADMIN_TOKEN_SECRET = os.getenv("ADMIN_TOKEN_SECRET", "aethel_secret_token")
REQUEST_STORAGE_PATH = str(DATA_DIR / "access_requests.json")
APPROVED_TOKENS_PATH = str(DATA_DIR / "approved_tokens.json")
MASTER_TOKEN = os.getenv("MASTER_TOKEN", "aethel_master_dev_token_2026")
