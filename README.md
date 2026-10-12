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

## システム指示とモード

モデルへ送るシステム指示は `backend/prompts/` のファイルで管理する（CLAUDE.md にあたる）。
編集すると次の実行から反映され、再起動は不要。

| ファイル | 内容 |
| --- | --- |
| `prompts/base.md` | 全モード共通の基本ルール |
| `prompts/modes/<id>.md` | モードごとの追加指示。base.md の後ろに追記して送る |

| モード | ID | 内容 |
| --- | --- | --- |
| 標準 | `standard` | 基本ルールのみ（モード導入前と同じ指示） |
| トークン節約 | `token-saver` | 簡略化した日本語で要点だけ答える。gpt-5 系では `text.verbosity` と `reasoning.effort` も `low` にする |
| ビジネス | `business` | ビジネスシーン向けの丁寧な文体と「結論→根拠→補足」の構成 |
| やさしく説明 | `easy` | 専門知識がない人向けに、たとえを交えて順を追って説明する |

モードファイルの書式（先頭の `---` で囲んだ設定と本文）:

```markdown
---
label: トークン節約            # 画面の表示名（必須）
description: 要点だけを…       # 選択時のツールチップ
order: 2                       # 表示順
verbosity: low                 # 任意: low / medium / high
reasoning_effort: low          # 任意: minimal / low / medium / high
---
## 回答スタイル: …（本文がそのまま指示になる）
```

- `verbosity` / `reasoning_effort` は対応モデル（gpt-5 系）にだけ付ける。gpt-4.1 系は 400 エラーになるため指示文だけを適用する。
- `<!-- -->` のコメントは読み込み時に除去され、モデルには送られない。
- 指示文とパラメータのハッシュを `prompt_version` として実行ごとに記録する。Langfuse にもモードと版を送る。
- サイドバーの「モード別の実績」で、モード×モデル×検索有無ごとの出力トークン・文字数・料金・時間を比較できる。

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

## 保存期間・バックアップ・復元

| 対象 | 保存期間 | バックアップ |
| --- | --- | --- |
| アプリ DB（会話・回答・計測値の正本） | 無期限（非表示の会話も残す） | 毎日、R2 に 30 世代 |
| Langfuse の Postgres（ユーザー・プロジェクト・API キー） | 無期限 | 毎日、R2 に 30 世代 |
| `.env`、`langfuse/.env` | — | 上記と同じ世代に含める（`ENCRYPTION_KEY` 等がないと復元できないため） |
| Langfuse のトレース（ClickHouse） | 無期限（セルフホスト OSS 版に保持期間設定がない）。1年後または 1GB 超で見直す | 対象外（計測値の正本はアプリ DB） |
| ClickHouse 自身の動作ログ（`system.*_log`） | 7日（`langfuse/clickhouse/system-log-ttl.xml`） | 対象外 |

バックアップは `scripts/backup.sh` で取る。pg_dump（custom 形式）→ age 暗号化 → Cloudflare R2 の
`<R2_BUCKET>/openai-query-app/<UTC時刻>/` へ保存し、30 日より古い世代はこのプレフィックス配下だけ削除する
（バケットは kaikei と共用のため、バケットのライフサイクル設定は変更しない）。

```bash
scripts/backup.sh                       # R2 へ（aws CLI と ~/.aws/credentials が必要）
scripts/backup.sh --local ./backups     # ローカル保存のみ（手動確認用）
```

`.env` に設定する値:

| 変数 | 内容 |
| --- | --- |
| `BACKUP_PUBLIC_KEY` | age 公開鍵（必須。kaikei と共通。復号鍵はサーバーに置かない） |
| `R2_BUCKET` / `R2_ENDPOINT` | R2 のバケット名とエンドポイント（エンドポイントにバケット名を含めない） |
| `R2_PREFIX` | 保存先プレフィックス（既定 `openai-query-app`） |
| `BACKUP_RETENTION_DAYS` | 保持日数（既定 30） |
| `BACKUP_HEARTBEAT_URL` | Healthchecks.io の Ping URL（任意。成功時のみ送る） |

日次の自動実行（systemd timer）と Healthchecks.io 登録は t430-server 移行時に設定する。

### 復元テスト（3か月ごと）

本番 DB には触れず、一時 DB（`oqa_restore_check`）へ復元して件数を確認し、終了時に削除する。

```bash
aws s3 cp s3://<R2_BUCKET>/openai-query-app/<STAMP>/ ./restore --recursive --endpoint-url <R2_ENDPOINT>
scripts/restore_check.sh ./restore <age 秘密鍵ファイル>
```

### 本番への復元

1. 世代を取得して復号する（`age -d -i <秘密鍵> -o app.dump app.dump.age` など）。`sha256sum -c SHA256SUMS` で照合する。
2. `env.tar` から `.env` / `langfuse/.env` を戻す（新しい環境の場合）。
3. 空の DB を用意して起動し、アプリ DB を戻す。

   ```bash
   docker compose stop backend
   docker compose exec -T db dropdb -U app app
   docker compose exec -T db createdb -U app app
   docker compose exec -T db pg_restore --no-owner -U app -d app < app.dump
   docker compose start backend
   ```

4. Langfuse は web / worker を止めてから同様に `postgres` DB を戻す（`-U postgres -d postgres`）。
   ClickHouse のトレースは戻らないため、Langfuse 上の過去トレースは表示されなくなる。
