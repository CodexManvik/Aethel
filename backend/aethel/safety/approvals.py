"""Suspends a task step until the user decides (spec §4.3 approvals)."""
import asyncio
from dataclasses import dataclass

from ..hub import EventHub
from ..protocol import ApprovalNeeded, ApprovalResolved, Decision
from ..store.repos import new_id


@dataclass
class _Pending:
    event: ApprovalNeeded
    future: asyncio.Future


class ApprovalBroker:
    def __init__(self, hub: EventHub):
        self.hub = hub
        self._pending: dict[str, _Pending] = {}

    async def request(self, *, task_id: str, step_id: str, tool: str, summary: str, reason: str,
                      tier: str) -> Decision:
        event = ApprovalNeeded(approval_id=new_id("appr"), task_id=task_id, step_id=step_id, tool=tool,
                               summary=summary, reason=reason, tier=tier)
        future = asyncio.get_running_loop().create_future()
        self._pending[event.approval_id] = _Pending(event, future)
        try:
            await self.hub.publish(event)
            return await future
        finally:
            self._pending.pop(event.approval_id, None)

    async def resolve(self, approval_id: str, decision: Decision) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(decision)
        await self.hub.publish(ApprovalResolved(approval_id=approval_id, task_id=pending.event.task_id,
                                                decision=decision))
        return True

    def pending_for(self, task_id: str) -> list[ApprovalNeeded]:
        return [p.event for p in self._pending.values() if p.event.task_id == task_id]
