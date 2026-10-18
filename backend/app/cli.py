"""ログイン用アカウントの管理コマンド（画面からの新規登録は作らない）。

    docker compose exec backend python -m app.cli create-user you@example.com
    docker compose exec backend python -m app.cli set-password you@example.com
    docker compose exec backend python -m app.cli list-users
    docker compose exec backend python -m app.cli logout-all you@example.com
    docker compose exec backend python -m app.cli delete-user you@example.com

パスワードは入力を表示せずに 2 回尋ねる。
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AuthSession, User
from app.services import auth


def _ask_password() -> str:
    first = getpass.getpass("パスワード（12文字以上）: ")
    if first != getpass.getpass("確認のためもう一度: "):
        sys.exit("パスワードが一致しません")
    try:
        return auth.hash_password(first)
    except ValueError as e:
        sys.exit(str(e))


async def _get(session, email: str) -> User:
    user = await session.scalar(select(User).where(User.email == auth.normalize_email(email)))
    if user is None:
        sys.exit(f"アカウントがありません: {email}")
    return user


async def create_user(email: str) -> None:
    email = auth.normalize_email(email)
    if "@" not in email:
        sys.exit("メールアドレスの形式が不正です")
    async with SessionLocal() as session:
        if await session.scalar(select(User).where(User.email == email)):
            sys.exit(f"既にあります: {email}（パスワードの変更は set-password）")
        session.add(User(email=email, password_hash=_ask_password()))
        await session.commit()
    print(f"作成しました: {email}")


async def set_password(email: str) -> None:
    async with SessionLocal() as session:
        user = await _get(session, email)
        user.password_hash = _ask_password()
        user.failed_login_count = 0
        user.locked_until = None
        n = await auth.revoke_all(session, user)
        await session.commit()
    print(f"変更しました: {user.email}（ログイン中の {n} 件をログアウトしました）")


async def logout_all(email: str) -> None:
    async with SessionLocal() as session:
        user = await _get(session, email)
        n = await auth.revoke_all(session, user)
        await session.commit()
    print(f"{n} 件のログインを無効にしました: {user.email}")


async def delete_user(email: str) -> None:
    async with SessionLocal() as session:
        user = await _get(session, email)
        await session.delete(user)
        await session.commit()
    print(f"削除しました: {user.email}（会話データは残ります）")


async def list_users() -> None:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(User, func.count(AuthSession.token_hash))
            .outerjoin(AuthSession, AuthSession.user_id == User.id)
            .group_by(User.id)
            .order_by(User.created_at)
        )
        for user, n in rows:
            locked = f" ロック中（{user.locked_until:%Y-%m-%d %H:%M} UTC まで）" if user.locked_until else ""
            print(f"{user.email}  最終ログイン: {user.last_login_at or '-'}  ログイン中: {n}件{locked}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="ログイン用アカウントの管理")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-user", "set-password", "logout-all", "delete-user"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list-users")
    args = parser.parse_args()
    commands = {
        "create-user": lambda: create_user(args.email),
        "set-password": lambda: set_password(args.email),
        "logout-all": lambda: logout_all(args.email),
        "delete-user": lambda: delete_user(args.email),
        "list-users": list_users,
    }
    asyncio.run(commands[args.command]())


if __name__ == "__main__":
    main()
