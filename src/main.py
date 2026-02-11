"""CAOS Agent - Main entry point.

This is the FastAPI application that serves as the API layer for CAOS.
The cognitive processing is handled by LangGraph via Pub/Sub triggers.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from caos import __version__
from caos.config import get_settings

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown logic."""
    # --- Startup ---
    settings = get_settings()

    from caos.observability.tracing import configure_langsmith
    configure_langsmith()

    logger.info(
        "caos_agent_starting",
        version=__version__,
        project=settings.google_cloud_project,
        model=settings.vertex_ai_model,
    )

    # Start Pub/Sub consumer in a background thread (if enabled)
    _pubsub_consumer = None
    if settings.pubsub_enabled:
        import asyncio
        from caos.core.brain import process_trigger
        from caos.integration.pubsub import create_sentinel_consumer

        async def _handle_trigger(trigger):
            """Route Pub/Sub message into the brain pipeline."""
            await process_trigger(trigger.model_dump(mode="json"))

        _pubsub_consumer = create_sentinel_consumer(handler=_handle_trigger)
        asyncio.get_event_loop().run_in_executor(None, _pubsub_consumer.start)
        logger.info("pubsub_consumer_enabled")
    else:
        logger.info("pubsub_consumer_disabled", hint="Set PUBSUB_ENABLED=true to enable")

    yield

    # --- Shutdown ---
    if _pubsub_consumer is not None:
        _pubsub_consumer.stop()
        logger.info("pubsub_consumer_stopped")

    logger.info("caos_agent_shutting_down")


# Create FastAPI app
app = FastAPI(
    title="CAOS Agent",
    description="Centralized Autonomous Operating System - Judge-LM for VivAIOT",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS — restrict origins per environment
settings = get_settings()
_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:8080",
]
# In production, add the real frontend origin from environment/settings
# e.g.: _allowed_origins.append(settings.frontend_url) when available

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# --- Include API Routers ---
from caos.api.events import router as events_router
from caos.api.traces import router as traces_router
from caos.api.feedback import router as feedback_router
from caos.api.guardrails import router as guardrails_router
from caos.observability.metrics import router as metrics_router

app.include_router(events_router, prefix="/v1")
app.include_router(traces_router, prefix="/v1")
app.include_router(feedback_router, prefix="/v1")
app.include_router(guardrails_router, prefix="/v1")
app.include_router(metrics_router, prefix="/v1")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy", "version": __version__}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": "CAOS Agent",
        "version": __version__,
        "description": "Centralized Autonomous Operating System",
        "docs": "/docs",
    }
