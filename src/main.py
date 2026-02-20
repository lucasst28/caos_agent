"""CAOS Agent - Main entry point.

This is the FastAPI application that serves as the API layer for CAOS.
The cognitive processing is handled by LangGraph via Pub/Sub triggers.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from caos import __version__
from caos.config import get_settings

# Set stdlib logging level to INFO so structlog captures info+ logs
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Configure structured logging
from caos.observability.logs import LogCapturingProcessor

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        LogCapturingProcessor(),  # Capture logs for API
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

    # Start HITL background timeout timer
    from caos.api.feedback import start_hitl_timer, stop_hitl_timer
    start_hitl_timer(interval_seconds=30.0)
    logger.info("hitl_timer_enabled")

    yield

    # --- Shutdown ---
    stop_hitl_timer()
    logger.info("hitl_timer_stopped")

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
from caos.api.dashboard import router as dashboard_router
from caos.observability.metrics import router as metrics_router
from caos.observability.logs import router as logs_router
from caos.api.reasoning import router as reasoning_router
from caos.api.audit import router as audit_router
from caos.api.rlhf_api import router as rlhf_router

app.include_router(events_router, prefix="/v1")
app.include_router(traces_router, prefix="/v1")
app.include_router(feedback_router, prefix="/v1")
app.include_router(guardrails_router, prefix="/v1")
app.include_router(metrics_router, prefix="/v1")
app.include_router(logs_router, prefix="/v1")
app.include_router(reasoning_router, prefix="/v1")
app.include_router(audit_router, prefix="/v1")
app.include_router(rlhf_router, prefix="/v1")
app.include_router(dashboard_router)  # Root level: /stats, /clients, /circuit-breakers, /rules, /streams


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
        "dashboard": "/dashboard",
    }


# === Serve Frontend Dashboard ===
# Mount static assets first
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=str(frontend_dir / "assets")),
        name="dashboard-assets",
    )
    
    @app.get("/dashboard")
    async def dashboard():
        """Serve the CAOS dashboard."""
        return FileResponse(str(frontend_dir / "index.html"))
    
    @app.get("/dashboard/reasoning")
    async def reasoning_page():
        """Serve the CAOS reasoning page."""
        return FileResponse(str(frontend_dir / "reasoning.html"))
    
    logger.info("frontend_dashboard_enabled", path=str(frontend_dir))
else:
    logger.warning("frontend_not_found", expected_path=str(frontend_dir))
