"""Image prompts are composed in code (spec §10.2): the model only writes the scene, and picks a framing and an
expression from fixed lists."""
from ..personas.model import Persona

CONSISTENCY = ("Keep this exact person: same face, age, skin tone, hair and features as in the reference images. "
               "Photorealistic, natural, no text or watermarks.")
FRAMINGS = ("close-up", "portrait", "half-body", "full-body", "scene")
EXPRESSIONS = ("neutral", "smiling", "laughing", "thoughtful", "surprised", "sleepy")
SCENE_MAX = 400


def compose(persona: Persona, scene: str, framing: str | None = None, expression: str | None = None,
            references: bool = True) -> str:
    """"{image_style}. {scene}. Framing: {framing}. Expression: {expression}. {CONSISTENCY}", parts left out when
    empty; the consistency line only when reference images go with it."""
    scene = " ".join(scene.split())[:SCENE_MAX].rstrip(".")
    parts = [persona.image_style.strip().rstrip("."), scene,
             f"Framing: {framing}" if framing in FRAMINGS else "",
             f"Expression: {expression}" if expression in EXPRESSIONS else ""]
    text = ". ".join(p for p in parts if p) + "."
    return f"{text} {CONSISTENCY}" if references else text
