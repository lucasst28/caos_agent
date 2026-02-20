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
                f"v1/tenants/{tenant_id}/assets/{asset_id}/context"
            )
            
            # Map response to AssetContext
            # Support both nested (production Atlas) and flat (simulator) response formats
            if "digital_twin" in response:
                # Production Atlas format
                current_state = response.get("digital_twin", {}).get("state", {})
                max_temp = response.get("specs", {}).get("max_temp")
                min_temp = response.get("specs", {}).get("min_temp")
                max_pressure = response.get("specs", {}).get("max_pressure")
                max_vib = response.get("specs", {}).get("max_vibration")
                manuals = response.get("manuals", [])
                contract_id = response.get("contract", {}).get("id")
                contract_tier = response.get("contract", {}).get("tier")
                allowed_actions = response.get("contract", {}).get("allowed_actions", [])
                sla_minutes = response.get("contract", {}).get("sla_minutes")
                under_maint = response.get("maintenance", {}).get("active", False)
                maint_window = response.get("maintenance", {}).get("window_active", False)
            else:
                # Simulator flat format
                current_state = response.get("current_state", {})
                max_temp = response.get("max_operating_temp")
                min_temp = response.get("min_operating_temp")
                max_pressure = response.get("max_pressure")
                max_vib = response.get("max_vibration")
                manuals = response.get("manual_excerpts", [])
                contract_id = response.get("contract_id")
                contract_tier = response.get("contract_tier")
                allowed_actions = response.get("allowed_actions", [])
                sla_minutes = response.get("sla_response_time_minutes")
                under_maint = response.get("under_maintenance", False)
                maint_window = response.get("maintenance_window_active", False)

            context: AssetContext = {
                "current_state": current_state,
                "last_updated": response.get("last_updated"),
                "max_operating_temp": max_temp,
                "min_operating_temp": min_temp,
                "max_pressure": max_pressure,
                "max_vibration": max_vib,
                "manual_excerpts": manuals,
                "contract_id": contract_id,
                "contract_tier": contract_tier,
                "allowed_actions": allowed_actions,
                "sla_response_time_minutes": sla_minutes,
                "under_maintenance": under_maint,
                "maintenance_window_active": maint_window,
                "location": response.get("location"),
            }

            # Include real_data from simulator if available
            if "real_data" in response:
                context["real_data"] = response["real_data"]

            # Include simulation_data (for scenarios like Freezer)
            if "simulation_data" in response:
                context["simulation_data"] = response["simulation_data"]
            
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
