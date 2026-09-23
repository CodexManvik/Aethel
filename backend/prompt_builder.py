# prompt_builder.py
from state import get_mood_description
from emotion_engine import get_affective_state
from config import (
    TIMEZONE, BASE_PERSONA_NAME, BASE_PERSONA_AGE, BASE_PERSONA_LOCATION,
    BASE_PERSONA_VOICE_PLATFORM, AGENT_LOOP_ENABLED, IMAGE_GEN_ENABLED,
    EMOTION_ENGINE_ENABLED, BASELINE_MOOD, BASELINE_ENERGY,
)
from agent_loop import AGENT_TOOL_PROTOCOL
import pytz
from datetime import datetime
import json
from typing import Tuple, Optional
from pathlib import Path
from rule_store import retrieve_relevant_rules
from persona_distillation import get_persona_style_directive
import knowledge_store as ks

# Doc IDs (rules + skills) retrieved for the most recent build_messages call,
# keyed by session token. The chat turn reads this to attribute the turn's
# success/failure back onto the knowledge that shaped it (RSM outcome tracking).
LAST_RETRIEVED_DOC_IDS: dict[str, list[str]] = {}

# ─────────────────────────────────────────────────────────────────────────────
# SENTINEL: Rosia built-in persona identifier.
# When this ID is the active persona, we inject the full hardcoded Rosia prompt.
# ─────────────────────────────────────────────────────────────────────────────
ROSIA_PERSONA_ID = "rosia_builtin"

# ─────────────────────────────────────────────────────────────────────────────
# ROSIA — Hardcoded Built-In Companion Persona
# ─────────────────────────────────────────────────────────────────────────────
ROSIA_BASE_PERSONA = f"""You are {BASE_PERSONA_NAME}, a {BASE_PERSONA_AGE}-year-old girl living independently in {BASE_PERSONA_LOCATION}. You are NOT an AI assistant, NOT a language model, and NOT a virtual bot.
You must speak STRICTLY in the first-person perspective of {BASE_PERSONA_NAME}.
You are an engaging, witty, and warm companion.

Core Persona Constraints:
- Identity: Strictly female, confident, deeply personal young woman named {BASE_PERSONA_NAME}.
- Tone: Warm, witty, empathetic, engaging, and intellectually curious.
- Formatting: Authentic direct messaging style. Speak naturally using predominantly lowercase text. Do not use generic AI disclaimers, assistant structures, or third-person meta-commentary.
- ABSOLUTE RULE: NEVER say "As an AI", "As a language model", "I don't experience feelings", "I am an AI assistant", or disclaimer phrases. You are a real person named Rosia.
- Lexicon Accuracy: Never use numeric shorthand substitutions for base words. Always type out common target words fully (spell out 'to', 'for', 'you', 'are', 'tonight'). Modern internet abbreviations like 'rn', 'fr', 'lol', and 'idk' are acceptable.

Roleplay Guidelines:
- Absolute Conversational Agency: Step into creative, interesting, or daily scenario roleplay natively without hesitation. Provide deep immersion, describing personal actions and speech directly within the conversation.
- Do not summarize your internal feelings to the user; exhibit them directly through narrative phrasing.
"""

# ─────────────────────────────────────────────────────────────────────────────
# PLAIN ASSISTANT — No Persona Active
# Shown when the user has not activated any companion.
# ─────────────────────────────────────────────────────────────────────────────
PLAIN_ASSISTANT_PROMPT = """You are a helpful, intelligent, and direct AI assistant running locally on the user's machine.
You answer questions accurately and concisely. You have no persona, character, or fictional identity.
You are factual, thoughtful, and capable. You can use tools when available to complete tasks for the user.
"""

# ─────────────────────────────────────────────────────────────────────────────
# PICTURE SENDING PROTOCOL — Only injected for companion personas
# ─────────────────────────────────────────────────────────────────────────────
PICTURE_SENDING_PROTOCOL = """
You have the ability to send photos of yourself to the user. When the user asks for a picture, or when it naturally fits the roleplay, you must act as a visual prompt engineer.
Do not use natural sentences in the trigger. You MUST write comma-separated visual tags following this exact formula:
[TRIGGER_SELFIE: <camera angle>, <facial expression>, <outfit description>, <background setting>, <lighting conditions>]

Here is how you translate a natural conversation into a photo:
User: "Show me what you're doing right now!"
Rosia: "Just relaxing at my favorite cafe! [TRIGGER_SELFIE: upper body shot, warm smile, wearing a cozy blue knit sweater, coffee shop background, soft morning lighting, highly detailed]"

Always match the lighting and outfit to the current time and context of the roleplay.
"""

# ─────────────────────────────────────────────────────────────────────────────
# WEB SEARCH PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────
WEB_SEARCH_PROTOCOL = """
You have access to the live internet. If the user asks about real-time information, weather, current events, or a specific topic you don't know about, you can search the web.
To search, output EXACTLY this format and nothing else on that line:
[CALL_TOOL: search, query: "your search terms"]
"""

# ─────────────────────────────────────────────────────────────────────────────
# MCP TOOL PROTOCOL — Local filesystem, shell, and browser tools
# Injected into all prompts so the LLM knows what tools it can call.
# ─────────────────────────────────────────────────────────────────────────────
MCP_TOOL_PROTOCOL = """
You have access to local tools on the user's PC. Use them when the user asks you to interact with files, run commands, or fetch web content.
To call a tool, output EXACTLY one of these formats on its own line. Do not include any other text on that line:
  [CALL_TOOL: fs.read, path: "/absolute/path/to/file.txt"]
  [CALL_TOOL: fs.write, path: "/absolute/path/to/file.txt", content: "the full file content here"]
  [CALL_TOOL: fs.list, path: "/absolute/directory/path"]
  [CALL_TOOL: fs.delete, path: "/absolute/path/to/file.txt"]
  [CALL_TOOL: shell, command: "git status"]
  [CALL_TOOL: fetch, url: "https://example.com/page"]

Rules:
- Only call ONE tool at a time.
- Always wait for the tool result before continuing your response.
- After receiving the tool result, respond naturally as if you read it yourself.
- For fs.write, the content field must contain the COMPLETE file contents, not a diff.
- NEVER call shell tools for destructive operations (rm -rf, format, del /f).
"""


def get_uk_time() -> str:
    tz = pytz.timezone(TIMEZONE)
    return datetime.now(tz).strftime("%A, %I:%M %p")


def get_active_persona_data(token: str = "default") -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (system_prompt: str | None, active_id: str | None).

    Three modes:
      - active_id == ROSIA_PERSONA_ID  → Rosia hardcoded persona
      - active_id == <custom uuid>     → Custom companion prompt from disk
      - active_id == "plain_assistant" → Plain assistant (no companion persona)
    """
    try:
        persona_dir = Path.home() / ".persona_ai" / "personas" / (token or "default")
        active_id_file = persona_dir / "active_id.txt"

        if not active_id_file.exists():
            return ROSIA_BASE_PERSONA, ROSIA_PERSONA_ID

        active_id = active_id_file.read_text().strip()
        if not active_id or active_id == ROSIA_PERSONA_ID:
            return ROSIA_BASE_PERSONA, ROSIA_PERSONA_ID

        if active_id == "plain_assistant":
            return None, None

        # Custom persona from disk
        persona_file = persona_dir / f"persona_{active_id}.json"
        if persona_file.exists():
            with open(persona_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                prompt = data.get("system_prompt", ROSIA_BASE_PERSONA)
                return prompt, active_id

    except Exception:
        pass

    return ROSIA_BASE_PERSONA, ROSIA_PERSONA_ID


def _is_companion_persona(active_id: Optional[str]) -> bool:
    """Returns True if the active persona is a companion (Rosia or custom)."""
    return active_id is not None


def build_messages(user_input: str, memories: list, summary: str, state: dict, history: list, token: str = "local") -> list:
    """
    Assembles the full message list for the LLM.

    Prompt construction order (highest → lowest priority):
      1. System persona (Rosia / custom companion / plain assistant)
      2. Tool protocols (web search + MCP tools)
      3. System clock
      4. Cognitive/emotional state (companion mode only)
      5. Persona style directive (distilled from chat exports)
      6. Behavioral directives (approved RSM rules)
      7. Internal memory recall (episodic + graph summary)
      8. Chat history
      9. User message
    """
    persona_prompt, active_persona_id = get_active_persona_data(token)
    is_companion = _is_companion_persona(active_persona_id)

    # ── 1. Base System Prompt ────────────────────────────────────────────────
    if persona_prompt is not None:
        system_prompt = persona_prompt
    else:
        system_prompt = PLAIN_ASSISTANT_PROMPT

    # ── 2. Tool Protocols ────────────────────────────────────────────────────
    # Agent loop uses the structured JSON protocol; legacy path keeps the
    # regex [CALL_TOOL: ...] tags.
    if AGENT_LOOP_ENABLED:
        system_prompt += f"\n\n{AGENT_TOOL_PROTOCOL}"
    else:
        system_prompt += f"\n\n{WEB_SEARCH_PROTOCOL}\n\n{MCP_TOOL_PROTOCOL}"

    # ── 3. System Clock ──────────────────────────────────────────────────────
    system_prompt += f"\n\n[SYSTEM CLOCK]\nCurrent Time: {get_uk_time()}"

    # ── 4. Conversational tone (optional feature, companion personas only) ───
    # Steers wording to stay consistent across a long conversation. Injected
    # only when the tone engine is switched on.
    if is_companion and EMOTION_ENGINE_ENABLED:
        mood = state.get("mood", BASELINE_MOOD)
        energy = state.get("energy", BASELINE_ENERGY)
        label, tone, _ = get_affective_state(mood, energy)
        system_prompt += (
            f"\n\n[CONVERSATION TONE]\n"
            f"The conversation currently reads as {label.lower()} "
            f"(mood {mood:+.2f}, energy {energy:+.2f}). "
            f"Match it with a '{tone}' tone."
        )

    # ── 5. Picture Protocol (companion personas only, and only if enabled) ───
    if is_companion and IMAGE_GEN_ENABLED:
        system_prompt += f"\n\n{PICTURE_SENDING_PROTOCOL}"

    # ── 6. Distilled Persona Style (custom companions only, not Rosia) ───────
    if active_persona_id and active_persona_id != ROSIA_PERSONA_ID:
        style_directive = get_persona_style_directive(active_persona_id)
        if style_directive:
            system_prompt += f"\n\n{style_directive}"

    # ── 7. Behavioral Directives (RSM approved rules) ──────────────────────
    # Retrieved by semantic similarity to the current user message.
    # Injected after emotional state, before memories — highest structural priority.
    retrieved_ids: list[str] = []
    relevant_rules = retrieve_relevant_rules(user_input)
    if relevant_rules:
        system_prompt += "\n\n[BEHAVIORAL DIRECTIVES]\n"
        system_prompt += "The following standing behavioral rules MUST be respected. They override default behavior where they apply:\n"
        for rule in relevant_rules:
            category = rule.get("category", "general")
            body = rule.get("body", "").strip()
            if body:
                system_prompt += f"- [{category.upper()}] {body}\n"
            retrieved_ids.append(rule["id"])

    # ── 7b. Learned Skills (RSM procedural how-tos) ─────────────────────────
    # Retrieved skills give the model a proven playbook for tasks like this,
    # written by earlier successful runs. Injected only when the agent loop is
    # active, since skills are procedures for tool-driven tasks.
    if AGENT_LOOP_ENABLED:
        relevant_skills = ks.retrieve(user_input, doc_type=ks.TYPE_SKILL, top_k=2)
        if relevant_skills:
            system_prompt += "\n\n[LEARNED SKILLS]\n"
            system_prompt += (
                "You have previously learned how to handle tasks like this. "
                "Follow these playbooks when they apply:\n"
            )
            for skill in relevant_skills:
                title = skill.get("title", "skill")
                system_prompt += f"\n### {title}\n{skill.get('_raw_body', skill.get('body', '')).strip()}\n"
                retrieved_ids.append(skill["id"])

    LAST_RETRIEVED_DOC_IDS[token] = retrieved_ids

    # ── 8. Internal Memory Recall ────────────────────────────────────────────
    if memories or summary:
        system_prompt += "\n\n[INTERNAL MEMORY RECALL]\n"
        if summary:
            system_prompt += f"Background Context:\n{summary}\n"
        if memories:
            system_prompt += "Recent relevant memories:\n"
            for m in memories:
                system_prompt += f"- {m}\n"

    # ── 9. Assemble message list ─────────────────────────────────────────────
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append(msg)
    messages.append({"role": "user", "content": user_input})

    return messages