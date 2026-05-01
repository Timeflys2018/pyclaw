from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from pyclaw.channels.web.auth import create_jwt
from pyclaw.infra.settings import WebSettings

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenRequest(BaseModel):
    user_id: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user_id: str


@auth_router.post("/token")
async def issue_token(body: TokenRequest, request: Request) -> TokenResponse:
    settings: WebSettings = request.app.state.web_settings
    for user in settings.users:
        if user.id == body.user_id and hmac.compare_digest(
            user.password, body.password
        ):
            token = create_jwt(body.user_id, settings.jwt_secret)
            return TokenResponse(token=token, user_id=body.user_id)
    raise HTTPException(status_code=401, detail="Invalid credentials")
