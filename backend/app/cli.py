"""ログイン用アカウントの管理コマンド（画面からの新規登録は作らない）。

    docker compose exec backend python -m app.cli create-user you@example.com
    docker compose exec backend python -m app.cli set-password you@example.com
    docker compose exec backend python -m app.cli list-users
    docker compose exec backend python -m app.cli logout-all you@example.com
    docker compose exec backend python -m app.cli delete-user you@example.com
    docker compose exec backend python -m app.cli eval-search search/eval/toyota_2027q1.json

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


async def eval_search(path: str) -> None:
    """検索の評価（設計書 §17.2）。expect の文字列をすべて含むチャンクが上位 K 件に入れば正解とする。
    質問は埋め込みのため OpenAI へ送る（1 問あたり $0.000001 程度）"""
    import json

    from app.config import get_settings
    from app.doc_models import Chunk, Issuer, RetrievalHit
    from app.services.search import retrieval
    from app.services.search.chunking import normalize

    data = json.loads(open(path, encoding="utf-8").read())
    settings = get_settings()
    hits_at_k, rr = 0, 0.0
    async with SessionLocal() as session:
        issuer_ids = []
        if data.get("issuer_code"):
            issuer = await session.scalar(select(Issuer).where(Issuer.code == data["issuer_code"]))
            if issuer is None:
                sys.exit(f"企業がありません: {data['issuer_code']}")
            issuer_ids = [issuer.id]
        for case in data["cases"]:
            run = await retrieval.search(
                session, settings, case["q"], retrieval.Filters(issuer_ids=issuer_ids)
            )
            rows = (
                await session.execute(
                    select(
                        RetrievalHit.final_rank, RetrievalHit.lexical_rank, RetrievalHit.vector_rank, Chunk
                    )
                    .join(Chunk, Chunk.id == RetrievalHit.chunk_id)
                    .where(RetrievalHit.run_id == run.id)
                    .order_by(RetrievalHit.final_rank)
                )
            ).all()
            # 検索と同じ正規化（均等割付の空白を詰める等）で照合する
            expect = [normalize(e) for e in case["expect"]]
            found = next(
                (
                    (rank, lex, vec, c)
                    for rank, lex, vec, c in rows
                    if all(e in normalize(c.text) for e in expect)
                ),
                None,
            )
            if found:
                hits_at_k += 1
                rr += 1 / found[0]
                rank, lex, vec, c = found
                print(
                    f"OK   #{rank:<2} (字句 {lex or '-'} / ベクトル {vec or '-'}) p{c.page_number:<3} {case['q']}"
                )
            else:
                print(
                    f"MISS     {case['q']}  期待: {case['expect']}  上位: {[f'p{c.page_number}' for *_, c in rows[:5]]}"
                )
    n = len(data["cases"])
    print(f"\nRecall@{settings.context_top_k}: {hits_at_k}/{n} = {hits_at_k / n:.1%}  MRR: {rr / n:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="ログイン用アカウントの管理")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-user", "set-password", "logout-all", "delete-user"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list-users")
    sub.add_parser("eval-search").add_argument("file")
    args = parser.parse_args()
    commands = {
        "create-user": lambda: create_user(args.email),
        "set-password": lambda: set_password(args.email),
        "logout-all": lambda: logout_all(args.email),
        "delete-user": lambda: delete_user(args.email),
        "list-users": list_users,
        "eval-search": lambda: eval_search(args.file),
    }
    asyncio.run(commands[args.command]())


if __name__ == "__main__":
    main()
