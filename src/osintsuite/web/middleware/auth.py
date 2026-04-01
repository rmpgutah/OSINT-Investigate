"""JWT authentication middleware using RMPG Flex shared secret."""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from osintsuite.config import get_settings

logger = logging.getLogger(__name__)

# Routes that don't require authentication
PUBLIC_PATHS = {"/login", "/health", "/auth/callback", "/auth/logout"}
PUBLIC_PREFIXES = ("/static",)


def verify_jwt_token(token: str, secret: str) -> Optional[dict]:
    """Decode and verify a RMPG Flex JWT token.

    Returns the payload dict if valid, None if invalid.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "userId", "username", "role"]},
        )
        # Reject non-access tokens
        if payload.get("type") not in ("access", None):
            logger.warning(f"Rejected non-access token type: {payload.get('type')}")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT validation failed: {e}")
        return None


def get_token_from_request(request: Request) -> Optional[str]:
    """Extract JWT token from cookie or Authorization header."""
    # Check cookie first (browser sessions)
    token = request.cookies.get("osint_token")
    if token:
        return token

    # Check Authorization header (API calls)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


def get_current_user(request: Request) -> Optional[dict]:
    """Get the current authenticated user from the request.

    Returns user dict with userId, username, role, fullName or None.
    """
    settings = get_settings()
    secret = settings.rmpg_jwt_secret
    if not secret:
        # No secret configured — auth disabled (development mode)
        return {"userId": 0, "username": "dev", "role": "admin", "fullName": "Developer"}

    token = get_token_from_request(request)
    if not token:
        return None

    return verify_jwt_token(token, secret)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces JWT authentication on all routes except public ones."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Strip root_path prefix for path matching
        root_path = request.scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]

        # Allow public paths
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # Check authentication
        settings = get_settings()
        if not settings.rmpg_jwt_secret:
            # Auth disabled — pass through
            return await call_next(request)

        user = get_current_user(request)
        if not user:
            # Check if this is an API request or browser request
            accept = request.headers.get("Accept", "")
            if "application/json" in accept or path.startswith("/api/"):
                raise HTTPException(status_code=401, detail="Authentication required")

            # Browser request — redirect to login
            login_url = f"{root_path}/login"
            return RedirectResponse(url=login_url, status_code=302)

        # Attach user to request state for templates/handlers
        request.state.user = user
        return await call_next(request)
