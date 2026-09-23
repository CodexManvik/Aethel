from fastapi import HTTPException, Request

from ..services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_auth(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    if not get_services(request).auth.check(token):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
