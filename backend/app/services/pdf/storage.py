"""原本 PDF の保存。内容の SHA-256 から保存先を決め、元のファイル名は使わない。

一時ファイルに書き込みながらハッシュとサイズを確認し、問題がなければ保存先へ原子的に移動する。
同じ内容の PDF は同じ保存先になる（上書きはしない）。
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

PDF_SIGNATURE = b"%PDF-"
_CHUNK = 1024 * 1024


class StorageError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size: int
    storage_key: str


def storage_key(sha256: str) -> str:
    return f"originals/{sha256[:2]}/{sha256}.pdf"


def path_for(root: str, key: str) -> Path:
    path = (Path(root) / key).resolve()
    if Path(root).resolve() not in path.parents:
        raise ValueError("storage key outside root")
    return path


async def save_upload(upload: UploadFile, root: str, max_bytes: int) -> StoredFile:
    """アップロードを検査しながら保存する。PDF の署名とサイズ上限をここで確認する。"""
    tmp_dir = Path(root) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            first = True
            while chunk := await upload.read(_CHUNK):
                if first:
                    # 拡張子や Content-Type ではなく、先頭の署名で判定する（先頭 1KB 内にあればよい）
                    if PDF_SIGNATURE not in chunk[:1024]:
                        raise StorageError("INVALID_PDF", "PDF ファイルではありません")
                    first = False
                size += len(chunk)
                if size > max_bytes:
                    raise StorageError(
                        "FILE_LIMIT_EXCEEDED",
                        f"ファイルが大きすぎます（上限 {max_bytes // (1024 * 1024)} MiB）",
                        413,
                    )
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise StorageError("INVALID_PDF", "空のファイルです")
        sha = digest.hexdigest()
        key = storage_key(sha)
        dest = path_for(root, key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            os.unlink(tmp_name)
        else:
            os.replace(tmp_name, dest)
        return StoredFile(sha256=sha, size=size, storage_key=key)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
