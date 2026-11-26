"""Local account authentication with per-user document isolation."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

import jwt
from fastapi import HTTPException, Request, status

from .config import settings

ITERATIONS = 240_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_token(user_id: str) -> str:
    if not settings.secret_key:
        raise RuntimeError("RESEARCHMIND_SECRET_KEY is not configured")
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + 7 * 24 * 3600},
        settings.secret_key,
        algorithm="HS256",
    )


def current_user(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.secret_key, algorithms=["HS256"], options={"require": ["sub", "exp"]}
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return str(claims["sub"])


def new_id() -> str:
    return uuid.uuid4().hex
