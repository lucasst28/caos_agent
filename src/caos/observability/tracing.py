"""LangSmith Tracing - Observability for the CAOS cognitive pipeline.

Provides integration with LangSmith for:
- Full chain-of-thought tracing
- Step-by-step latency measurement
- Decision auditing and WORM logging
"""

import os
import structlog
from typing import Any

from caos.config import get_settings

logger = structlog.get_logger(__name__)


def configure_langsmith() -> None:
    """Configure LangSmith tracing from settings.
    
    Sets the environment variables that LangChain/LangGraph
    use to connect to LangSmith.
    """
    settings = get_settings()

    if not settings.langchain_api_key:
        logger.warning("langsmith_not_configured", detail="No API key set")
        return

    os.environ["LANGCHAIN_TRACING_V2"] = str(settings.langchain_tracing_v2).lower()
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

    logger.info(
        "langsmith_configured",
        project=settings.langchain_project,
    )


def get_trace_metadata(
    event_id: str,
    tenant_id: str,
    asset_id: str,
    severity: str,
) -> dict[str, Any]:
    """Build metadata dict to attach to LangGraph run."""
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "severity": severity,
        "agent": "caos_core",
    }

