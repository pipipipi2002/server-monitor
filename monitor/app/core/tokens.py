"""Bearer token generation + bcrypt verification.

Tokens are 32 random bytes encoded as URL-safe base64 (≈ 256 bits).
Storing a bcrypt hash means the SQLite DB does not contain plaintext tokens.
"""

from __future__ import annotations

import os
import secrets

import bcrypt


def generate_token() -> str:
    """Return a fresh URL-safe random token."""
    return secrets.token_urlsafe(32)


def _cost() -> int:
    return int(os.environ.get("BCRYPT_COST", "10"))


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=_cost())).decode("ascii")


def verify_token(token: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(token.encode("utf-8"), stored_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False
