"""システム指示（基本ルール＋モード）の読み込み。

backend/prompts/base.md が全モード共通の基本ルール、backend/prompts/modes/<id>.md が各モードの追加指示。
ファイルは小さいので実行ごとに読み直し、編集を再起動なしで反映する。
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
DEFAULT_MODE = "standard"

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_ALLOWED = {
    "verbosity": {"low", "medium", "high"},
    "reasoning_effort": {"minimal", "low", "medium", "high"},
}


class PromptError(ValueError):
    pass


class UnknownMode(PromptError):
    pass


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    description: str
    order: int
    body: str
    # 対応モデルにだけ付ける API パラメータ（None は指定しない）
    verbosity: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Instructions:
    mode: Mode
    text: str
    # 指示文と API パラメータから決まる版。実行ごとに記録して比較に使う
    version: str


def _read(path: Path) -> str:
    return _COMMENT.sub("", path.read_text(encoding="utf-8").replace("\r\n", "\n"))


def _parse_mode(path: Path) -> Mode:
    m = _FRONTMATTER.match(_read(path).lstrip("\n"))
    if not m:
        raise PromptError(f"{path.name}: 先頭に --- で囲んだ設定（label など）が必要です")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise PromptError(f"{path.name}: 設定行の形式が不正です: {line}")
        meta[key.strip()] = value.split(" #", 1)[0].strip()
    for key, allowed in _ALLOWED.items():
        if key in meta and meta[key] not in allowed:
            raise PromptError(f"{path.name}: {key} は {sorted(allowed)} のいずれかです")
    if "label" not in meta:
        raise PromptError(f"{path.name}: label がありません")
    return Mode(
        id=path.stem,
        label=meta["label"],
        description=meta.get("description", ""),
        order=int(meta.get("order", "100")),
        body=m.group(2).strip(),
        verbosity=meta.get("verbosity"),
        reasoning_effort=meta.get("reasoning_effort"),
    )


def load_modes() -> list[Mode]:
    modes = [_parse_mode(p) for p in (PROMPTS_DIR / "modes").glob("*.md")]
    if not any(m.id == DEFAULT_MODE for m in modes):
        raise PromptError(f"modes/{DEFAULT_MODE}.md がありません")
    return sorted(modes, key=lambda m: (m.order, m.id))


def load_base() -> str:
    text = _read(PROMPTS_DIR / "base.md").strip()
    if not text:
        raise PromptError("base.md が空です")
    return text


def build_instructions(mode_id: str | None) -> Instructions:
    """基本ルール＋モードの指示を組み立てる。存在しないモードは UnknownMode。"""
    modes = {m.id: m for m in load_modes()}
    mode = modes.get(mode_id or DEFAULT_MODE)
    if mode is None:
        raise UnknownMode(f"存在しないモードです: {mode_id}")
    text = load_base() + (f"\n\n{mode.body}" if mode.body else "")
    digest = hashlib.sha256(f"{text}\n{mode.verbosity}\n{mode.reasoning_effort}".encode()).hexdigest()
    return Instructions(mode=mode, text=text, version=digest[:12])
