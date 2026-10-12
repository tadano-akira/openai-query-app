#!/usr/bin/env bash
# アプリ DB・Langfuse 用 Postgres・.env 類をバックアップする。
#
#   1. pg_dump（custom 形式、DB コンテナ内で実行）でアプリ DB と Langfuse の Postgres を取得
#   2. 空でないこと・pg_restore --list で読めることを確認
#   3. .env と langfuse/.env を tar にまとめる（Langfuse の ENCRYPTION_KEY 等がないと復元しても使えないため）
#   4. 平文の SHA-256 を記録し、各ファイルを age 公開鍵で暗号化（復号鍵はサーバーに置かない）
#   5. Cloudflare R2 の <R2_PREFIX>/<STAMP>/ へアップロードしてサイズを確認
#   6. BACKUP_RETENTION_DAYS より古い世代を R2 から削除（バケットは他システムと共用のため、
#      バケットのライフサイクル設定ではなくこのプレフィックス配下だけを対象にする）
#   7. BACKUP_HEARTBEAT_URL があれば Healthchecks.io へ ping
#
# ClickHouse（Langfuse のトレース本体）は対象外。計測値の正本はアプリ DB にある。
#
# 使い方:
#   scripts/backup.sh                 R2 へアップロード（t430 の systemd timer から実行）
#   scripts/backup.sh --local <DIR>   <DIR>/<STAMP>/ へ保存のみ（aws CLI 不要、手動確認用）
#
# 設定は .env から読む: BACKUP_PUBLIC_KEY（必須）、R2_BUCKET / R2_ENDPOINT（R2 時必須）、
#   R2_PREFIX（既定 openai-query-app）、BACKUP_RETENTION_DAYS（既定 30）、BACKUP_HEARTBEAT_URL（任意）
# aws CLI の認証情報は ~/.aws/credentials で別管理する。
# 依存コマンド: docker, age, sha256sum, tar（R2 時は aws, curl も）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_DIR=""
if [[ "${1:-}" == "--local" ]]; then
  LOCAL_DIR="${2:?--local には保存先ディレクトリを指定してください}"
fi

# .env を source せず、必要なキーだけ読む（値をシェルとして評価しない）
env_value() {
  grep -E "^$1=" "$ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true
}
BACKUP_PUBLIC_KEY="${BACKUP_PUBLIC_KEY:-$(env_value BACKUP_PUBLIC_KEY)}"
R2_BUCKET="${R2_BUCKET:-$(env_value R2_BUCKET)}"
R2_ENDPOINT="${R2_ENDPOINT:-$(env_value R2_ENDPOINT)}"
R2_PREFIX="${R2_PREFIX:-$(env_value R2_PREFIX)}"
R2_PREFIX="${R2_PREFIX:-openai-query-app}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(env_value BACKUP_RETENTION_DAYS)}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_HEARTBEAT_URL="${BACKUP_HEARTBEAT_URL:-$(env_value BACKUP_HEARTBEAT_URL)}"

: "${BACKUP_PUBLIC_KEY:?.env に BACKUP_PUBLIC_KEY（age 公開鍵）を設定してください}"
if [[ -z "$LOCAL_DIR" ]]; then
  : "${R2_BUCKET:?.env に R2_BUCKET を設定してください}"
  : "${R2_ENDPOINT:?.env に R2_ENDPOINT を設定してください（バケット名は含めない）}"
fi

APP=(docker compose -f "$ROOT/compose.yaml")
LANGFUSE=(docker compose -f "$ROOT/langfuse/compose.yaml")

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

dump() { # <compose...> -- <service> <user> <db> <out>
  local -a compose=()
  while [[ "$1" != "--" ]]; do compose+=("$1"); shift; done
  shift
  local service="$1" user="$2" db="$3" out="$4"
  "${compose[@]}" exec -T "$service" pg_dump --format=custom --no-owner --no-privileges -U "$user" "$db" > "$out"
  test -s "$out"
  "${compose[@]}" exec -T "$service" pg_restore --list < "$out" > /dev/null
}

echo "[1/6] pg_dump: アプリ DB"
dump "${APP[@]}" -- db app app "$WORKDIR/app.dump"

echo "[2/6] pg_dump: Langfuse Postgres"
dump "${LANGFUSE[@]}" -- postgres postgres postgres "$WORKDIR/langfuse.dump"

echo "[3/6] .env 類"
tar -C "$ROOT" -cf "$WORKDIR/env.tar" .env langfuse/.env

echo "[4/6] SHA-256 と暗号化 (age)"
(cd "$WORKDIR" && sha256sum app.dump langfuse.dump env.tar > SHA256SUMS)
mkdir -p "$WORKDIR/out"
for f in app.dump langfuse.dump env.tar; do
  age -r "$BACKUP_PUBLIC_KEY" -o "$WORKDIR/out/$f.age" "$WORKDIR/$f"
done
cp "$WORKDIR/SHA256SUMS" "$WORKDIR/out/"

if [[ -n "$LOCAL_DIR" ]]; then
  echo "[5/6] ローカル保存: $LOCAL_DIR/$STAMP"
  mkdir -p "$LOCAL_DIR/$STAMP"
  cp "$WORKDIR/out/"* "$LOCAL_DIR/$STAMP/"
  ls -l "$LOCAL_DIR/$STAMP"
  echo "[6/6] ローカル保存のため世代削除・死活監視 ping は行わない"
  echo "backup ${STAMP} done (local)"
  exit 0
fi

DEST="s3://${R2_BUCKET}/${R2_PREFIX}/${STAMP}/"
echo "[5/6] R2 アップロード: ${DEST}"
aws s3 cp "$WORKDIR/out/" "$DEST" --recursive --endpoint-url "$R2_ENDPOINT"
uploaded="$(aws s3 ls "$DEST" --endpoint-url "$R2_ENDPOINT" | wc -l)"
if [[ "$uploaded" -ne 4 ]]; then
  echo "アップロード後のファイル数が想定と異なります: $uploaded" >&2
  exit 1
fi

echo "[6/6] ${BACKUP_RETENTION_DAYS} 日より古い世代を削除"
CUTOFF="$(date -u -d "-${BACKUP_RETENTION_DAYS} days" +%Y%m%dT%H%M%SZ)"
aws s3 ls "s3://${R2_BUCKET}/${R2_PREFIX}/" --endpoint-url "$R2_ENDPOINT" |
  awk '$1 == "PRE" { sub("/$", "", $2); print $2 }' |
  while read -r gen; do
    # STAMP 形式の世代だけを対象にし、文字列比較で古いものを消す
    if [[ "$gen" =~ ^[0-9]{8}T[0-9]{6}Z$ && "$gen" < "$CUTOFF" ]]; then
      echo "  delete ${gen}"
      aws s3 rm "s3://${R2_BUCKET}/${R2_PREFIX}/${gen}/" --recursive --endpoint-url "$R2_ENDPOINT" > /dev/null
    fi
  done

if [[ -n "$BACKUP_HEARTBEAT_URL" ]]; then
  curl -fsS -m 10 --retry 3 "$BACKUP_HEARTBEAT_URL" > /dev/null
fi
echo "backup ${STAMP} done"
