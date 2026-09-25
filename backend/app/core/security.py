import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def _get_fernet_key(raw_key: str) -> bytes:
    """Generate a valid 32-byte urlsafe base64 key from any string."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_cipher = Fernet(_get_fernet_key(settings.TOKEN_ENCRYPTION_KEY))


def encrypt_token(token: str) -> str:
    """Encrypt a sensitive token (e.g. OAuth refresh token) for database storage."""
    if not token:
        return ""
    return _cipher.encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a stored token."""
    if not encrypted_token:
        return ""
    try:
        return _cipher.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def create_access_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Generate a JWT session token for authenticated client requests."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode: Dict[str, Any] = {"sub": str(subject), "exp": expire}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT session token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None
