import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ...protocol import ApprovalNeeded
from ...runtime.checks import Check
from ...runtime.store import StepRecord, TERMINAL_STATES, TaskRecord
from ...paths import aethel_home
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


@router.get("/recent")
def recent_tasks(limit: int = 50, svc: Services = Depends(get_services)) -> list[TaskRecord]:
    """Every conversation's tasks, newest first: the Tasks screen (spec §4.5)."""
    return svc.tasks.recent(min(max(limit, 1), 200))


def _thumbnail_path(svc: Services, task_id: str, step_id: str):
    step = next((s for s in svc.tasks.steps(task_id) if s.id == step_id), None)
    if step is None or not step.thumbnail:
        raise HTTPException(status_code=404, detail="No thumbnail for that step")
    media = (aethel_home() / "media").resolve()
    path = (media / step.thumbnail).resolve()
    if media not in path.parents or not path.is_file():  # the stored path must stay inside media/
        raise HTTPException(status_code=404, detail="No thumbnail for that step")
    return path


@router.get("/{task_id}/steps/{step_id}/thumbnail")
def step_thumbnail(task_id: str, step_id: str, svc: Services = Depends(get_services)) -> FileResponse:
    return FileResponse(_thumbnail_path(svc, task_id, step_id), media_type="image/jpeg")


@router.get("/{task_id}/export")
def export_task(task_id: str, svc: Services = Depends(get_services)) -> Response:
    """task.json (the task and every step) plus the thumbnails, for the dissertation appendix."""
    task = svc.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    steps = svc.tasks.steps(task_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("task.json", json.dumps({"task": task.model_dump(), "steps": [s.model_dump() for s in steps]},
                                           indent=2, ensure_ascii=False))
        for s in steps:
            if s.thumbnail:
                try:
                    z.write(_thumbnail_path(svc, task_id, s.id), f"thumbnails/{s.idx:03d}-{s.tool}.jpg")
                except HTTPException:
                    pass  # recorded but gone from disk
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="aethel-{task_id}.zip"'})


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
