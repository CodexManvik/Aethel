from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...protocol import ApprovalNeeded
from ...runtime.checks import Check
from ...runtime.store import StepRecord, TERMINAL_STATES, TaskRecord
from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/tasks", dependencies=[Depends(require_auth)])


class TaskDetail(BaseModel):
    task: TaskRecord
    steps: list[StepRecord]
    approvals: list[ApprovalNeeded]
    check_descriptions: list[str]


class RollbackItem(BaseModel):
    ok: bool
    message: str


class RollbackOut(BaseModel):
    results: list[RollbackItem]


def _detail(svc: Services, task: TaskRecord) -> TaskDetail:
    return TaskDetail(task=task, steps=svc.tasks.steps(task.id), approvals=svc.approvals.pending_for(task.id),
                      check_descriptions=[Check.model_validate(c).describe() for c in task.checks])


@router.get("")
def list_tasks(conversation_id: str, svc: Services = Depends(get_services)) -> list[TaskDetail]:
    return [_detail(svc, t) for t in svc.tasks.list_for_conversation(conversation_id)]


@router.get("/{task_id}")
def get_task(task_id: str, svc: Services = Depends(get_services)) -> TaskDetail:
    task = svc.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _detail(svc, task)


@router.post("/{task_id}/rollback")
def rollback_task(task_id: str, svc: Services = Depends(get_services)) -> RollbackOut:
    task = svc.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.state not in TERMINAL_STATES and task.state != "paused":
        raise HTTPException(status_code=409, detail="Stop the task before undoing its changes.")
    return RollbackOut(results=[RollbackItem(ok=ok, message=msg) for ok, msg in svc.changes.rollback_task(task_id)])
