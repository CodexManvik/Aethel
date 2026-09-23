# summarizer.py
import requests
from config import LLAMA_BASE_URL, LLAMA_TIMEOUT, SUMMARY_MESSAGE_WINDOW

conversation_summary = ""


def update_summary(history: list) -> str:
    """
    Generates a compressed semantic summary of recent conversation history
    by passing it through the local LLM. Falls back to plain concatenation
    if the model is unreachable.
    """
    global conversation_summary

    if not history:
        return conversation_summary

    recent = history[-SUMMARY_MESSAGE_WINDOW:]

    # Build a readable transcript for the summarizer
    transcript = "\n".join(
        f"{msg['role'].capitalize()}: {msg['content']}"
        for msg in recent
        if isinstance(msg.get("content"), str)
    )

    if not transcript.strip():
        return conversation_summary

    prompt = (
        "You are a memory compression system. "
        "Summarize the following conversation into 3-5 concise bullet points. "
        "Focus on: emotional tone, key topics discussed, any facts the user shared about themselves, "
        "and anything Rosia committed to or expressed. "
        "Be terse. No filler. Output only the bullet points.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    try:
        response = requests.post(
            f"{LLAMA_BASE_URL}/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 200,
                "stream": False,
            },
            timeout=LLAMA_TIMEOUT,
        )

        if response.status_code == 200:
            data = response.json()
            summary_text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if summary_text:
                conversation_summary = summary_text
                return conversation_summary

    except Exception as e:
        print(f"[SUMMARIZER] LLM unreachable, falling back to transcript: {e}")

    # Fallback: plain transcript slice (old behavior)
    conversation_summary = transcript
    return conversation_summary