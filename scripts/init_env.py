"""ローカル用の .env と langfuse/.env をランダムなシークレットで生成する。

既存ファイルは上書きしない。標準ライブラリのみ使用。
使い方: python scripts/init_env.py [--email you@example.com]
"""

import argparse
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def write_if_absent(path: Path, content: str) -> None:
    if path.exists():
        print(f"skip: {path.relative_to(ROOT)} は既に存在します")
        return
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"created: {path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="admin@example.com", help="Langfuse 初期ユーザーのメール")
    args = parser.parse_args()

    public_key = f"pk-lf-{secrets.token_hex(16)}"
    secret_key = f"sk-lf-{secrets.token_hex(16)}"
    project_id = "oqa-local"

    write_if_absent(
        ROOT / "langfuse" / ".env",
        f"""# Langfuse セルフホスト用シークレット（自動生成・Git管理外）
LANGFUSE_VERSION=4.56.0
NEXTAUTH_SECRET={secrets.token_hex(32)}
SALT={secrets.token_hex(32)}
ENCRYPTION_KEY={secrets.token_hex(32)}
POSTGRES_PASSWORD={secrets.token_hex(16)}
CLICKHOUSE_PASSWORD={secrets.token_hex(16)}
REDIS_AUTH={secrets.token_hex(16)}
MINIO_ROOT_PASSWORD={secrets.token_hex(16)}

# 初回起動時に自動作成される org / project / user
LANGFUSE_INIT_PROJECT_ID={project_id}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY={public_key}
LANGFUSE_INIT_PROJECT_SECRET_KEY={secret_key}
LANGFUSE_INIT_USER_EMAIL={args.email}
LANGFUSE_INIT_USER_PASSWORD={secrets.token_urlsafe(18)}
""",
    )

    write_if_absent(
        ROOT / ".env",
        f"""# アプリ用設定（自動生成・Git管理外）
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5

POSTGRES_PASSWORD={secrets.token_hex(16)}

# Langfuse（langfuse/.env と同じキー）
LANGFUSE_PUBLIC_KEY={public_key}
LANGFUSE_SECRET_KEY={secret_key}
LANGFUSE_BASE_URL=http://host.docker.internal:3000
LANGFUSE_PUBLIC_URL=http://localhost:3000
LANGFUSE_PROJECT_ID={project_id}
""",
    )


if __name__ == "__main__":
    main()
