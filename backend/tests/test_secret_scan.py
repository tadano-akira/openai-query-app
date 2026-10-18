"""機密情報の検知ルールのテスト（値はすべて架空）。

Presidio の PatternRecognizer と同じ条件（global_regex_flags）で正規表現を照合し（scan_locally）、
Presidio を起動せずにルールの検知・非検知を確かめる。Presidio 実機との結合は手動確認（README 参照）。
"""

import random
import re
import string

import pytest

from app.config import Settings
from app.services import chat, secret_scan

_rnd = random.Random(7)


def _r(n: int, chars: str = string.ascii_letters + string.digits) -> str:
    return "".join(_rnd.choice(chars) for _ in range(n))


MIN_SCORE = Settings.model_fields["secret_scan_min_score"].default
B62 = string.ascii_letters + string.digits
HEX = "0123456789abcdef"


def _detect(text: str) -> set[str]:
    return {f.entity for f in secret_scan.scan_locally(text, MIN_SCORE)}


POSITIVE = [
    ("PRIVATE_KEY", f"-----BEGIN OPENSSH PRIVATE KEY-----\n{_r(64)}\n-----END OPENSSH PRIVATE KEY-----"),
    ("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIE" + _r(40)),
    ("PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----"),
    ("PRIVATE_KEY", "-----BEGIN ENCRYPTED PRIVATE KEY-----"),
    ("PRIVATE_KEY", "-----BEGIN PGP PRIVATE KEY BLOCK-----"),
    ("PRIVATE_KEY", "PuTTY-User-Key-File-3: ssh-ed25519"),
    ("AGE_SECRET_KEY", "AGE-SECRET-KEY-1" + _r(58, string.ascii_uppercase + string.digits)),
    ("AWS_ACCESS_KEY", "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"),
    ("AWS_SECRET_KEY", "aws_secret_access_key = " + _r(40, string.ascii_letters + string.digits + "/+")),
    ("GITHUB_TOKEN", "token ghp_" + _r(36)),
    ("GITHUB_TOKEN", "github_pat_" + _r(82, string.ascii_letters + string.digits + "_")),
    ("GITLAB_TOKEN", "glpat-" + _r(20)),
    ("API_SECRET_KEY", "OPENAI_API_KEY=sk-proj-" + _r(48)),
    ("API_SECRET_KEY", "sk-ant-api03-" + _r(40)),
    ("STRIPE_KEY", "sk_live_" + _r(24)),
    ("GOOGLE_API_KEY", "AIza" + _r(35)),
    ("SLACK_TOKEN", "xoxb-" + _r(12, string.digits) + "-" + _r(24)),
    ("SLACK_WEBHOOK", "https://hooks.slack.com/services/T0ABCDEF1/B0ABCDEF2/" + _r(24)),
    ("JWT", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + _r(43)),
    ("CONNECTION_STRING", "postgresql://app:" + _r(16) + "@db:5432/app"),
    ("CONNECTION_STRING", "AccountKey=" + _r(88) + "=="),
    ("GENERIC_SECRET", '{"password": "' + _r(14) + '"}'),
    # 値がランダムなら、ランダムトークンとして検知されてもよい（重なりは確からしさの高い方を残す）
    (("GENERIC_SECRET", "RANDOM_TOKEN"), "api_key: " + _r(32)),
    (("GENERIC_SECRET", "RANDOM_TOKEN"), "Authorization: Bearer " + _r(40)),
    ("GOOGLE_OAUTH_SECRET", '"client_secret": "GOCSPX-' + _r(28) + '"'),
    ("SAAS_TOKEN", "xkeysib-" + _r(64, HEX) + "-" + _r(16)),
    ("SAAS_TOKEN", "xsmtpsib-" + _r(64, HEX) + "-" + _r(16)),
    ("SAAS_TOKEN", "//registry.npmjs.org/:_authToken=npm_" + _r(36)),
    ("SAAS_TOKEN", "hf_" + _r(34, string.ascii_letters)),
    ("SAAS_TOKEN", "pypi-AgEIcHlwaS5vcmc" + _r(60)),
    ("SAAS_TOKEN", "dop_v1_" + _r(64, HEX)),
    ("SAAS_TOKEN", "SG." + _r(22) + "." + _r(43)),
    ("SAAS_TOKEN", "shpat_" + _r(32, HEX)),
    ("SAAS_TOKEN", "123456789:AA" + _r(33)),
    # 接頭辞のないランダムなトークン
    ("RANDOM_TOKEN", "この値で接続して " + _r(32) + " お願い"),
    ("RANDOM_TOKEN", "Cloudflare: " + _r(40, B62 + "_-")),
    ("RANDOM_TOKEN", "LANGFUSE_SECRET_KEY は " + _r(32, HEX)),
    ("RANDOM_TOKEN", "APIトークン: " + _r(32, string.ascii_lowercase + string.digits)),
]

NEGATIVE = [
    "パスワードを安全に管理する方法を教えてください。APIキーはどこに保存するべき？",
    "SSHの秘密鍵と公開鍵の違いは？ ssh-keygen -t ed25519 で作れますか？",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI" + _r(43) + " user@host",
    "https://finance.yahoo.co.jp/quote/7203.T?utm_source=openai",
    "password = os.environ['DB_PASSWORD']\napi_key = get_key()",
    "const apiKey = process.env.OPENAI_API_KEY;\napi_key: ${OPENAI_API_KEY}",
    "password: <your-password-here>\nAPI_KEY=<your-api-key>",
    "DATABASE_URL: postgresql+psycopg://app:${POSTGRES_PASSWORD:?}@db:5432/app",
    "password: 必須項目です",
    "akiaiosfodnn7example（小文字は AWS キーではない）",
    "sk-8 は型番です。task-manager と skill-creator",
    "id=3149b0e5-aca2-4b7b-9313-8d9c6214f843 sha=955af8b223c1",
    "連絡先 taro@example.com 090-1234-5678（個人情報は対象外）",
    # ランダムトークンと紛らわしいが検知しないもの
    "getUser2FactorAuthenticationStatus と ConversationSidebarComponent2 を直す",
    "createUserSession2FA / handleOAuth2CallbackRequest / Base64EncodeUtility2026",
    "MAX_CONTEXT_CHARS_LIMIT_2026 と test_build_request_search_with_domain2",
    "frontend/src/components/AssistantFrame.tsx を開く",
    "コミット a3f9c2d1e4b5f6a7b8c9d0e1f2a3b4c5d6e7f8a9 を見て",
    "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "pip install langfuse==4.17.0 fastapi==0.143.0 sqlalchemy==2.1.4",
    "mcr.microsoft.com/presidio-analyzer:2.2.362 と python:3.13-slim-bookworm-20261001",
    "abcdefghijklmnopqrstuvwxyz0123456789 と ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
    "20261018T095336Z_backup_generation_01",
    "APIキーの設定は config_value_name_2026 にあります",
    "clientId: 326390304321-abc123def456ghi789jkl012mno345pq.apps.googleusercontent.com",
]


@pytest.mark.parametrize(("entity", "text"), POSITIVE)
def test_detects_secrets(entity, text):
    allowed = set(entity) if isinstance(entity, tuple) else {entity}
    assert allowed & _detect(text)


@pytest.mark.parametrize(
    ("chars", "length", "context", "min_rate"),
    [
        (B62, 20, "", 0.93),
        (B62, 32, "", 0.95),
        (B62 + "-_", 43, "", 0.97),
        (HEX, 32, "token: ", 0.95),
        (string.ascii_lowercase + string.digits, 32, "APIキー ", 0.93),
    ],
)
def test_random_token_detection_rate(chars, length, context, min_rate):
    rnd = random.Random(length)
    tokens = ["".join(rnd.choice(chars) for _ in range(length)) for _ in range(400)]
    rate = sum("RANDOM_TOKEN" in _detect(f"{context}{t} です") for t in tokens) / len(tokens)
    assert rate >= min_rate


def test_hex_without_keyword_is_not_detected():
    rnd = random.Random(1)
    tokens = ["".join(rnd.choice(HEX) for _ in range(40)) for _ in range(200)]
    assert not any(_detect(f"コミット {t} を見て") for t in tokens)


@pytest.mark.parametrize("text", NEGATIVE)
def test_ignores_ordinary_text(text):
    assert _detect(text) == set()


def test_ad_hoc_recognizers_cover_all_entities_case_sensitively():
    recs = secret_scan.ad_hoc_recognizers()
    assert {r["supported_entity"] for r in recs} == set(secret_scan.ENTITY_LABELS)
    for r in recs:
        assert not r["global_regex_flags"] & re.IGNORECASE
        for p in r["patterns"]:
            re.compile(p["regex"])


def test_mask_keeps_only_a_short_prefix():
    secret = "sk-proj-" + _r(48)
    masked = secret_scan.mask(secret)
    assert masked.startswith("sk-p") and secret[4:12] not in masked and str(len(secret)) in masked
    assert secret_scan.mask("pwd=abcdefgh") == "p…（12文字）"


def test_overlapping_findings_keep_the_most_confident():
    f = secret_scan.Finding
    merged = secret_scan._merge(
        [
            f("GENERIC_SECRET", "x", 0, 30, 0.6, "a"),
            f("API_SECRET_KEY", "y", 10, 40, 0.9, "b"),
            f("JWT", "z", 50, 60, 0.8, "c"),
        ]
    )
    assert [m.entity for m in merged] == ["API_SECRET_KEY", "JWT"]


def _service(**settings) -> chat.ChatService:
    s = Settings(database_url="postgresql+psycopg://x", **settings)
    return chat.ChatService(s, chat.Tracer(s))


class _NoDb:
    added: list

    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


async def test_blocks_and_records_without_secret_value(monkeypatch):
    secret = "sk-proj-" + _r(48)
    finding = secret_scan.Finding(
        "API_SECRET_KEY", "API シークレットキー（sk-）", 4, 4 + len(secret), 0.9, "sk-p…"
    )

    async def fake_scan(settings, text):
        return [finding]

    db = _NoDb()
    monkeypatch.setattr(chat.secret_scan, "scan", fake_scan)
    monkeypatch.setattr(chat, "SessionLocal", lambda: db)
    with pytest.raises(chat.ChatError) as e:
        await _service()._scan_secrets(f"key {secret}", chat.uuid.uuid4(), "message")
    assert e.value.status_code == 422 and e.value.code == "secret_detected"
    assert e.value.extra["findings"][0]["masked"] == "sk-p…"
    # 記録・応答のどこにも機密情報そのものを含めない
    assert secret not in repr(e.value.extra) and secret not in e.value.message
    assert len(db.added) == 1 and secret not in repr(db.added[0].findings)


@pytest.mark.parametrize(("fail_open", "blocked"), [(False, True), (True, False)])
async def test_presidio_unavailable(monkeypatch, fail_open, blocked):
    async def down(settings, text):
        raise secret_scan.ScanUnavailable("connection refused")

    monkeypatch.setattr(chat.secret_scan, "scan", down)
    svc = _service(secret_scan_fail_open=fail_open)
    if blocked:
        with pytest.raises(chat.ChatError) as e:
            await svc._scan_secrets("hello", chat.uuid.uuid4(), "message")
        assert e.value.status_code == 503 and e.value.code == "secret_scan_unavailable"
    else:
        await svc._scan_secrets("hello", chat.uuid.uuid4(), "message")
