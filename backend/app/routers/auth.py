"""ログイン不要の API（ヘルスチェック・ログイン）と、他の API で使う認証の依存関係。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User, utcnow
from app.services import auth


def _set_cookie(response: Response, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=int((expires_at - utcnow()).total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def require_csrf_header(request: Request) -> None:
    """更新系リクエストにアプリ専用ヘッダーを必須にする（他サイトからの送信を拒否する）。"""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(auth.CSRF_HEADER) != "1":
        raise HTTPException(403, detail={"code": "forbidden", "message": "不正なリクエストです"})


async def current_user(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    """ログイン中の利用者。未ログイン・期限切れは 401。期限を延ばしたときは Cookie も更新する。"""
    token = request.cookies.get(auth.COOKIE_NAME)
    resolved = await auth.resolve(session, get_settings(), token)
    if resolved is None:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "ログインしてください"})
    user, extended_until = resolved
    if extended_until is not None and token:
        _set_cookie(response, token, extended_until)
    return user


router = APIRouter(dependencies=[Depends(require_csrf_header)])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class MeOut(BaseModel):
    email: str


@router.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, detail={"code": "db_error", "message": "DB に接続できません"}) from e
    return {"status": "ok"}


@router.post("/api/v1/auth/login", response_model=MeOut)
async def login(
    body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    try:
        user, new = await auth.login(
            session, get_settings(), body.email, body.password, request.headers.get("user-agent")
        )
    except auth.LoginError as e:
        status = 429 if e.code == "login_locked" else 401
        raise HTTPException(status, detail={"code": e.code, "message": e.message}) from e
    _set_cookie(response, new.token, new.expires_at)
    return MeOut(email=user.email)


@router.post("/api/v1/auth/logout", status_code=204)
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    await auth.logout(session, request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")


@router.get("/api/v1/auth/me", response_model=MeOut)
async def me(user: User = Depends(current_user)):
    return MeOut(email=user.email)
