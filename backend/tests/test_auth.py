import pytest

from app.services import auth


def test_password_hash_roundtrip_and_minimum_length():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("$argon2")
    assert auth.verify_password(h, "correct horse battery")
    assert not auth.verify_password(h, "wrong password!!")
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_verify_password_tolerates_broken_hash():
    assert not auth.verify_password("not-a-hash", "whatever password")


def test_token_hash_is_stable_and_not_the_token():
    t = "abc"
    assert auth.token_hash(t) == auth.token_hash(t)
    assert auth.token_hash(t) != t and len(auth.token_hash(t)) == 64


def test_normalize_email():
    assert auth.normalize_email("  You@Example.COM ") == "you@example.com"
