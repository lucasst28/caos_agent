"""Oracle Client - Predictions and Simulations API.

Oracle provides:
- Failure probability predictions
- Financial impact estimates
- Recommended actions
- What-if scenario simulations
"""

import structlog
from typing import Any

from caos.config import get_settings
from caos.integration.base import BaseClient
from caos.schemas.state import OraclePrediction

logger = structlog.get_logger(__name__)


class OracleClient(BaseClient):
    """Client for the Oracle Agent API."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialize Oracle client."""
        settings = get_settings()
        url = base_url or settings.oracle_api_url
        super().__init__(base_url=url, timeout=5.0)  # Shorter timeout for Oracle

    async def get_prediction(
        self,
        tenant_id: str,
        asset_id: str,
        metric: str = "failure_probability",
        horizon_hours: int = 24,
    ) -> OraclePrediction | None:
        """Get prediction for an asset.
        
        Args:
            tenant_id: Tenant identifier
            asset_id: Asset identifier
            metric: Metric to predict
            horizon_hours: Prediction horizon
            
        Returns:
            OraclePrediction or None if unavailable
        """
        logger.info(
            "oracle_get_prediction",
            tenant_id=tenant_id,
            asset_id=asset_id,
            metric=metric,
        )
        
        try:
            response = await self.post(
                f"/v1/predict",
                data={
                    "tenant_id": tenant_id,
                    "asset_id": asset_id,
                    "metric": metric,
                    "horizon_hours": horizon_hours,
                },
            )
            
            prediction: OraclePrediction = {
                "forecast_metric": response.get("metric", metric),
                "predicted_value": response.get("value", 0.0),
                "confidence": response.get("confidence", 0.0),
                "horizon_hours": response.get("horizon", horizon_hours),
                "financial_impact": response.get("financial_impact"),
                "recommendation": response.get("recommendation"),
            }
            
            logger.info(
                "oracle_prediction_received",
                tenant_id=tenant_id,
                asset_id=asset_id,
                value=prediction["predicted_value"],
                confidence=prediction["confidence"],
            )
            
            return prediction
        
        except Exception as e:
            logger.warning(
                "oracle_prediction_error",
                tenant_id=tenant_id,
                asset_id=asset_id,
                error=str(e),
            )
            return None

    async def simulate_scenario(
        self,
        tenant_id: str,
        asset_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Simulate a what-if scenario.
        
        Args:
            tenant_id: Tenant identifier
            asset_id: Asset identifier
            action: Action to simulate
            params: Action parameters
            
        Returns:
            Simulation results
        """
        try:
            return await self.post(
                f"/v1/simulate",
                data={
                    "tenant_id": tenant_id,
                    "asset_id": asset_id,
                    "action": action,
                    "params": params or {},
                },
            )
        except Exception as e:
            logger.error("oracle_simulate_error", error=str(e))
            return {"error": str(e)}


def get_oracle_client() -> OracleClient:
    """Get Oracle client singleton (via registry)."""
    from caos._registry import get, put
    client = get("oracle_client")
    if client is None:
        client = OracleClient()
        put("oracle_client", client)
    return client
