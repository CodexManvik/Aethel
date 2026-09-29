"""What Aethel learned before, for a new task (spec §6.3 retrieval, §5.2 skill selection).

Embedding retrieval shortlists approved skills and relevant app notes; System 1
then picks the one skill that is a procedure for this goal, or none. Learned
text came out of earlier tasks that may have read untrusted content, so it is
handed to the model as hints, never as instructions."""
from dataclasses import dataclass, field

import anyio

SHORTLIST = 5
OFFERED_WITHOUT_A_PICK = 3
INSTRUCTIONS = "Which of these learned skills is a procedure for achieving the user's goal?"
NONE = "none of these fits the goal"


@dataclass
class Recall:
    text: str | None = None
    skill_ids: list[str] = field(default_factory=list)  # the skills shown to the model, credited by outcome
    chosen: str | None = None                            # the one System 1 picked, if any


def _skill_block(doc: dict, heading: str) -> str:
    lines = [f"### {heading}: {doc['title']}", f"For: {doc['intent']}"]
    if doc["apps"]:
        lines.append("Apps: " + ", ".join(doc["apps"]))
    lines += [f"{i}. {s}" for i, s in enumerate(doc["steps"], 1)]
    lines += [f"- Pitfall: {p}" for p in doc["pitfalls"]]
    if doc["runs"]:
        lines.append(f"(worked {doc['successes']} of {doc['runs']} times)")
    return "\n".join(lines)


async def recall(knowledge, system1, goal: str, threshold: float) -> Recall:
    if knowledge is None:
        return Recall()
    skills = await anyio.to_thread.run_sync(knowledge.retrieve_skills, goal, SHORTLIST)
    notes = await anyio.to_thread.run_sync(knowledge.retrieve_notes, goal)
    chosen, sure_none = None, False
    if skills and system1 is not None:
        picked = await system1.choice({"goal": goal}, INSTRUCTIONS,
                                      {**{d["id"]: f"{d['title']}: {d['intent']}" for d in skills}, "none": NONE},
                                      "skill_select")
        if picked is not None and picked[1][picked[0]] >= threshold:
            if picked[0] == "none":
                sure_none = True
            else:
                chosen = next(d for d in skills if d["id"] == picked[0])
    # A confident "none" shows no skills; unsure (or no System 1) offers the shortlist's top as maybes.
    shown = [chosen] if chosen else [] if sure_none else skills[:OFFERED_WITHOUT_A_PICK]
    parts = [_skill_block(d, "A skill you learned for this" if chosen else "A skill that may help") for d in shown]
    parts += [f"### Notes on {n['app']}\n" + "\n".join(f"- {f}" for f in n["facts"]) for n in notes if n["facts"]]
    if not parts:
        return Recall()
    return Recall("\n\n".join(parts), [d["id"] for d in shown], chosen["id"] if chosen else None)
