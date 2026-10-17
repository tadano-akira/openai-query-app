import json

import pytest

from app.services import character


@pytest.fixture
def char_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(character, "CHARACTER_DIR", tmp_path)
    return tmp_path


def _config(d, **data):
    (d / "character.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_bundled_character_has_name_and_images():
    c = character.load_character()
    assert c.name
    assert c.avatar is not None and c.avatar.version
    assert c.portrait is not None and c.portrait.version


def test_name_and_avatar(char_dir):
    (char_dir / "me.png").write_bytes(b"\x89PNG")
    (char_dir / "big.webp").write_bytes(b"RIFF")
    _config(char_dir, name="  ミライ  ", avatar="me.png", portrait="big.webp")
    c = character.load_character()
    assert c.name == "ミライ"
    assert c.image("avatar").path == (char_dir / "me.png").resolve()
    assert c.image("portrait").path == (char_dir / "big.webp").resolve()


def test_avatar_version_changes_with_content(char_dir):
    _config(char_dir, name="A", avatar="a.svg")
    (char_dir / "a.svg").write_text("<svg/>", encoding="utf-8")
    v1 = character.load_character().avatar.version
    (char_dir / "a.svg").write_text("<svg></svg>", encoding="utf-8")
    assert character.load_character().avatar.version != v1


@pytest.mark.parametrize("avatar", ["../secret.png", "missing.png", "notes.txt", "", None, 123])
def test_invalid_avatar_is_ignored(char_dir, avatar):
    (char_dir.parent / "secret.png").write_bytes(b"x")
    (char_dir / "notes.txt").write_text("x", encoding="utf-8")
    _config(char_dir, name="A", avatar=avatar, portrait=avatar)
    c = character.load_character()
    assert c.avatar is None and c.portrait is None


def test_missing_or_broken_config_falls_back(char_dir):
    assert character.load_character().name == character.DEFAULT_NAME
    (char_dir / "character.json").write_text("{broken", encoding="utf-8")
    c = character.load_character()
    assert c.name == character.DEFAULT_NAME and c.avatar is None and c.portrait is None
    _config(char_dir, name="x" * 100)
    assert len(character.load_character().name) == character.NAME_MAX
