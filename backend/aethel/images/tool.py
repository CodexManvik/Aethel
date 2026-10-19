"""image_generate: a persona sends a photo of itself (or of something), in chat or in a task, when the persona's
image_frequency allows it (spec §10.1). The model writes only the scene; code composes the prompt."""
import re
import shutil
from pathlib import Path

from ..personas.model import Persona
from ..tools.base import Assessment, Tool, ToolContext, ToolResult
from .compose import EXPRESSIONS, FRAMINGS

SOMETIMES_EVERY = 15  # "sometimes": at most one unasked photo per this many replies
REQUEST_Q = "Is the user asking for a picture or photo?"
# Used only when System 1 can't answer (off, still loading): the obvious ways of asking.
_ASKS = re.compile(r"\b(pic|pics|picture|pictures|photo|photos|selfie|selfies|snap|image)\b|\bshow me (you|yourself|what)",
                   re.I)
PARAMETERS = {"type": "object", "properties": {
    "scene": {"type": "string", "description": "What the photo shows: where you are, what you're doing and wearing, the light"},
    "framing": {"type": "string", "enum": list(FRAMINGS)},
    "expression": {"type": "string", "enum": list(EXPRESSIONS)}},
    "required": ["scene"]}
CHAT_DESCRIPTION = ("Send the user a photo of yourself (or of something you're looking at). Use it when they ask for "
                    "one, or when a photo genuinely fits the moment. Describe only the scene.")
ON_ITS_WAY = ("A photo is on its way: the user will see it as an attachment to this reply in a few seconds. Mention it "
              "naturally; don't describe it as if they've already seen it.")


class FrequencyRule:
    def __init__(self, *, system1, messages, settings):
        self.system1, self.messages, self.settings = system1, messages, settings

    async def asked(self, user_text: str) -> bool:
        answer = await self.system1.ask({"message": user_text[:600]},
                                        {"picture": {"type": "noul", "instructions": REQUEST_Q}}, "image_request")
        if answer is None:
            return bool(_ASKS.search(user_text))
        return answer["picture"]["noul"] >= self.settings.get().images.request_threshold

    def replies_since_last_image(self, conversation_id: str) -> int:
        n = 0
        for m in reversed(self.messages.list(conversation_id)):
            if m.role != "assistant":
                continue
            if m.meta.get("images"):
                return n
            n += 1
        return n

    async def allowed(self, persona: Persona, conversation_id: str, user_text: str) -> bool:
        if persona.image_frequency == "never" or persona.built_in or not persona.anchors:
            return False  # no face yet: every photo would show someone else
        if await self.asked(user_text):
            return True
        # the reply being written is already saved (empty), so it's counted: subtract it
        return (persona.image_frequency == "sometimes"
                and self.replies_since_last_image(conversation_id) - 1 >= SOMETIMES_EVERY)


def chat_image_tool(service, persona: Persona, conversation_id: str, message_id: str) -> Tool:
    """One photo per reply; the picture is made in the background while the words keep streaming."""
    sent: list[str] = []

    async def handler(args: dict, ctx: ToolContext) -> ToolResult:
        if sent:
            return ToolResult(False, "Error: one photo per reply. Carry on with your answer.")
        scene = str(args.get("scene") or "").strip()
        if not scene:
            return ToolResult(False, "Error: describe the scene of the photo.")
        image_id = await service.start(persona=persona, scene=scene, framing=args.get("framing"),
                                       expression=args.get("expression"), conversation_id=conversation_id,
                                       message_id=message_id, reason="chat")
        sent.append(image_id)
        return ToolResult(True, ON_ITS_WAY)

    return Tool("image_generate", CHAT_DESCRIPTION, PARAMETERS, "read", handler,
                lambda args: Assessment("allow", "", "Send a photo"))


def task_image_tool(service, personas, conversations, task_conversation, out_dir: Path) -> Tool:
    """For tasks (toolgroup "images"): waits for the picture and, when asked, saves a copy to Documents\\Aethel\\images."""
    async def handler(args: dict, ctx: ToolContext) -> ToolResult:
        conv = conversations.get(task_conversation(ctx.task_id)) if ctx.task_id else None
        persona = (personas.get(conv.persona_id) if conv else None) or personas.builtin()
        scene = str(args.get("scene") or "").strip()
        if not scene:
            return ToolResult(False, "Error: describe the scene of the picture.")
        got = await service.generate(persona=persona, scene=scene, framing=args.get("framing"),
                                     expression=args.get("expression"), conversation_id=conv.id if conv else None,
                                     reason="task", references=bool(persona.anchors))
        if not got["ok"]:
            return ToolResult(False, f"Error: {got.get('message', 'the picture failed')}")
        text = f"Made the picture ({got['url']})."
        if args.get("save"):
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / f"{got['id']}.jpg"
            shutil.copyfile(personas.folder(persona.id) / "gallery" / f"{got['id']}.jpg", target)
            text += f" Saved a copy to {target}."
        return ToolResult(True, text, meta={"image": got})

    params = {**PARAMETERS, "properties": {**PARAMETERS["properties"],
              "save": {"type": "boolean", "description": "Also save it to Documents\\Aethel\\images"}}}
    return Tool("image_generate", "Make a picture (of the persona when it has a face, or of anything).", params, "read",
                handler, lambda args: Assessment("allow", "", "Make a picture"), toolgroup="images")
