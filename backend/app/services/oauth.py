"""OAuth 2.0 authorization-code + PKCE flow for Google (Microsoft Entra ID sign-in is commented out)."""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.connectors.base import ConnectorAuthError, ConnectorError, http_client

logger = logging.getLogger(__name__)


@dataclass
class OAuthProfile:
    subject: str  # stable provider user id
    email: str
    name: str
    email_verified: bool


class OAuthProvider:
    name: str
    authorize_url: str
    token_url: str
    scopes: list

    @property
    def client_id(self) -> str: ...

    @property
    def client_secret(self) -> str: ...

    @property
    def redirect_uri(self) -> str: ...

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def extra_auth_params(self) -> Dict[str, str]:
        return {}

    def authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **self.extra_auth_params(),
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return await http_client().request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise ConnectorError(f"{self.name} sign-in service is unreachable. Try again in a moment.") from exc

    async def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        payload = {"client_id": self.client_id, "client_secret": self.client_secret, **data}
        res = await self._send("POST", self.token_url, data=payload)
        if res.status_code != 200:
            body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
            error = body.get("error", "")
            logger.warning("%s token endpoint error %s: %s", self.name, res.status_code, error)
            if error in ("invalid_grant", "interaction_required", "consent_required"):
                raise ConnectorAuthError(f"{self.name} authorization expired or was revoked. Reconnect your account.")
            raise ConnectorError(f"{self.name} sign-in failed")
        return res.json()

    async def exchange_code(self, code: str, code_verifier: str) -> Dict[str, Any]:
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        })

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def profile(self, access_token: str) -> OAuthProfile: ...


# Microsoft sign-in is turned off: only Google sign-in is used. To turn it back on, uncomment this class,
# its PROVIDERS entry below, the MICROSOFT_* settings, the /auth/login and /auth/callback routes in
# app/api/auth.py and the Microsoft button in the frontend.
# class MicrosoftOAuth(OAuthProvider):
#     name = "Microsoft"
#     # Files.Read covers the signed-in user's own OneDrive, which is all the app reads.
#     scopes = ["openid", "profile", "email", "offline_access", "User.Read", "Files.Read"]
#
#     @property
#     def authorize_url(self) -> str:
#         return f"https://login.microsoftonline.com/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/authorize"
#
#     @property
#     def token_url(self) -> str:
#         return f"https://login.microsoftonline.com/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
#
#     @property
#     def client_id(self) -> str:
#         return settings.MICROSOFT_CLIENT_ID
#
#     @property
#     def client_secret(self) -> str:
#         return settings.MICROSOFT_CLIENT_SECRET
#
#     @property
#     def redirect_uri(self) -> str:
#         return settings.MICROSOFT_REDIRECT_URI
#
#     def extra_auth_params(self) -> Dict[str, str]:
#         return {"response_mode": "query"}
#
#     async def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
#         return await super()._token_request({**data, "scope": " ".join(self.scopes)})
#
#     async def profile(self, access_token: str) -> OAuthProfile:
#         res = await self._send("GET", "https://graph.microsoft.com/v1.0/me", headers={"Authorization": f"Bearer {access_token}"})
#         if res.status_code != 200:
#             raise ConnectorError("Could not read the Microsoft profile")
#         data = res.json()
#         email = data.get("mail") or data.get("userPrincipalName") or ""
#         # Graph's mail attribute is not verified by Microsoft, so it is never used to link accounts.
#         return OAuthProfile(subject=data["id"], email=email.lower(), name=data.get("displayName") or "", email_verified=False)


class GoogleOAuth(OAuthProvider):
    name = "Google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    @property
    def client_id(self) -> str:
        return settings.GOOGLE_CLIENT_ID

    @property
    def client_secret(self) -> str:
        return settings.GOOGLE_CLIENT_SECRET

    @property
    def redirect_uri(self) -> str:
        return settings.GOOGLE_REDIRECT_URI

    def extra_auth_params(self) -> Dict[str, str]:
        # Google only issues a refresh token with offline access on a consent screen, so consent is
        # requested on every sign-in: re-signing in then always repairs an expired or revoked grant.
        return {"access_type": "offline", "include_granted_scopes": "true", "prompt": "consent"}

    async def profile(self, access_token: str) -> OAuthProfile:
        res = await self._send("GET", "https://openidconnect.googleapis.com/v1/userinfo",
                               headers={"Authorization": f"Bearer {access_token}"})
        if res.status_code != 200:
            raise ConnectorError("Could not read the Google profile")
        data = res.json()
        return OAuthProfile(subject=data["sub"], email=(data.get("email") or "").lower(),
                            name=data.get("name") or "", email_verified=bool(data.get("email_verified")))


PROVIDERS: Dict[str, OAuthProvider] = {
    # "microsoft": MicrosoftOAuth(),  # Microsoft sign-in is turned off
    "google": GoogleOAuth(),
}


def get_provider(name: str) -> Optional[OAuthProvider]:
    return PROVIDERS.get(name)
