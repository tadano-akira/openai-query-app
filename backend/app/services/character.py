"""回答に表示するキャラクター（名前・画像）。見た目だけで、モデルへの指示には影響しない。

backend/character/character.json と画像ファイルで設定する。実行ごとに読み直すので、差し替えは再起動不要。
画像は 2 種類: avatar（各回答の横の小さな丸アイコン）と portrait（チャット欄の右に出す大きな立ち絵）。
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CHARACTER_DIR = Path(__file__).resolve().parents[2] / "character"
DEFAULT_NAME = "アシスタント"
NAME_MAX = 40

ImageKind = Literal["avatar", "portrait"]

# 画像として配信してよい拡張子と Content-Type
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class Image:
    path: Path
    # 画像の内容から作る版。URL に付けてブラウザのキャッシュを差し替え時に無効化する
    version: str


@dataclass(frozen=True)
class Character:
    name: str
    avatar: Image | None
    portrait: Image | None

    def image(self, kind: ImageKind) -> Image | None:
        return self.avatar if kind == "avatar" else self.portrait


def _image(file_name: object) -> Image | None:
    if not isinstance(file_name, str) or not file_name:
        return None
    path = (CHARACTER_DIR / file_name).resolve()
    # character/ 直下のファイルだけを許可する（../ 等で他のファイルを配信しない）
    if path.parent != CHARACTER_DIR.resolve() or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return None
    return Image(path=path, version=hashlib.sha256(path.read_bytes()).hexdigest()[:12])


def load_character() -> Character:
    """設定が無い・壊れている場合は既定の名前・画像なしで返す（画面表示を止めない）。"""
    try:
        data = json.loads((CHARACTER_DIR / "character.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    name = data.get("name")
    name = name.strip()[:NAME_MAX] if isinstance(name, str) and name.strip() else DEFAULT_NAME
    return Character(name=name, avatar=_image(data.get("avatar")), portrait=_image(data.get("portrait")))
