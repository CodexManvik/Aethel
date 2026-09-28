from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...services import Services
from ..deps import get_services, require_auth

router = APIRouter(prefix="/api/tools", dependencies=[Depends(require_auth)])


class ToolsStatus(BaseModel):
    servers: dict[str, str]  # MCP server -> starting | running | stopped | failed: …
    tools: list[str]


@router.get("")
def tools_status(svc: Services = Depends(get_services)) -> ToolsStatus:
    return ToolsStatus(servers=svc.mcp.status(), tools=sorted(svc.registry.names()))
