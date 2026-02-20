"""Weather Client - External Environmental Data API."""

import structlog
from typing import Any
from caos.config import get_settings
from caos.integration.base import BaseClient

logger = structlog.get_logger(__name__)

class WeatherClient(BaseClient):
    """Client for the External Weather API."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialize Weather client."""
        settings = get_settings()
        url = base_url or settings.weather_api_url
        super().__init__(base_url=url)

    async def get_forecast(self, location: str) -> dict[str, Any]:
        """Get current weather/forecast for a location.
        
        Args:
            location: The location string (e.g. city or outlet name)
            
        Returns:
            Dict with temperature, condition, etc.
        """
        logger.info("weather_get_forecast", location=location)
        try:
            response = await self.get(f"v1/forecast", params={"location": location})
            return response
        except Exception as e:
            logger.error("weather_get_forecast_error", location=location, error=str(e))
            return {"error": str(e), "temperature": 25.0} # Neutral fallback

def get_weather_client() -> WeatherClient:
    """Get Weather client singleton (via registry)."""
    from caos._registry import get, put
    client = get("weather_client")
    if client is None:
        client = WeatherClient()
        put("weather_client", client)
    return client
