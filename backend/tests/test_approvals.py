import asyncio
import json

import pytest

from aethel.hub import EventHub
from aethel.runtime.prompts import COMPLETE_STEP, FINISH_TASK, SUBMIT_PLAN, executor_system, resume_note
from aethel.runtime.store import StepRecord
from aethel.safety.approvals import ApprovalBroker

pytestmark = pytest.mark.anyio


async def test_request_blocks_until_resolved_and_announces_both_sides():
    hub = EventHub()
    seen = []

    async def collect(p):
        seen.append(json.loads(p))

    hub.subscribe(collect)
    broker = ApprovalBroker(hub)
    waiter = asyncio.create_task(broker.request(task_id="t", step_id="s", tool="fs_write", summary="C:/x",
                                                reason="outside", tier="write"))
    await asyncio.sleep(0)
    [needed] = [e for e in seen if e["type"] == "approval_needed"]
    assert [a.approval_id for a in broker.pending_for("t")] == [needed["approval_id"]]
    assert await broker.resolve(needed["approval_id"], "allow_task") is True
    assert await waiter == "allow_task"
    assert seen[-1] == {"type": "approval_resolved", "approval_id": needed["approval_id"], "task_id": "t",
                        "decision": "allow_task"}
    assert broker.pending_for("t") == []
    assert await broker.resolve(needed["approval_id"], "deny") is False


async def test_cancelled_request_is_cleaned_up():
    broker = ApprovalBroker(EventHub())
    waiter = asyncio.create_task(broker.request(task_id="t", step_id="s", tool="x", summary="", reason="",
                                                tier="irreversible"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert broker.pending_for("t") == []


def test_prompts_are_wired():
    from datetime import datetime

    assert SUBMIT_PLAN.name == "submit_plan" and "checks" in SUBMIT_PLAN.parameters["required"]
    assert COMPLETE_STEP.name == "complete_plan_step" and FINISH_TASK.name == "finish_task"
    text = executor_system("write a haiku", ["write", "save"], ["C:/a.txt exists"], datetime(2026, 9, 23, 10, 0))
    assert "write a haiku" in text and "0. write" in text and "untrusted" in text and "C:/a.txt exists" in text
    note = resume_note([StepRecord(id="s", task_id="t", idx=0, tool="fs_write", args={}, summary="C:/a.txt",
                                   verdict="allow", ok=True, result="Wrote 5 characters", duration_ms=3,
                                   created_at="")])
    assert "fs_write" in note and "C:/a.txt" in note
