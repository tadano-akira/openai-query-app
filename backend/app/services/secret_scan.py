"""OpenAI へ送る前に、機密情報（秘密鍵・アクセストークン・API キー等）を Microsoft Presidio で検知する。

Presidio の標準の認識器は個人情報（PII）向けなので使わず、ここで定義した正規表現の認識器を
リクエストごとに ad_hoc_recognizers として渡し、entities をそれらに限定して検知させる。

接頭辞のないランダムなトークンは正規表現だけでは見分けられないため、Presidio で候補
（英数字・記号が 20 文字以上続く部分）を抽出し、ここでランダムさを判定して絞り込む（is_random_token）。

検知した内容そのものはログ・DB・Langfuse のどこにも残さない（種類・位置・伏せ字のみ）。
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

# Presidio の PatternRecognizer は既定で IGNORECASE を付けるため、大文字小文字を区別する（AKIA… 等の誤検知を防ぐ）
_REGEX_FLAGS = int(re.DOTALL | re.MULTILINE)


@dataclass(frozen=True)
class SecretPattern:
    entity: str
    label: str
    regex: str
    score: float


# 種類ごとの検知ルール。score はルールの確からしさ（SECRET_SCAN_MIN_SCORE 以上で検知扱い）
PATTERNS: list[SecretPattern] = [
    SecretPattern(
        "PRIVATE_KEY",
        "秘密鍵（SSH・TLS・PGP 等）",
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
        1.0,
    ),
    SecretPattern("PRIVATE_KEY", "秘密鍵（PuTTY）", r"PuTTY-User-Key-File-\d+:", 1.0),
    SecretPattern("AGE_SECRET_KEY", "age 秘密鍵", r"AGE-SECRET-KEY-1[0-9A-Z]{58}", 1.0),
    SecretPattern("AWS_ACCESS_KEY", "AWS アクセスキー", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", 0.9),
    SecretPattern(
        "AWS_SECRET_KEY",
        "AWS シークレットキー",
        r"(?i:aws).{0,20}(?i:secret).{0,20}[:=]\s*[\"']?[A-Za-z0-9/+=]{40}\b",
        0.9,
    ),
    SecretPattern("GITHUB_TOKEN", "GitHub トークン", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b", 1.0),
    SecretPattern("GITHUB_TOKEN", "GitHub トークン", r"\bgithub_pat_[A-Za-z0-9_]{60,}\b", 1.0),
    SecretPattern("GITLAB_TOKEN", "GitLab トークン", r"\bglpat-[A-Za-z0-9_-]{20,}\b", 1.0),
    # OpenAI（sk-proj- 等）・Anthropic（sk-ant-）・Langfuse（sk-lf-）などの sk- 形式
    SecretPattern(
        "API_SECRET_KEY", "API シークレットキー（sk-）", r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{19,}", 0.9
    ),
    SecretPattern("STRIPE_KEY", "Stripe キー", r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b", 1.0),
    SecretPattern(
        "GOOGLE_OAUTH_SECRET", "Google OAuth クライアントシークレット", r"\bGOCSPX-[A-Za-z0-9_-]{28}\b", 1.0
    ),
    SecretPattern(
        "SAAS_TOKEN", "Brevo（Sendinblue）キー", r"\bx(?:keysib|smtpsib)-[0-9a-f]{64}-[A-Za-z0-9]{16}\b", 1.0
    ),
    SecretPattern("SAAS_TOKEN", "npm トークン", r"\bnpm_[A-Za-z0-9]{36}\b", 1.0),
    SecretPattern("SAAS_TOKEN", "Hugging Face トークン", r"\bhf_[A-Za-z]{34}\b", 1.0),
    SecretPattern("SAAS_TOKEN", "PyPI トークン", r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}", 1.0),
    SecretPattern("SAAS_TOKEN", "DigitalOcean トークン", r"\bdo[por]_v1_[0-9a-f]{64}\b", 1.0),
    SecretPattern("SAAS_TOKEN", "SendGrid キー", r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b", 1.0),
    SecretPattern("SAAS_TOKEN", "Shopify トークン", r"\bshp(?:at|ca|pa|ss)_[0-9a-fA-F]{32}\b", 1.0),
    SecretPattern("SAAS_TOKEN", "Telegram ボットトークン", r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b", 0.9),
    SecretPattern("GOOGLE_API_KEY", "Google API キー", r"\bAIza[0-9A-Za-z_-]{35}\b", 0.9),
    SecretPattern("SLACK_TOKEN", "Slack トークン", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", 1.0),
    SecretPattern(
        "SLACK_WEBHOOK",
        "Slack Webhook URL",
        r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
        1.0,
    ),
    SecretPattern(
        "JWT",
        "JWT（アクセストークン）",
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
        0.8,
    ),
    SecretPattern(
        "CONNECTION_STRING",
        "パスワード付き接続文字列・URL",
        # パスワード部分が ${VAR} や <password> のようなプレースホルダーのものは除く
        r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s:/@${}<>]{3,}@[^\s/]+",
        0.7,
    ),
    SecretPattern("CONNECTION_STRING", "Azure ストレージキー", r"AccountKey=[A-Za-z0-9+/=]{40,}", 0.9),
    # 「password = xxx」のような代入。説明文の「パスワード」等には反応しない（英字のキー名＋値 8 文字以上）。
    # 値が関数呼び出し・os.environ[...]・process.env.X・${VAR}・<your-key> のような参照やプレースホルダーのものは除く
    SecretPattern(
        "GENERIC_SECRET",
        "パスワード・トークン等の値",
        r"(?i:\b(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|auth[_-]?token|bearer))"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;()\[\]{}.$<>]{8,}+(?![(\[.])",
        0.6,
    ),
    SecretPattern(
        "GENERIC_SECRET", "Bearer トークン", r"(?i:authorization:\s*bearer)\s+[A-Za-z0-9._~+/-]{20,}=*", 0.8
    ),
]

# 接頭辞のないランダムなトークンの候補。Presidio で抽出し、is_random_token で判定してから検知扱いにする
RANDOM_TOKEN = "RANDOM_TOKEN"
RANDOM_CANDIDATE = SecretPattern(
    RANDOM_TOKEN,
    "ランダムな文字列のトークン（推定）",
    r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{20,}(?![A-Za-z0-9_+/=-])",
    # 候補として Presidio のしきい値を通すための値。最終的な確からしさは is_random_token が決める
    0.5,
)

ENTITY_LABELS: dict[str, str] = {}
for _p in [*PATTERNS, RANDOM_CANDIDATE]:
    ENTITY_LABELS.setdefault(_p.entity, _p.label)

# 16 進数・小文字と数字だけ等の弱い候補は、直前にこれらの言葉があるときだけ検知する
# （コミットハッシュ・チェックサム等と区別できないため）
_SECRET_CONTEXT = re.compile(
    r"(?i:key|token|secret|passw|pwd|credential|auth|api|bearer)|鍵|キー|トークン|パスワード|シークレット|認証"
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_WORD_RUN = re.compile(r"[a-z]{5,}|[A-Z]{5,}")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def is_random_token(text: str, start: int, end: int) -> float | None:
    """候補がランダムなトークンらしければ確からしさ、そうでなければ None を返す。

    2026-10-18 の実験（架空データ）で、20〜44 文字の英数字ランダム文字列を 97〜100%、
    キーワードを伴う 16 進数・小文字＋数字のトークンを 97〜100% 検知し、識別子・パス・ハッシュ・
    UUID・バージョン番号・公開鍵など 23 パターンは検知しなかった値に合わせている。
    """
    s = text[start:end].strip("=-_")
    if len(s) < 20 or s.startswith("AAAA") or _UUID.fullmatch(s):  # AAAA… は SSH 公開鍵等の本体
        return None
    has_lower = any(c.islower() for c in s)
    has_upper = any(c.isupper() for c in s)
    has_digit = any(c.isdigit() for c in s)
    entropy = _entropy(s)
    # 同じ大文字小文字の英字が 5 文字以上続く部分の割合。単語をつないだ識別子ほど高い
    words = sum(len(m) for m in _WORD_RUN.findall(s)) / len(s)
    if has_lower and has_upper and (has_digit or words <= 0.2) and entropy >= 3.5 and words <= 0.4:
        return 0.7
    digits = sum(c.isdigit() for c in s) / len(s)
    near_keyword = bool(_SECRET_CONTEXT.search(text[max(0, start - 40) : start]))
    if near_keyword and len(s) >= 24 and digits >= 0.15 and (has_lower or has_upper) and entropy >= 3.2:
        return 0.6
    return None


def ad_hoc_recognizers() -> list[dict]:
    """Presidio の /analyze に渡す ad_hoc_recognizers（種類ごとに 1 つの PatternRecognizer）。"""
    by_entity: dict[str, list[SecretPattern]] = {}
    for p in [*PATTERNS, RANDOM_CANDIDATE]:
        by_entity.setdefault(p.entity, []).append(p)
    return [
        {
            "name": f"oqa_{entity.lower()}",
            "supported_language": "en",
            "supported_entity": entity,
            "global_regex_flags": _REGEX_FLAGS,
            "patterns": [
                {"name": f"{entity.lower()}_{i}", "regex": p.regex, "score": p.score}
                for i, p in enumerate(pats)
            ],
        }
        for entity, pats in by_entity.items()
    ]


@dataclass(frozen=True)
class Finding:
    entity: str
    label: str
    start: int
    end: int
    score: float
    # 先頭数文字だけを残した伏せ字（画面表示用）。機密情報の本体は含まない
    masked: str


class ScanUnavailable(Exception):
    pass


def mask(value: str) -> str:
    head = value[: min(4, max(1, len(value) // 8))]
    return f"{head}…（{len(value)}文字）"


def _merge(findings: list[Finding]) -> list[Finding]:
    """重なった検知は、確からしさが高いものを 1 つだけ残す。"""
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: (-x.score, x.start)):
        if all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda x: x.start)


def finalize(text: str, results: list[dict], min_score: float) -> list[Finding]:
    """Presidio の結果（entity_type / start / end / score）から検知結果を作る。

    ランダムトークンの候補は is_random_token で判定し、該当しないものは捨てる。重なりは確からしさで 1 つにする。
    """
    findings = []
    for r in results:
        entity = r.get("entity_type")
        if entity not in ENTITY_LABELS:
            continue
        start, end, score = r["start"], r["end"], float(r["score"])
        if entity == RANDOM_TOKEN:
            judged = is_random_token(text, start, end)
            if judged is None or judged < min_score:
                continue
            score = judged
        findings.append(
            Finding(
                entity=entity,
                label=ENTITY_LABELS[entity],
                start=start,
                end=end,
                score=score,
                masked=mask(text[start:end]),
            )
        )
    return _merge(findings)


def scan_locally(text: str, min_score: float) -> list[Finding]:
    """Presidio と同じ条件（global_regex_flags）で正規表現を照合する。テストと動作確認用。"""
    results = [
        {"entity_type": p.entity, "start": m.start(), "end": m.end(), "score": p.score}
        for p in [*PATTERNS, RANDOM_CANDIDATE]
        if p.score >= min_score
        for m in re.finditer(p.regex, text, _REGEX_FLAGS)
    ]
    return finalize(text, results, min_score)


async def scan(settings: Settings, text: str) -> list[Finding]:
    """機密情報を検知して返す。Presidio に接続できない場合は ScanUnavailable。"""
    if not settings.secret_scan_enabled or not text.strip():
        return []
    payload = {
        "text": text,
        "language": "en",
        "entities": sorted(ENTITY_LABELS),
        "ad_hoc_recognizers": ad_hoc_recognizers(),
        "score_threshold": settings.secret_scan_min_score,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.secret_scan_timeout_seconds) as client:
            res = await client.post(f"{settings.presidio_url.rstrip('/')}/analyze", json=payload)
            res.raise_for_status()
            results = res.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ScanUnavailable(str(e)) from e
    return finalize(text, results, settings.secret_scan_min_score)
