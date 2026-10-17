import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

ALGORITHM = "HS256"


class TokenDecryptionError(Exception):
    """A stored secret could not be decrypted (wrong TOKEN_ENCRYPTION_KEY or corrupted value)."""


class OAuthStateError(Exception):
    """The OAuth state parameter is missing, forged, expired, or for another provider."""


def _fernet_key(raw_key: str) -> bytes:
    """Derives a valid Fernet key from an arbitrary-length secret."""
    return base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())


_cipher = Fernet(_fernet_key(settings.TOKEN_ENCRYPTION_KEY))


def encrypt_token(token: str) -> str:
    """Encrypts an OAuth token for storage at rest."""
    return _cipher.encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: Optional[str]) -> Optional[str]:
    """Decrypts a stored OAuth token. Returns None for an empty value, raises if undecryptable."""
    if not encrypted_token:
        return None
    try:
        return _cipher.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenDecryptionError("Stored OAuth token cannot be decrypted") from exc


def create_access_token(user_id: str, token_version: int) -> str:
    """Issues a session JWT. `token_version` lets logout revoke every outstanding token."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    except jwt.PyJWTError:
        return None


def create_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def create_oauth_state(provider: str, code_verifier: str, link_user_id: Optional[str] = None) -> str:
    """
    Builds an encrypted, expiring OAuth `state` that carries the PKCE verifier (and, when a signed-in
    user connects another drive, their id), so the callback needs no server-side session. The SPA
    also keeps the state in sessionStorage and refuses callbacks it did not start (login CSRF).
    """
    payload = json.dumps({"p": provider, "v": code_verifier, "u": link_user_id, "n": secrets.token_urlsafe(16)})
    return _cipher.encrypt(payload.encode("utf-8")).decode("ascii")


def verify_oauth_state(state: Optional[str], provider: str) -> tuple[str, Optional[str]]:
    """Validates the state; returns (PKCE code verifier, user id to link or None)."""
    if not state:
        raise OAuthStateError("Missing OAuth state")
    try:
        payload = json.loads(_cipher.decrypt(state.encode("ascii"), ttl=settings.OAUTH_STATE_TTL_SECONDS))
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise OAuthStateError("Invalid or expired OAuth state") from exc
    if payload.get("p") != provider or not payload.get("v"):
        raise OAuthStateError("OAuth state does not match this provider")
    return payload["v"], payload.get("u")
