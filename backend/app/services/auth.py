"""メールアドレス＋パスワードのログインと、Cookie によるログイン状態の管理。

- パスワードは Argon2 のハッシュだけを保存する。
- ログイン状態は Cookie のランダムなトークンで持ち、DB には SHA-256 だけを保存する。
- 最後に使ってから session_days 日で切れる（使うたびに延長する。書き込みは 1 時間に 1 回まで）。
- 連続 login_max_failures 回の失敗で login_lock_minutes 分ロックする。
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AuthSession, User, utcnow

COOKIE_NAME = "oqa_session"
# 更新系リクエストに必須のヘッダー。他サイトのページからは CORS の事前確認なしに付けられないため CSRF 対策になる
CSRF_HEADER = "X-OQA-Request"
PASSWORD_MIN = 12
_TOUCH_INTERVAL = timedelta(hours=1)

_hasher = PasswordHasher()
# 存在しないメールアドレスでも同じ時間をかけ、登録有無を推測させない
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class LoginError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"パスワードは {PASSWORD_MIN} 文字以上にしてください")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class NewSession:
    token: str
    expires_at: datetime


async def login(
    session: AsyncSession, settings: Settings, email: str, password: str, user_agent: str | None
) -> tuple[User, NewSession]:
    """認証に成功したらログイン状態を作る。失敗理由は区別せずに返す（ロック中のみ別扱い）。"""
    now = utcnow()
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        raise LoginError("invalid_credentials", "メールアドレスまたはパスワードが違います")
    if user.locked_until and user.locked_until > now:
        raise LoginError(
            "login_locked",
            f"ログインの失敗が続いたため、{settings.login_lock_minutes} 分間ログインできません",
        )
    if not verify_password(user.password_hash, password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.login_max_failures:
            user.failed_login_count = 0
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
        await session.commit()
        raise LoginError("invalid_credentials", "メールアドレスまたはパスワードが違います")

    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(days=settings.session_days)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=expires,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    # 期限切れのログイン状態はここでまとめて掃除する
    await session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    await session.commit()
    return user, NewSession(token=token, expires_at=expires)


async def resolve(
    session: AsyncSession, settings: Settings, token: str | None
) -> tuple[User, datetime | None] | None:
    """Cookie のトークンからログイン中の利用者を返す。

    使うたびに有効期限を延ばし、延ばした場合は新しい期限を返す（Cookie の期限も合わせて延ばすため）。
    """
    if not token:
        return None
    now = utcnow()
    auth = await session.get(AuthSession, token_hash(token))
    if auth is None or auth.expires_at <= now:
        return None
    user = await session.get(User, auth.user_id)
    if user is None:
        return None
    if now - auth.last_seen_at < _TOUCH_INTERVAL:
        return user, None
    auth.last_seen_at = now
    auth.expires_at = now + timedelta(days=settings.session_days)
    await session.commit()
    return user, auth.expires_at


async def logout(session: AsyncSession, token: str | None) -> None:
    if token:
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        await session.commit()


async def revoke_all(session: AsyncSession, user: User) -> int:
    """パスワード変更時などに、その利用者のログイン状態をすべて無効にする。"""
    result = await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return result.rowcount or 0
