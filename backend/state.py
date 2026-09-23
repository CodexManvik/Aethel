# state.py
from generation import get_available_models
from config import (
    DEFAULT_ACTIVE_MODEL, MOOD_DESCRIPTION, BASELINE_MOOD, BASELINE_ENERGY,
)

# Dynamically fetch the running model from llama-server on boot
loaded_models = get_available_models()
initial_model = loaded_models[0]["id"] if loaded_models else DEFAULT_ACTIVE_MODEL

# 'mood' and 'energy' track conversational tone (see emotion_engine.py).
# They stay at baseline when the tone engine is disabled.
state = {
    "interaction_count": 0,
    "active_model": initial_model,
    "mood": BASELINE_MOOD,
    "energy": BASELINE_ENERGY,
}

def update_state(user_input):
    if not user_input or not user_input.strip():
        return
    state["interaction_count"] += 1

def set_active_model(model_name):
    state["active_model"] = model_name

def get_mood_description():
    return MOOD_DESCRIPTION