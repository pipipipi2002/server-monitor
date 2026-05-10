"""Bearer token generation and verification."""

from __future__ import annotations


def test_generate_token_is_high_entropy_url_safe() -> None:
    from app.core.tokens import generate_token

    a = generate_token()
    b = generate_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_then_verify_succeeds() -> None:
    from app.core.tokens import generate_token, hash_token, verify_token

    t = generate_token()
    h = hash_token(t)
    assert verify_token(t, h) is True


def test_verify_rejects_wrong_token() -> None:
    from app.core.tokens import generate_token, hash_token, verify_token

    t = generate_token()
    h = hash_token(t)
    assert verify_token(generate_token(), h) is False


def test_verify_rejects_garbage_hash() -> None:
    from app.core.tokens import verify_token

    assert verify_token("anything", "not-a-bcrypt-hash") is False
