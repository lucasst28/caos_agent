"""Atlas Client - Digital Twin and Context API.

Atlas provides:
- Digital Twin state (current sensor values)
- Technical manuals (RAG excerpts)
- Contract information (tier, SLA, allowed actions)
- Maintenance status
"""

import structlog
from typing import Any

from caos.config import get_settings
from caos.integration.base import BaseClient
from caos.schemas.state import AssetContext

logger = structlog.get_logger(__name__)


class AtlasClient(BaseClient):
    """Client for the Atlas Agent API."""

    def __init__(self, base_url: str | None = None) -> None:
        """Initialize Atlas client."""
        settings = get_settings()
        url = base_url or settings.atlas_api_url
        super().__init__(base_url=url)

    async def get_asset_context(
        self,
        tenant_id: str,
        asset_id: str,
    ) -> AssetContext:
        """Get full context for an asset.
        
        Args:
            tenant_id: Tenant identifier
            asset_id: Asset identifier (e.g., CHILLER-04)
            
        Returns:
            AssetContext with Digital Twin, manuals, and contract info
        """
        logger.info(
            "atlas_get_context",
            tenant_id=tenant_id,
            asset_id=asset_id,
        )
        
        try:
            response = await self.get(
                f"/v1/tenants/{tenant_id}/assets/{asset_id}/context"
            )
            
            # Map response to AssetContext
            context: AssetContext = {
                "current_state": response.get("digital_twin", {}).get("state", {}),
                "last_updated": response.get("digital_twin", {}).get("updated_at"),
                "max_operating_temp": response.get("specs", {}).get("max_temp"),
                "min_operating_temp": response.get("specs", {}).get("min_temp"),
                "max_pressure": response.get("specs", {}).get("max_pressure"),
                "max_vibration": response.get("specs", {}).get("max_vibration"),
                "manual_excerpts": response.get("manuals", []),
                "contract_id": response.get("contract", {}).get("id"),
                "contract_tier": response.get("contract", {}).get("tier"),
                "allowed_actions": response.get("contract", {}).get("allowed_actions", []),
                "sla_response_time_minutes": response.get("contract", {}).get("sla_minutes"),
                "under_maintenance": response.get("maintenance", {}).get("active", False),
                "maintenance_window_active": response.get("maintenance", {}).get(
                    "window_active", False
                ),
            }
            
            logger.info(
                "atlas_context_received",
                tenant_id=tenant_id,
                asset_id=asset_id,
                contract_tier=context.get("contract_tier"),
            )
            
            return context
        
        except Exception as e:
            logger.error(
                "atlas_get_context_error",
                tenant_id=tenant_id,
                asset_id=asset_id,
                error=str(e),
            )
            # Return minimal context on error
            return {
                "current_state": {},
                "under_maintenance": False,
                "maintenance_window_active": False,
            }

    async def search_manuals(
        self,
        tenant_id: str,
        asset_id: str,
        query: str,
        top_k: int = 3,
    ) -> list[str]:
        """Search technical manuals using RAG.
        
        Args:
            tenant_id: Tenant identifier
            asset_id: Asset identifier
            query: Search query (e.g., "temperatura máxima")
            top_k: Number of excerpts to return
            
        Returns:
            List of relevant manual excerpts
        """
        try:
            response = await self.post(
                f"/v1/tenants/{tenant_id}/assets/{asset_id}/manuals/search",
                data={"query": query, "top_k": top_k},
            )
            return response.get("excerpts", [])
        except Exception as e:
            logger.error("atlas_search_error", error=str(e))
            return []


def get_atlas_client() -> AtlasClient:
    """Get Atlas client singleton (via registry)."""
    from caos._registry import get, put
    client = get("atlas_client")
    if client is None:
        client = AtlasClient()
        put("atlas_client", client)
    return client
