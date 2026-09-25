import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, encrypt_token
from app.db.database import get_db
from app.models.user import OAuthAccount, User
from app.services.microsoft_graph import graph_service
from app.services.google_drive import google_drive_service

router = APIRouter(prefix="/auth", tags=["Authentication & Cloud OAuth"])
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency to retrieve the authenticated user from the JWT Bearer token."""
    # In dev mode with no token provided, return active user or default demo user
    if not credentials:
        if settings.ENVIRONMENT == "development" or settings.DEV_MOCK_ONEDRIVE or settings.DEV_MOCK_GDRIVE:
            stmt = (
                select(User)
                .join(OAuthAccount, OAuthAccount.user_id == User.id, isouter=True)
                .order_by(OAuthAccount.created_at.desc().nullslast(), User.created_at.desc())
            )
            res = await db.execute(stmt)
            user = res.scalars().first()
            if not user:
                user = User(
                    email="demo@contoso.com",
                    full_name="Demo Enterprise User",
                    microsoft_id="ms_demo_user_123",
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required.",
        )

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )

    user_id = payload["sub"]
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


@router.get("/login")
async def login_with_microsoft(redirect_uri: Optional[str] = None):
    """
    Returns the Microsoft OAuth 2.0 authorization URL or instant dev session URL.
    """
    redirect = redirect_uri or settings.MICROSOFT_REDIRECT_URI
    state = str(uuid.uuid4())

    if settings.DEV_MOCK_ONEDRIVE:
        # Return mock dev login flow URL
        return {
            "auth_url": f"{redirect}?code=mock_dev_code_123&state={state}",
            "mode": "mock",
            "message": "Development Sandbox Mode active. Automatic authorization enabled.",
        }

    auth_url = graph_service.get_auth_url(redirect_uri=redirect, state=state)
    return {"auth_url": auth_url, "mode": "production"}


@router.get("/callback")
async def oauth_callback(
    code: str = Query(...),
    state: Optional[str] = Query(None),
    redirect_uri: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchanges Microsoft auth code for tokens, creates/updates user, and returns JWT session.
    """
    redirect = redirect_uri or settings.MICROSOFT_REDIRECT_URI

    if settings.DEV_MOCK_ONEDRIVE or code.startswith("mock_"):
        # Create or fetch mock user
        stmt = select(User).where(User.email == "demo@contoso.com")
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                email="demo@contoso.com",
                full_name="Demo Enterprise User",
                microsoft_id="ms_demo_user_123",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        session_token = create_access_token(user.id, {"email": user.email, "name": user.full_name})
        return {
            "token": session_token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
            },
            "connected_to_onedrive": True,
        }

    try:
        tokens = await graph_service.exchange_code(code=code, redirect_uri=redirect)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        profile = await graph_service.get_user_profile(access_token)

        email = profile.get("mail") or profile.get("userPrincipalName")
        full_name = profile.get("displayName", "")
        ms_id = profile.get("id")

        stmt = select(User).where(User.microsoft_id == ms_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            stmt = select(User).where(User.email == email)
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()

        if not user:
            user = User(email=email, full_name=full_name, microsoft_id=ms_id)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            user.full_name = full_name
            user.microsoft_id = ms_id

        # Save / Update encrypted tokens
        stmt = select(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "microsoft")
        res = await db.execute(stmt)
        oauth_acc = res.scalar_one_or_none()
        if not oauth_acc:
            oauth_acc = OAuthAccount(
                user_id=user.id,
                provider="microsoft",
                encrypted_access_token=encrypt_token(access_token),
                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                token_type=tokens.get("token_type", "Bearer"),
                scope=tokens.get("scope"),
            )
            db.add(oauth_acc)
        else:
            oauth_acc.encrypted_access_token = encrypt_token(access_token)
            if refresh_token:
                oauth_acc.encrypted_refresh_token = encrypt_token(refresh_token)

        await db.commit()

        session_token = create_access_token(user.id, {"email": user.email, "name": user.full_name})
        return {
            "token": session_token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
            },
            "connected_to_onedrive": True,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


@router.get("/google/login")
async def login_with_google(redirect_uri: Optional[str] = None):
    """
    Returns the Google OAuth 2.0 authorization URL or instant dev session URL.
    """
    redirect = redirect_uri or settings.GOOGLE_REDIRECT_URI
    state = str(uuid.uuid4())

    if settings.DEV_MOCK_GDRIVE:
        return {
            "auth_url": f"{redirect}?code=mock_google_code_123&state={state}",
            "mode": "mock",
            "message": "Development Google Sandbox Mode active. Automatic authorization enabled.",
        }

    auth_url = google_drive_service.get_auth_url(redirect_uri=redirect, state=state)
    return {"auth_url": auth_url, "mode": "production"}


@router.get("/google/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: Optional[str] = Query(None),
    redirect_uri: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchanges Google auth code for tokens, creates/updates user, and returns JWT session.
    """
    redirect = redirect_uri or settings.GOOGLE_REDIRECT_URI

    if settings.DEV_MOCK_GDRIVE or code.startswith("mock_"):
        stmt = select(User).where(User.email == "demo@contoso.com")
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                email="demo@contoso.com",
                full_name="Demo Enterprise User",
                google_id="g_demo_user_123",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        session_token = create_access_token(user.id, {"email": user.email, "name": user.full_name})
        return {
            "token": session_token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
            },
            "connected_to_gdrive": True,
        }

    try:
        tokens = await google_drive_service.exchange_code(code=code, redirect_uri=redirect)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        profile = await google_drive_service.get_user_profile(access_token)

        email = profile.get("email")
        full_name = profile.get("name", "")
        google_id = profile.get("id")

        stmt = select(User).where(User.google_id == google_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        if not user:
            stmt = select(User).where(User.email == email)
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()

        if not user:
            user = User(email=email, full_name=full_name, google_id=google_id)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            user.full_name = full_name or user.full_name
            user.google_id = google_id

        # Save / Update encrypted tokens
        stmt = select(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google")
        res = await db.execute(stmt)
        oauth_acc = res.scalar_one_or_none()
        if not oauth_acc:
            oauth_acc = OAuthAccount(
                user_id=user.id,
                provider="google",
                encrypted_access_token=encrypt_token(access_token),
                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                token_type=tokens.get("token_type", "Bearer"),
                scope=tokens.get("scope"),
            )
            db.add(oauth_acc)
        else:
            oauth_acc.encrypted_access_token = encrypt_token(access_token)
            if refresh_token:
                oauth_acc.encrypted_refresh_token = encrypt_token(refresh_token)

        await db.commit()

        session_token = create_access_token(user.id, {"email": user.email, "name": user.full_name})
        return {
            "token": session_token,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
            },
            "connected_to_gdrive": True,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google authentication failed: {str(e)}")


@router.get("/me")
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the authenticated user and their cloud storage connection status."""
    stmt = select(OAuthAccount.provider).where(OAuthAccount.user_id == current_user.id)
    res = await db.execute(stmt)
    providers = set(res.scalars().all())

    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "onedrive_connected": ("microsoft" in providers) or settings.DEV_MOCK_ONEDRIVE,
        "gdrive_connected": ("google" in providers) or settings.DEV_MOCK_GDRIVE,
        "has_google_oauth": "google" in providers,
        "has_microsoft_oauth": "microsoft" in providers,
        "sandbox_mode": settings.DEV_MOCK_ONEDRIVE or settings.DEV_MOCK_GDRIVE,
    }


@router.post("/logout")
async def logout():
    return {"status": "success", "message": "Successfully logged out."}

