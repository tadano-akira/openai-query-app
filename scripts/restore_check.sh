#!/usr/bin/env bash
# バックアップ 1 世代を復号し、一時 DB へ復元できることを確認する（本番 DB には触れない）。
#
#   1. age 秘密鍵で復号し、SHA256SUMS と照合
#   2. env.tar に .env / langfuse/.env が含まれること、原本 PDF のファイル名（SHA-256）と内容が一致することを確認
#   3. アプリ DB・Langfuse Postgres をそれぞれ一時 DB へ pg_restore し、主要テーブルの件数を稼働中の DB と並べて表示
#   4. 一時 DB を削除
#
# 使い方: scripts/restore_check.sh <世代ディレクトリ> <age 秘密鍵ファイル>
#   R2 の世代は先に取得しておく:
#   aws s3 cp s3://<bucket>/openai-query-app/<STAMP>/ <dir> --recursive --endpoint-url <endpoint>
#
# 本番へ復元する手順は README の「バックアップ・復元」を参照。
set -euo pipefail

SRC="${1:?世代ディレクトリを指定してください}"
IDENTITY="${2:?age 秘密鍵ファイルを指定してください}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=(docker compose -f "$ROOT/compose.yaml")
LANGFUSE=(docker compose -f "$ROOT/langfuse/compose.yaml")
CHECK_DB=oqa_restore_check

WORKDIR="$(mktemp -d)"
cleanup() {
  "${APP[@]}" exec -T db dropdb -U app --if-exists "$CHECK_DB" > /dev/null 2>&1 || true
  "${LANGFUSE[@]}" exec -T postgres dropdb -U postgres --if-exists "$CHECK_DB" > /dev/null 2>&1 || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "[1/4] 復号と SHA-256 照合"
# 鍵ファイルにラベル等が同じ行に書かれていても使えるよう、鍵の文字列だけを取り出す（WORKDIR ごと削除される）
grep -o 'AGE-SECRET-KEY-[0-9A-Z]*' "$IDENTITY" > "$WORKDIR/identity" || {
  echo "age 秘密鍵が見つかりません: $IDENTITY" >&2
  exit 1
}
FILES=(app.dump langfuse.dump env.tar)
# 資料Q&A 導入前の世代には pdfs.tar がない
[[ -f "$SRC/pdfs.tar.age" ]] && FILES+=(pdfs.tar)
for f in "${FILES[@]}"; do
  age -d -i "$WORKDIR/identity" -o "$WORKDIR/$f" "$SRC/$f.age"
done
cp "$SRC/SHA256SUMS" "$WORKDIR/"
(cd "$WORKDIR" && sha256sum -c SHA256SUMS)

echo "[2/4] .env 類"
tar -tf "$WORKDIR/env.tar" | grep -qx ".env"
tar -tf "$WORKDIR/env.tar" | grep -qx "langfuse/.env"
echo "  .env / langfuse/.env を含む"

restore() { # <compose...> -- <service> <user> <dump>
  local -a compose=()
  while [[ "$1" != "--" ]]; do compose+=("$1"); shift; done
  shift
  local service="$1" user="$2" dump="$3"
  "${compose[@]}" exec -T "$service" dropdb -U "$user" --if-exists "$CHECK_DB" 2> /dev/null
  "${compose[@]}" exec -T "$service" createdb -U "$user" "$CHECK_DB"
  "${compose[@]}" exec -T "$service" pg_restore --no-owner --exit-on-error -U "$user" -d "$CHECK_DB" < "$dump"
}

counts() { # <compose...> -- <service> <user> <db> <tables...>
  local -a compose=()
  while [[ "$1" != "--" ]]; do compose+=("$1"); shift; done
  shift
  local service="$1" user="$2" db="$3"
  shift 3
  local sql="" t
  for t in "$@"; do sql+="select '$t', count(*) from \"$t\" union all "; done
  "${compose[@]}" exec -T "$service" psql -U "$user" -d "$db" -At -F ' ' -c "${sql% union all }"
}

compare() { # <label> <live> <restored>
  echo "  $1"
  paste -d ' ' <(echo "$2") <(echo "$3" | awk '{print $2}') |
    awk '{ printf "    %-14s 稼働中 %6s / 復元 %6s\n", $1, $2, $3 }'
}

if [[ -f "$WORKDIR/pdfs.tar" ]]; then
  mkdir -p "$WORKDIR/pdfs"
  tar -C "$WORKDIR/pdfs" -xf "$WORKDIR/pdfs.tar"
  n=0
  while IFS= read -r -d '' f; do
    name="$(basename "$f" .pdf)"
    actual="$(sha256sum "$f" | cut -d' ' -f1)"
    if [[ "$name" != "$actual" ]]; then
      echo "  原本 PDF のハッシュが一致しません: $f" >&2
      exit 1
    fi
    n=$((n + 1))
  done < <(find "$WORKDIR/pdfs" -type f -name '*.pdf' -print0)
  echo "  原本 PDF ${n} 件の SHA-256 が一致"
fi

echo "[3/4] 一時 DB へ復元"
restore "${APP[@]}" -- db app "$WORKDIR/app.dump"
APP_TABLES=(conversations turns messages runs api_calls documents document_versions extractions pages elements)
compare "アプリ DB" \
  "$(counts "${APP[@]}" -- db app app "${APP_TABLES[@]}")" \
  "$(counts "${APP[@]}" -- db app "$CHECK_DB" "${APP_TABLES[@]}")"

restore "${LANGFUSE[@]}" -- postgres postgres "$WORKDIR/langfuse.dump"
LF_TABLES=(organizations projects users api_keys)
compare "Langfuse Postgres" \
  "$(counts "${LANGFUSE[@]}" -- postgres postgres postgres "${LF_TABLES[@]}")" \
  "$(counts "${LANGFUSE[@]}" -- postgres postgres "$CHECK_DB" "${LF_TABLES[@]}")"

echo "[4/4] 一時 DB を削除"
echo "restore check OK: $SRC"
