from __future__ import annotations

from urllib.parse import urlencode

import httpx

from codey.saas.config import settings

# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


def oauth_github_url() -> str:
    """Return the full GitHub OAuth authorization URL."""
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": f"{settings.api_url}/auth/github/callback",
        "scope": "read:user user:email",
    }
    return f"{_GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_github_code(code: str) -> dict:
    """Exchange a GitHub OAuth code for an access token and fetch user info.

    Returns a dict with keys: ``id``, ``email``, ``name``, ``avatar_url``,
    ``access_token``.
    """
    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        token_resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data["access_token"]

        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        # Fetch user profile
        user_resp = await client.get(_GITHUB_USER_URL, headers=auth_headers)
        user_resp.raise_for_status()
        user_data = user_resp.json()

        # If email is not public, fetch from the emails endpoint
        email = user_data.get("email")
        if not email:
            emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=auth_headers)
            emails_resp.raise_for_status()
            emails = emails_resp.json()
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            if primary:
                email = primary["email"]

    return {
        "id": str(user_data["id"]),
        "email": email,
        "name": user_data.get("name") or user_data.get("login"),
        "avatar_url": user_data.get("avatar_url"),
        "access_token": access_token,
    }


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def oauth_google_url() -> str:
    """Return the full Google OAuth authorization URL."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.api_url}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{_GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_google_code(code: str) -> dict:
    """Exchange a Google OAuth code for an access token and fetch user info.

    Returns a dict with keys: ``id``, ``email``, ``name``, ``avatar_url``,
    ``access_token``.
    """
    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.api_url}/auth/google/callback",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data["access_token"]

        # Fetch user profile
        user_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_resp.raise_for_status()
        user_data = user_resp.json()

    return {
        "id": user_data["id"],
        "email": user_data["email"],
        "name": user_data.get("name"),
        "avatar_url": user_data.get("picture"),
        "access_token": access_token,
    }
