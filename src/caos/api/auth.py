"""API Authentication — API Key validation middleware.

Security layer for all mutating endpoints (trigger, feedback, delete).
Read-only endpoints (health, GET reasoning, dashboard) remain open.

Configuration:
    CAOS_API_KEY: Required API key for protected endpoints.
                  If not set, auth is DISABLED (dev mode) with a warning.

Usage in routers:
    from caos.api.auth import require_api_key
    
    @router.post("/endpoint", dependencies=[Depends(require_api_key)])
    async def my_endpoint(): ...
"""

import os
import secrets
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = structlog.get_logger(__name__)

# Header name for API key
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Loaded once at module level
_CONFIGURED_API_KEY: str | None = os.environ.get("CAOS_API_KEY")

if not _CONFIGURED_API_KEY:
    logger.warning(
        "auth_disabled",
        reason="CAOS_API_KEY not set — all endpoints are unprotected. "
               "Set CAOS_API_KEY env var to enable authentication.",
    )


async def require_api_key(
    api_key: Annotated[str | None, Security(_API_KEY_HEADER)] = None,
) -> str:
    """Dependency that validates the X-API-Key header.
    
    If CAOS_API_KEY is not configured, auth is bypassed (dev mode).
    In production, missing or invalid keys return 401.
    """
    # Dev mode: no key configured → allow all
    if not _CONFIGURED_API_KEY:
        return "dev-mode"
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    
    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(api_key, _CONFIGURED_API_KEY):
        logger.warning("auth_failed", provided_key_prefix=api_key[:4] + "***")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    
    return api_key
