"""Sentinel Client - Alert Generation and Enrichment API.

Sentinel provides:
- Alert generation and validation
- Alert enrichment with context
- Scenario-based alert triggers
"""

import structlog
from typing import Any

from caos.config import get_settings
from caos.integration.base import BaseClient

logger = structlog.get_logger(__name__)


class SentinelClient(BaseClient):
    """Client for the Sentinel Agent API."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialize Sentinel client."""
        settings = get_settings()
        url = base_url or settings.sentinel_api_url
        super().__init__(base_url=url, timeout=5.0)

    async def generate_alert(
        self,
        asset_id: str,
        metric: str,
        severity: str,
        value: float | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Generate/enrich an alert through Sentinel.
        
        Args:
            asset_id: Asset identifier
            metric: Metric name (temperature, pressure, etc.)
            severity: Alert severity (LOW, MEDIUM, HIGH, CRITICAL)
            value: Current metric value
            tenant_id: Tenant identifier for proper attribution
            
        Returns:
            Enriched alert data or None if unavailable
        """
        logger.info(
            "sentinel_generate_alert",
            asset_id=asset_id,
            metric=metric,
            severity=severity,
            tenant_id=tenant_id,
        )
        
        try:
            data = {
                "asset_id": asset_id,
                "metric": metric,
                "severity": severity,
                "value": value,
            }
            if tenant_id:
                data["tenant_id"] = tenant_id

            response = await self.post(
                "v1/alerts/generate",
                data=data,
            )
            
            logger.info(
                "sentinel_alert_received",
                asset_id=asset_id,
                alert_id=response.get("alert_id"),
                severity=response.get("severity"),
                value=response.get("value"),
            )
            
            return response
        
        except Exception as e:
            logger.warning(
                "sentinel_alert_error",
                asset_id=asset_id,
                error=str(e),
            )
            return None


def get_sentinel_client() -> SentinelClient:
    """Get Sentinel client singleton (via registry)."""
    from caos._registry import get, put
    client = get("sentinel_client")
    if client is None:
        client = SentinelClient()
        put("sentinel_client", client)
    return client
