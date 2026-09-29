"""Intent routing (spec §5.2): does a message want talk, a task, or to stop
the running task? System 1 answers two yes/no questions in one call; below the
measured thresholds it's chat, because a chat reply is harmless and a
surprise task isn't. Phrasings and thresholds come from
scripts/eval_s1_intent.py on eval/s1_intent.jsonl (tuned on the even rows,
reported on the odd ones)."""
from typing import Literal

Route = Literal["chat", "task", "stop_task"]

ACT = ("Is this message a request for Aethel to carry out an action on the computer right now, such as opening an "
       "app, making or changing a file, clicking or typing? Questions, conversation and requests for an answer in "
       "words are not.")
# Asked only while a task runs, with the task in the state: without it this
# measured far worse. As a choice it also beat a yes/no.
STOP = {"type": "choice", "instructions": "What does the user want Aethel to do with this message?", "criteria": {
    "chat": "talk with Aethel: a question, an opinion, small talk, or anything answered in words",
    "task": "have Aethel do something on the computer: open apps, create, edit or save files, click, type",
    "stop_task": "stop, cancel or abort the work Aethel is doing right now"}}


def act_state(text: str) -> dict:
    return {"message": text}  # just the message: the running task's goal confused this question


def stop_state(text: str, running_task: str) -> dict:
    return {"message": text, "task_in_progress": running_task}


def is_stop(answer: dict, threshold: float) -> bool:
    return answer["choice"] == "stop_task" and answer["probabilities"]["stop_task"] >= threshold


async def route(system1, settings, text: str, running_task: str | None) -> Route:
    s1 = settings.get().system1
    if running_task is not None:
        stop = await system1.ask(stop_state(text, running_task), {"stop": STOP}, "intent_stop")
        if stop is not None and is_stop(stop["stop"], s1.stop_threshold):
            return "stop_task"
    if not s1.auto_tasks:
        return "chat"
    act = await system1.ask(act_state(text), {"act": {"type": "noul", "instructions": ACT}}, "intent_act")
    return "task" if act is not None and act["act"]["noul"] >= s1.intent_threshold else "chat"
