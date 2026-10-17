import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    OAuthStateError,
    create_access_token,
    create_oauth_state,
    create_pkce_pair,
    decode_access_token,
    verify_oauth_state,
)
from app.db.database import get_db
from app.models.user import OAuthAccount, User
from app.services.connectors import ConnectorError
from app.services.oauth import OAuthProfile, get_provider
from app.services.oauth_tokens import apply_token_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)

SANDBOX_EMAIL = "demo@sandbox.local"
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"}
)


async def _user_from_token(credentials: Optional[HTTPAuthorizationCredentials], db: AsyncSession) -> Optional[User]:
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if not payload:
        return None
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active or payload.get("ver", 0) != user.token_version:
        return None
    return user


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await _user_from_token(credentials, db)
    if user is None:
        raise _UNAUTHORIZED
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    return await _user_from_token(credentials, db)


async def _linked_providers(db: AsyncSession, user_id: str) -> set:
    return set((await db.execute(select(OAuthAccount.provider).where(OAuthAccount.user_id == user_id))).scalars().all())


async def _profile(db: AsyncSession, user: User) -> dict:
    providers = await _linked_providers(db, user.id)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "onedrive_connected": "microsoft" in providers or settings.DEV_MOCK_ONEDRIVE,
        "gdrive_connected": "google" in providers or settings.DEV_MOCK_GDRIVE,
        "sandbox_mode": settings.sandbox_mode,
    }


async def _session(db: AsyncSession, user: User) -> dict:
    return {"token": create_access_token(user.id, user.token_version), "user": await _profile(db, user)}


def _login_url(provider_name: str, user: Optional[User]) -> dict:
    provider = get_provider(provider_name)
    if not provider.configured:
        raise HTTPException(status_code=503, detail=f"{provider.name} sign-in is not configured on this server.")
    verifier, challenge = create_pkce_pair()
    state = create_oauth_state(provider_name, verifier, user.id if user else None)
    return {"auth_url": provider.authorization_url(state, challenge), "state": state}


async def _resolve_user(db: AsyncSession, provider_name: str, profile: OAuthProfile, link_user_id: Optional[str]) -> User:
    id_column = User.google_id if provider_name == "google" else User.microsoft_id
    id_attr = "google_id" if provider_name == "google" else "microsoft_id"
    by_subject = (await db.execute(select(User).where(id_column == profile.subject))).scalar_one_or_none()

    if link_user_id:
        if by_subject and by_subject.id != link_user_id:
            raise HTTPException(status_code=409, detail="This account is already connected to a different user.")
        user = await db.get(User, link_user_id)
        if user is None:
            raise HTTPException(status_code=400, detail="Sign-in session expired. Please try again.")
        setattr(user, id_attr, profile.subject)
        return user

    if by_subject:
        if profile.name:
            by_subject.full_name = profile.name
        return by_subject

    email = profile.email or f"{profile.subject}@{provider_name}.invalid"
    by_email = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if by_email:
        # Only a provider-verified email may link to an existing account (prevents nOAuth-style takeover).
        if profile.email_verified and getattr(by_email, id_attr) is None:
            setattr(by_email, id_attr, profile.subject)
            return by_email
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Sign in with your original provider, "
                   "then connect this drive from the app.",
        )
    user = User(email=email, full_name=profile.name or None, **{id_attr: profile.subject})
    db.add(user)
    await db.flush()
    return user


async def _callback(provider_name: str, code: str, state: Optional[str], db: AsyncSession) -> dict:
    provider = get_provider(provider_name)
    try:
        verifier, link_user_id = verify_oauth_state(state, provider_name)
    except OAuthStateError:
        raise HTTPException(status_code=400, detail="Sign-in session expired or invalid. Please try again.")
    try:
        tokens = await provider.exchange_code(code, verifier)
        profile = await provider.profile(tokens["access_token"])
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user = await _resolve_user(db, provider_name, profile, link_user_id)
    account = (await db.execute(
        select(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == provider_name)
    )).scalar_one_or_none()
    if account is None:
        account = OAuthAccount(user_id=user.id, provider=provider_name, encrypted_access_token="")
        db.add(account)
    apply_token_response(account, tokens)
    await db.commit()
    logger.info("User %s signed in with %s", user.id, provider_name)
    return await _session(db, user)


@router.get("/login")
async def microsoft_login(user: Optional[User] = Depends(get_optional_user)):
    """Microsoft authorization URL. With a session token, the account is linked to the current user."""
    return _login_url("microsoft", user)


@router.get("/google/login")
async def google_login(user: Optional[User] = Depends(get_optional_user)):
    return _login_url("google", user)


@router.get("/callback")
async def microsoft_callback(code: str = Query(..., max_length=4096), state: Optional[str] = Query(None, max_length=4096),
                             db: AsyncSession = Depends(get_db)):
    return await _callback("microsoft", code, state, db)


@router.get("/google/callback")
async def google_callback(code: str = Query(..., max_length=4096), state: Optional[str] = Query(None, max_length=4096),
                          db: AsyncSession = Depends(get_db)):
    return await _callback("google", code, state, db)


@router.post("/sandbox")
async def sandbox_login(db: AsyncSession = Depends(get_db)):
    """Signs in to the shared sandbox account. Only available when a DEV_MOCK drive is enabled."""
    if not settings.sandbox_mode:
        raise HTTPException(status_code=404, detail="Not found")
    user = (await db.execute(select(User).where(User.email == SANDBOX_EMAIL))).scalar_one_or_none()
    if user is None:
        user = User(email=SANDBOX_EMAIL, full_name="Sandbox User")
        db.add(user)
        await db.commit()
    return await _session(db, user)


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _profile(db, user)


@router.post("/logout")
async def logout(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Revokes every session token issued to this user."""
    user.token_version += 1
    await db.commit()
    return {"status": "logged_out"}
