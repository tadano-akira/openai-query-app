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
| 機密情報検知 | Microsoft Presidio Analyzer 2.2.362（送信前の検査） |
| 実行環境 | Windows + Docker Desktop（ソースは Windows 側、コンテナは Linux） |

```
compose.yaml          アプリ一式（db / backend / worker / presidio / frontend）
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

# 5. ログイン用アカウントを作る（パスワードは 12 文字以上、入力は表示されない）
docker compose exec backend python -m app.cli create-user you@example.com
```

| 用途 | URL |
| --- | --- |
| アプリ | http://localhost:5173 |
| API ドキュメント | http://localhost:8000/docs |
| Langfuse | http://localhost:3000 （ログイン情報は `langfuse/.env` の `LANGFUSE_INIT_USER_*`） |

ポートはすべて 127.0.0.1 にバインドしている。

## ログイン

アプリはメールアドレス＋パスワードでログインして使う（利用者は本人 1 名の想定）。画面からの新規登録はない。

```powershell
docker compose exec backend python -m app.cli create-user you@example.com   # 作成
docker compose exec backend python -m app.cli set-password you@example.com  # 変更（全端末をログアウト）
docker compose exec backend python -m app.cli logout-all you@example.com    # 全端末をログアウト
docker compose exec backend python -m app.cli list-users                    # 一覧
```

- パスワードは Argon2 のハッシュだけを保存する。12 文字以上。
- ログイン状態は Cookie（HttpOnly、SameSite=Lax）で保持し、最後に使ってから 30 日で切れる。DB にはトークンの SHA-256 だけを保存する。
- 5 回続けて失敗すると 15 分ロックする。パスワード違いと未登録のメールアドレスは区別しない。
- 更新系 API はヘッダー `X-OQA-Request: 1` が必須（他サイトからの送信を拒否する）。
- HTTPS で配信するとき（t430）は `.env` に `COOKIE_SECURE=true` を設定する。外部公開時は Cloudflare Access を併用する。

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

## 資料Q&A（PDF 根拠提示型 RAG、段階 1〜5）

画面左上の「資料Q&A」で、公式の決算資料 PDF を登録・解析・レビューし、根拠を原本上でハイライト表示する。
設計は [docs/PDF_Evidence_RAG_System_Design.md](docs/PDF_Evidence_RAG_System_Design.md)（v1.2）に従う。今回は段階 1〜3（登録・抽出・レビュー・ハイライト）で、
段階 4（検索）・段階 5（根拠を選ぶ回答と検証）まで実装済み。計算（段階 6）以降はまだない。OCR は使わない。

1. **PDFを登録**: 企業（証券コード）・資料名・種別・対象期・連結区分・公表日・公式の掲載元 URL を入力してアップロードする。
   原本は内容の SHA-256 をキーに `pdf_data` volume へそのまま保存する（元のファイル名は使わない）。同じ企業に同じ PDF は登録できない。
2. **解析**: `worker` コンテナが PyMuPDF で文字・行・座標・表を抽出する（子プロセス、10 分で打ち切り、1 並列）。
   暗号化 PDF は拒否、全ページが画像だけなら「文字抽出不可」。再解析は新しい抽出版を作り、旧抽出版と根拠 ID は変えない。
3. **公式性の確認**: 版の詳細画面で掲載元 URL と内容を確認して「確認済み」にする（監査記録に残る）。
4. **レビュー・承認**: 原本と抽出結果を並べ、行・セルを選ぶと両側で強調する。セルの見出し（項目・期）は自動推定。
   承認後に「既定にする」と、その抽出版が今後の検索対象になる。
5. **根拠の表示**: `/viewer/evidence/{根拠ID}` で原本の該当ページを開き、値を黄色・見出しを青で強調する（PDF / HTML 切替）。

- 座標は PDF user space（CropBox ∩ MediaBox 基準）で保存し、PDF.js の viewport で回転・トリミング・拡大率を反映する。
  回転 0/90/180/270・CropBox・MediaBox 原点ずれ・横長で自動テスト済み。
- 回転したページの表は、検出のときだけ回転を外して抽出する（PyMuPDF の表検出は回転後の向きで動くため）。
- 表の列見出しは、上から金額らしい値が出るまでの行（最大 3 行）とみなす。行見出しは 1 列目。
- PDF.js の文字コード対応表・標準フォントは `/pdfjs/` からアプリ自身が配信する（外部 CDN を使わない）。
- 原本 PDF はバックアップ（`pdfs.tar`）に含め、復元テストでファイル名（SHA-256）と内容の一致を確かめる。
- PyMuPDF は AGPL-3.0。非公開・個人利用の現在の形態では問題ないが、配布・サービス提供する場合は再確認する。

### 検索（段階 4）

6. **索引**: 抽出版を「既定にする」と、`worker` がチャンクを作り、OpenAI の埋め込み（`EMBEDDING_MODEL`、既定 `text-embedding-3-small`）を
   pgvector に保存する。版の詳細画面で状態の確認・作り直しができる。トヨタの 1Q 短信（19 ページ）で 150 チャンク・約 $0.0014。
7. **検索（試験）**: 承認済み・既定・公式性確認済み・索引作成済みの抽出版だけを対象に、字句検索（pg_trgm の類似度＋語の出現、IDF で重み付け）と
   ベクトル検索（完全探索）の上位 30 件ずつを RRF（k=60）で統合し、上位 12 件を返す。結果から原本上のチャンクの位置を開ける。

- チャンク: 本文はページ内で約 500 トークン（上限 800、重なり 100）。表は表題・単位・列見出しを各チャンクに再掲し、
  行を「行見出し | 列見出し: 値」で並べる。ページ見出し（多くのページに繰り返し出る行）は除き、改ページした表は表題を引き継ぐ。
- 均等割付の「研 究 開 発 費」のような語は、検索用の文字列と質問の両方で空白を詰めて照合する。
- 同義語辞書は `backend/search/synonyms.json`（例: 有利子負債 → 借入金・社債・リース負債）。同義語は指標への算入を意味しない。
- 質問は埋め込みのため OpenAI へ送るので、チャットと同じ機密情報検知・利用上限を通す。埋め込みの推定料金は利用状況（1 日・1 か月の金額）に含める。
- 検索ごとに対象の抽出版（スコープ）と順位を `search_runs` / `retrieval_hits` に記録する。
- 評価: `docker compose exec backend python -m app.cli eval-search search/eval/toyota_2027q1.json`
  （20 問。2026-10-18 時点で Recall@12 = 100%、MRR = 0.818。設計書の目標は Recall@12 ≥ 95%）。
- 字句検索の類似度（pg_trgm）は、IDF の低い語（会社名など、ほとんどのチャンクに出る語）を除いた質問の語で測る。
  質問全体で測ると「トヨタの営業利益は？」のような短い質問が「トヨタ」を含む本文に引っ張られるため。
- DB のイメージは pgvector 入り（`pgvector/pgvector:0.8.6-pg17-trixie`。公式 postgres:17 と同じ Debian 13 で、データはそのまま使える）。

### 回答（段階 5）

8. **質問する**（`/docs/answer`）: 質問・企業の絞り込み・モデルを指定して送る。処理は次の順で、各段階を `answer_runs` に記録する。
   1. 対象企業の解決: 質問中の企業名（`backend/search/companies.json` の別名と登録企業名）で検索対象を絞る。
      資料が登録されていない企業（例: ホンダ）を問われたら、検索も LLM も呼ばずに「根拠不足」で返す。
      企業名がなく検索対象に複数企業がある場合は「確認が必要」で返す。
   2. 検索（段階 4）。ヒットした表が複数チャンクに分かれている場合は、同じ表の別チャンクで質問の語を含むものも候補に足す
      （例: 有利子負債は流動負債・非流動負債の両方に行がある）。
   3. 根拠の選択: 候補（表の値のセル・本文の行）に run 内だけの ID（E1…）を振り、行見出し・区分・列見出し・単位・資料名を添えて LLM に渡す。
      LLM は strict JSON schema で「ID・種類・短い説明」と計算の要否だけを返す。値・金額は書かせない。指示は `backend/search/answer_instructions.md`。
   4. 検証: 候補にない ID、重複、表の値でないものを数値として選ぶ、説明に根拠の見出しにない数字（金額など）を書く、
      根拠なしの回答、計算対象の番号の誤り、などがあれば出力ごと不採用にし、理由を添えて 1 回だけ作り直させる。それでも不合格なら「失敗」。
   5. 表示: 値・単位・行列見出し・ページはサーバーが承認済み抽出版の原文から組み立てる。各値から `/viewer/evidence/{根拠ID}` で原本を開ける。
- 状態: 回答（completed）／要確認（needs_review: 合算などの計算が必要。計算は段階 6 で対応し、今は個々の値と根拠だけを示す）／
  根拠不足（insufficient_evidence）／確認が必要（needs_clarification）／失敗（failed）。
- 回答の主張は `claims`、根拠との対応（値と、文脈として行・列見出し・単位・表題）は `claim_evidence` に残す。
- LLM の呼び出しは `llm_calls`（purpose `answer_selection`）に推定料金つきで記録し、利用上限に含める。Langfuse には `doc-answer` トレースとして送る。
- 実測（gpt-5.5、トヨタ 1Q 短信）: 1 問あたり約 4〜10 秒、推定 $0.03〜0.06。

## 送信前の機密情報検知（Presidio）

OpenAI へ送る前に、プロンプトに秘密鍵・アクセストークン・API キー等が含まれていないかを
Microsoft Presidio Analyzer（`presidio` コンテナ、ホストには非公開）で検査し、検知したら送信をブロックする。
個人情報（氏名・メールアドレス等）は検知対象外。

- 検知ルールは `backend/app/services/secret_scan.py` の `PATTERNS`。Presidio 標準の PII 認識器は使わず、
  このルールを ad_hoc_recognizers として毎回渡す（大文字小文字は区別する）。
- 対象: 秘密鍵（SSH / TLS / PGP / PuTTY / age）、AWS・GitHub・GitLab・Stripe・Google（API キー・OAuth クライアントシークレット）・
  Slack・Brevo・npm・Hugging Face・PyPI・DigitalOcean・SendGrid・Shopify・Telegram のキーやトークン、
  `sk-` 形式の API キー（OpenAI・Anthropic・Langfuse 等）、JWT、パスワード付き接続文字列、
  `password = 値` のような代入（関数呼び出し・環境変数参照・`${VAR}`・`<placeholder>` は除く）。
- 接頭辞のないランダムなトークンは、Presidio で候補（英数字・記号が 20 文字以上続く部分）を抽出し、
  アプリ側（`is_random_token`）で文字の散らばり（エントロピー）・文字種・英単語らしさから判定する。
  - 英大文字・小文字・数字が混ざるものは、前後の文脈なしで検知する（20〜44 文字で 97〜100%）。
  - 16 進数や小文字＋数字だけのものは、直前 40 文字に key / token / password / キー / トークン等があるときだけ検知する
    （コミットハッシュ・チェックサムと区別できないため）。
  - 単語をつないだ識別子・パス・UUID・バージョン番号・SSH 公開鍵は検知しない。
  - base64 でエンコードしたデータなど、ランダムに見える長い文字列は検知されることがある。
- ブロック時は画面に種類と伏せ字（先頭数文字と文字数）を表示する。入力内容は入力欄に残る。
  会話履歴・実行記録・Langfuse には何も残さない。
- 検知の記録は `secret_detections` テーブルに残す（種類・位置・確からしさ・文字数のみ。値そのものは保存しない）。
  アプリログにも種類だけを WARNING で出す。
- Presidio に接続できないときは送信を止める（`SECRET_SCAN_FAIL_OPEN=true` で検査なしの送信に切り替え）。
- 再実行でも同じ検査をする。
- メモリは Presidio だけで約 0.8〜1GB 使う（2026-10-18 実測、起動直後 0.8GB・検査後 0.95GB）。

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

## キャラクター（回答の名前・画像）

回答に表示する名前と画像は `backend/character/` で設定する。見た目だけで、モデルへの指示やトークン数には影響しない。
ファイルを差し替えると画面の再読み込みで反映され、再起動は不要。

`backend/character/character.json`:

```json
{
  "name": "アシスタント",
  "avatar": "avatar.webp",
  "portrait": "portrait.webp"
}
```

| 項目 | 表示場所 | 推奨 |
| --- | --- | --- |
| `name` | 各回答の上と立ち絵の下（40 文字まで） | — |
| `avatar` | 各回答の左のアイコン（角丸四角） | 顔を切り抜いた正方形（160px 程度） |
| `portrait` | チャット欄の右の立ち絵（画面幅 1280px 以上のときだけ表示） | 縦長 2:3（640×960px 程度） |

- 画像は `backend/character/` 直下に置く（png / jpg / webp / gif / svg）。それ以外の場所のファイルは配信しない。
- 設定が無い・読めない場合は名前を「アシスタント」とし、アイコンは名前の頭文字、立ち絵は表示しない。
- ヘッダーの「キャラクター」スイッチで表示を切り替えられる。OFF のときは顔（アイコン・立ち絵）を出さず、名前を「AI」と表示する（閲覧者のブラウザに保存）。

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
| `SECRET_SCAN_ENABLED` | `true` | 送信前の機密情報検知 |
| `PRESIDIO_URL` | `http://presidio:3000` | Presidio Analyzer の URL |
| `SECRET_SCAN_MIN_SCORE` | `0.5` | この確からしさ以上をブロック |
| `SECRET_SCAN_FAIL_OPEN` | `false` | Presidio 停止時に検査なしで送るか |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 資料Q&A の検索に使う埋め込みモデル（変えたら索引を作り直す） |
| `MAX_PDF_BYTES` / `MAX_PDF_PAGES` | `52428800` / `500` | 資料Q&A の PDF の上限 |
| `PARSER_TIMEOUT_SECONDS` | `600` | 1 文書の解析時間の上限 |
| `SESSION_DAYS` | `30` | 最後に使ってからログイン状態が切れるまでの日数 |
| `COOKIE_SECURE` | `false` | `true` で Cookie を HTTPS 限定にする（HTTPS 配信時は必須） |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCK_MINUTES` | `5` / `15` | 連続失敗回数とロック時間 |
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
