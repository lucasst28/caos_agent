"""Sense Node - Data Fusion (Perception).

Responsible for consolidating alerts from Sentinel with context from Atlas.
This is the first cognitive step: understanding the current reality.
"""

import structlog
from datetime import datetime, timezone
from typing import Any

from caos.integration.atlas import get_atlas_client
from caos.schemas.state import AssetContext, JudgeState

logger = structlog.get_logger(__name__)


async def sense_node(state: JudgeState) -> dict[str, Any]:
    """Sense Node: Data Fusion.
    
    Combines:
    - Sentinel alert data (from trigger)
    - Atlas context (Digital Twin, manuals, contracts)
    
    This node:
    1. Extracts relevant info from the trigger
    2. Calls Atlas API to get asset context
    3. Merges data into unified perception
    
    Returns:
        Updated state with atlas_context populated
    """
    trigger = state["trigger"]
    
    logger.info(
        "sense_node_start",
        event_id=trigger.event_id,
        asset_id=trigger.context.asset_id,
    )
    
    # Call Atlas to get context
    atlas_client = get_atlas_client()
    
    try:
        atlas_context = await atlas_client.get_asset_context(
            tenant_id=trigger.context.tenant_id,
            asset_id=trigger.context.asset_id,
        )
    except Exception as e:
        logger.warning(
            "sense_node_atlas_fallback",
            event_id=trigger.event_id,
            error=str(e),
        )
        # Fallback to minimal context
        atlas_context: AssetContext = {
            "current_state": {"sensor_value": trigger.value},
            "under_maintenance": False,
            "maintenance_window_active": False,
            "contract_tier": "PREMIUM",
            "allowed_actions": ["shutdown", "notification", "ticket"],
        }
    
    logger.info(
        "sense_node_complete",
        event_id=trigger.event_id,
        contract_tier=atlas_context.get("contract_tier"),
    )
    
    return {
        "atlas_context": atlas_context,
        "processing_started_at": datetime.now(timezone.utc).isoformat(),
    }
