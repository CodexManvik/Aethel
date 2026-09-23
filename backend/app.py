from memory import retrieve_memories
from prompt_builder import build_messages
from generation import generate_stream
from config import HISTORY_WINDOW_SIZE

from state import state, update_state
from summarizer import update_summary

history = []

print("Rosia is online.\n")

while True:
    user_input = input("You: ").strip()

    if not user_input:
        continue

    update_state(user_input)

    memories = retrieve_memories(user_input)
    summary = update_summary(history)

    messages = build_messages(
        user_input=user_input,
        memories=memories,
        summary=summary,
        state=state,
        history=history,
    )

    # Collect streaming output into a single response string
    response_chunks = []
    for chunk in generate_stream(messages, target_model=state.get("active_model", "default")):
        response_chunks.append(chunk)

    response = "".join(response_chunks)

    print(f"\nRosia: {response}\n")

    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response})

    history = history[-HISTORY_WINDOW_SIZE:]

