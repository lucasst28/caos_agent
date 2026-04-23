"""Weather Client - External Environmental Data API."""

import structlog
import time
from typing import Any
from caos.config import get_settings
from caos.integration.base import BaseClient

logger = structlog.get_logger(__name__)

# TTL cache: avoids redundant HTTP calls for the same location
_weather_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 600  # 10 minutes


class WeatherClient(BaseClient):
    """Client for the External Weather API."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialize Weather client."""
        settings = get_settings()
        url = base_url or settings.weather_api_url
        super().__init__(base_url=url)

    async def get_forecast(self, location: str) -> dict[str, Any]:
        """Get current weather/forecast for a location.
        
        Results are cached for 10 minutes per location to avoid
        redundant API calls (same asset/location within a pipeline).
        
        Args:
            location: The location string (e.g. city or outlet name)
            
        Returns:
            Dict with temperature, condition, etc.
        """
        cache_key = location.strip().lower()
        now = time.monotonic()

        # Check cache
        if cache_key in _weather_cache:
            cached_at, cached_data = _weather_cache[cache_key]
            if now - cached_at < _CACHE_TTL_SECONDS:
                logger.debug("weather_cache_hit", location=location, age_s=round(now - cached_at, 1))
                return cached_data

        logger.info("weather_get_forecast", location=location)
        try:
            response = await self.get(f"v1/forecast", params={"location": location})
            # Store in cache
            _weather_cache[cache_key] = (now, response)
            return response
        except Exception as e:
            logger.error("weather_get_forecast_error", location=location, error=str(e))
            fallback = {"error": str(e), "temperature": 25.0}  # Neutral fallback
            return fallback

def get_weather_client() -> WeatherClient:
    """Get Weather client singleton (via registry)."""
    from caos._registry import get, put
    client = get("weather_client")
    if client is None:
        client = WeatherClient()
        put("weather_client", client)
    return client
