# openai-query-app

ChatGPT 風のチャット UI から OpenAI Responses API（Web 検索ツール付き）を呼び出し、
会話・各応答の処理時間／トークン使用量／エラーを観測する個人用の技術検証アプリ。
仕様は [docs/specification.md](docs/specification.md) を参照。

## 構成

| 区分 | 内容 |
| --- | --- |
| フロント | React 19 + TypeScript + Vite 8 + Tailwind v4 + shadcn/ui |
| バックエンド | FastAPI + SQLAlchemy 2 (async) + Alembic + OpenAI Python SDK |
| アプリ DB | PostgreSQL 17 |
| 観測 | Langfuse v4（セルフホスト、別 Compose プロジェクト） |
| 実行環境 | Windows + Docker Desktop（ソースは Windows 側、コンテナは Linux） |

```
compose.yaml          アプリ一式（db / backend / frontend）
langfuse/compose.yaml Langfuse 一式（web / worker / postgres / clickhouse / redis / minio）
backend/              FastAPI アプリ・Alembic・テスト
frontend/             React アプリ
scripts/init_env.py   .env / langfuse/.env の生成
docs/                 仕様書
```

## 初回セットアップ

前提: Docker Desktop が起動していること。

```powershell
# 1. シークレットを生成（既存ファイルは上書きしない）
python scripts/init_env.py --email you@example.com

# 2. .env の OPENAI_API_KEY を設定

# 3. Langfuse を起動（初回はイメージ取得に数分）
docker compose -f langfuse/compose.yaml up -d

# 4. アプリを起動（DB マイグレーションは backend 起動時に自動実行）
docker compose up -d
```

| 用途 | URL |
| --- | --- |
| アプリ | http://localhost:5173 |
| API ドキュメント | http://localhost:8000/docs |
| Langfuse | http://localhost:3000 （ログイン情報は `langfuse/.env` の `LANGFUSE_INIT_USER_*`） |

ポートはすべて 127.0.0.1 にバインドしている。

## 開発

```powershell
docker compose logs -f backend                       # ログ
docker compose exec backend python -m pytest -q      # テスト
docker compose exec backend ruff check app tests     # lint
docker compose exec backend alembic revision --autogenerate -m "..."  # マイグレーション作成
docker compose exec frontend npm run build           # 型チェック + ビルド
```

- Windows のバインドマウントは変更通知が届かないため、uvicorn / Vite ともポーリングで変更を検知する。
- `node_modules` と Python venv は named volume に置いている。依存を変えたら
  `docker compose restart frontend` / `docker compose up -d --build backend`。

## 主な設定（.env）

| 変数 | 既定 | 内容 |
| --- | --- | --- |
| `OPENAI_API_KEY` | なし | 必須 |
| `OPENAI_MODEL` | `gpt-5.5` | 既定モデル |
| `OPENAI_MODELS` | `gpt-5.5,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.4,gpt-5.4-mini,gpt-5.4-nano,gpt-4.1,gpt-4.1-mini` | 画面で選択できるモデル（カンマ区切り） |
| `OPENAI_TIMEOUT_SECONDS` | `90` | API タイムアウト（Cloudflare Tunnel の 100 秒制限より短く） |
| `OPENAI_MAX_RETRIES` | `0` | 自動再試行回数（429 / 5xx / 接続エラーのみ対象） |
| `MAX_OUTPUT_TOKENS` | `8000` | 出力上限（推論トークンを含む） |
| `MAX_PROMPT_CHARS` | `20000` | 入力長上限 |
| `MAX_CONTEXT_CHARS` | `60000` | 文脈上限（文字数ベース、超過時は送信せず通知） |
| `DAILY_RUN_LIMIT` | `100` | 1日の実行回数上限（0 で無制限） |
| `DAILY_COST_LIMIT_USD` | `3.0` | 1日の推定利用額上限（0 で無制限） |
| `MONTHLY_COST_LIMIT_USD` | `30.0` | 1か月の推定利用額上限（0 で無制限） |
| `USAGE_TIMEZONE` | `Asia/Tokyo` | 1日・1か月の区切り |
| `LANGFUSE_BASE_URL` | `http://host.docker.internal:3000` | コンテナからの送信先 |
| `LANGFUSE_PUBLIC_URL` | `http://localhost:3000` | ブラウザ用リンク |

## 停止・データ

```powershell
docker compose stop; docker compose -f langfuse/compose.yaml stop
```

推定利用額は `backend/app/services/pricing.py` のモデル別単価から計算する（OpenAI の請求額ではない）。
`OPENAI_MODELS` に単価未定義のモデルを加えた場合は起動ログに警告が出て、そのモデルの実行は利用額に含まれない。
OpenAI 管理画面のプロジェクト予算アラートも併用すること。

データは named volume（`oqa-app_db_data`、`oqa-langfuse_*`）に保存される。
`docker compose down -v` はデータを削除するので通常は使わない。volume 自体はバックアップではない。
