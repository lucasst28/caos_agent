"""Base HTTP Client for external API calls."""

import structlog
from typing import Any
import httpx

logger = structlog.get_logger(__name__)


class BaseClient:
    """Base HTTP client with common configuration."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the client.
        
        Args:
            base_url: Base URL for the API
            timeout: Request timeout in seconds
            headers: Additional headers to include
        """
        self.base_url = base_url
        self.timeout = timeout
        self.headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self.headers,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Async context manager exit — ensures client is closed."""
        await self.close()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request."""
        client = await self._get_client()
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("http_get_error", path=path, status=e.response.status_code)
            raise
        except httpx.RequestError as e:
            logger.error("http_request_error", path=path, error=str(e))
            raise

    async def post(
        self, path: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a POST request."""
        client = await self._get_client()
        try:
            response = await client.post(path, json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("http_post_error", path=path, status=e.response.status_code)
            raise
        except httpx.RequestError as e:
            logger.error("http_request_error", path=path, error=str(e))
            raise
