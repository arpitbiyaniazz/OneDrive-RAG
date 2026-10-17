from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenDecryptionError, decrypt_token, encrypt_token
from app.models.user import OAuthAccount
from app.services.connectors import PROVIDER_FOR_DRIVE, ConnectorAuthError, is_sandbox
from app.services.oauth import get_provider

_REFRESH_MARGIN = timedelta(minutes=3)


def apply_token_response(account: OAuthAccount, tokens: Dict[str, Any]) -> None:
    """Stores a token endpoint response on the account (refresh tokens are only replaced when rotated)."""
    account.encrypted_access_token = encrypt_token(tokens["access_token"])
    if tokens.get("refresh_token"):
        account.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
    expires_in = int(tokens.get("expires_in") or 3600)
    account.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    account.token_type = tokens.get("token_type", "Bearer")
    if tokens.get("scope"):
        account.scope = tokens["scope"]


async def get_drive_token(db: AsyncSession, user_id: str, drive_type: str) -> str:
    """Returns a valid access token for the drive, refreshing it only when it is about to expire.

    The account row is locked during refresh so concurrent requests/jobs do not race on a rotating
    refresh token. Raises ConnectorAuthError when the user must (re)connect the drive.
    """
    if is_sandbox(drive_type):
        return "sandbox"
    provider_name = PROVIDER_FOR_DRIVE[drive_type]
    stmt = (
        select(OAuthAccount)
        .where(OAuthAccount.user_id == user_id, OAuthAccount.provider == provider_name)
        .with_for_update()
    )
    account: Optional[OAuthAccount] = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        await db.rollback()
        raise ConnectorAuthError("This drive is not connected. Connect it first.")
    try:
        access_token = decrypt_token(account.encrypted_access_token)
        refresh_token = decrypt_token(account.encrypted_refresh_token)
    except TokenDecryptionError as exc:
        await db.rollback()
        raise ConnectorAuthError("Stored credentials are unreadable. Reconnect the drive.") from exc

    expires_at = account.expires_at
    if access_token and expires_at and expires_at - _REFRESH_MARGIN > datetime.now(UTC):
        await db.commit()  # release the row lock
        return access_token
    if not refresh_token:
        await db.rollback()
        if access_token and expires_at is None:
            return access_token  # legacy row without expiry information; try it once
        raise ConnectorAuthError("The drive connection expired. Reconnect the drive.")

    provider = get_provider(provider_name)
    try:
        tokens = await provider.refresh(refresh_token)
    except ConnectorAuthError:
        await db.rollback()
        raise
    apply_token_response(account, tokens)
    await db.commit()
    return tokens["access_token"]
