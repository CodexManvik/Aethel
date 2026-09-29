from aethel.runtime.reflect import parse_learning, task_report
from aethel.runtime.store import StepRecord, TaskRecord


def test_parse_learning_is_forgiving_about_shape():
    notes, skill = parse_learning('{"app_notes": [{"app": "notepad", "facts": ["x"]}, {"bad": 1}], '
                                  '"skill": {"title": "T", "intent": "I", "steps": ["a"]}}')
    assert notes == [{"app": "notepad", "facts": ["x"]}] and skill["title"] == "T"
    assert parse_learning('{"app_notes": [], "skill": {"title": "T", "steps": []}}') == ([], None)
    assert parse_learning("not json") == ([], None)


def test_report_wraps_the_step_log_as_untrusted():
    task = TaskRecord(id="t", conversation_id="c", goal="Write a haiku", state="done", plan=["Open", "Type"],
                      plan_done=[], checks=[], summary=None, error=None, created_at="", updated_at="")
    steps = [StepRecord(id="s", task_id="t", idx=0, tool="win_type", args={}, summary="Type in notepad",
                        verdict="allow", ok=True, result="Typed.\nmore", duration_ms=40, created_at="")]
    text = task_report(task, steps, lambda src, body: f"<untrusted source=\"{src}\">{body}</untrusted>")
    assert "Outcome: succeeded" in text and '<untrusted source="task log">' in text
    assert "0. win_type -> Type in notepad | ok (40 ms): Typed." in text
