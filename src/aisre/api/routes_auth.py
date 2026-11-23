"""Login local, logout e verificação de sessão. Falha fechado sem hash/segredo configurados."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from aisre import strings_pt as t
from aisre.auth import (
    COOKIE_NAME,
    now_of,
    require_same_origin,
    require_session,
    sign_session,
    verify_password,
)

router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=1024)


@router.post("/login", dependencies=[Depends(require_same_origin)])
async def login(body: LoginIn, request: Request, response: Response):
    settings = request.app.state.settings
    stored = settings.admin_password_hash.get_secret_value()
    secret = settings.session_secret.get_secret_value()
    if not stored or not secret or not settings.admin_user:
        raise HTTPException(status_code=503, detail=t.ERRO_LOGIN_NAO_CONFIGURADO)
    throttle = request.app.state.login_throttle
    ip = request.client.host if request.client else "?"
    wait = throttle.retry_after(ip, body.username)
    if wait:
        raise HTTPException(
            status_code=429, detail=t.ERRO_LOGIN_BLOQUEADO, headers={"Retry-After": str(wait)}
        )
    # Sempre verifica a senha (custo constante), mesmo com usuário errado.
    password_ok = verify_password(body.password, stored)
    user_ok = body.username == settings.admin_user
    if not (password_ok and user_ok):
        throttle.failure(ip, body.username)
        raise HTTPException(status_code=401, detail=t.ERRO_LOGIN_INVALIDO)
    throttle.success(ip, body.username)
    ttl = settings.session_ttl_seconds
    response.set_cookie(
        COOKIE_NAME,
        sign_session(secret, settings.admin_user, now_of(request), ttl),
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return {"user": settings.admin_user}


@router.post("/logout", dependencies=[Depends(require_same_origin)])
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: str = Depends(require_session)):
    return {"user": user}
