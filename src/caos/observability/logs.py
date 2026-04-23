"""Log collection and API for CAOS.

Captures structured logs in memory and exposes them via REST API
for the dashboard frontend.
"""

import structlog
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])


class LogEntry(BaseModel):
    """A single log entry."""
    
    timestamp: str
    level: str
    logger_name: str
    event: str
    message: str
    context: dict[str, Any] = {}


class LogsResponse(BaseModel):
    """Response with log entries."""
    
    logs: list[LogEntry]
    total: int


class LogCollector:
    """In-memory log collector with thread-safe circular buffer.
    
    This collector captures structured logs from structlog and stores them
    in a deque with a maximum size. It's thread-safe and can be accessed
    from multiple FastAPI workers.
    
    NOTE: This is a development/demo implementation. For production,
    use a proper log aggregation system like Cloud Logging, ELK, or Loki.
    """
    
    def __init__(self, max_size: int = 1000):
        """Initialize the log collector.
        
        Args:
            max_size: Maximum number of logs to keep in memory
        """
        self.logs: deque[dict[str, Any]] = deque(maxlen=max_size)
        self.lock = Lock()
    
    def add(self, log_entry: dict[str, Any]) -> None:
        """Add a log entry to the collection.
        
        Args:
            log_entry: Structured log entry from structlog
        """
        with self.lock:
            # Add timestamp if not present
            if "timestamp" not in log_entry:
                log_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            self.logs.append(log_entry)
    
    def get_logs(
        self,
        level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get logs with optional filtering.
        
        Args:
            level: Filter by log level (info, warning, error, debug)
            limit: Maximum number of logs to return
            offset: Offset for pagination
            
        Returns:
            Tuple of (filtered logs, total count)
        """
        with self.lock:
            logs_list = list(self.logs)
        
        # Filter by level if specified
        if level:
            logs_list = [
                log for log in logs_list
                if log.get("level", "").lower() == level.lower()
            ]
        
        total = len(logs_list)
        
        # Apply pagination
        # Note: deque is FIFO, so newest logs are at the end
        # Reverse to show newest first
        logs_list.reverse()
        paginated = logs_list[offset:offset + limit]
        
        return paginated, total


# Global collector instance
_log_collector: LogCollector | None = None


def get_log_collector() -> LogCollector:
    """Get or create the global log collector instance."""
    global _log_collector
    if _log_collector is None:
        _log_collector = LogCollector(max_size=1000)
    return _log_collector


class LogCapturingProcessor:
    """Structlog processor that captures logs for the API.
    
    This processor should be added to structlog's processor chain
    to capture logs in memory for the dashboard.
    """
    
    def __call__(self, logger, method_name: str, event_dict: dict) -> dict:
        """Process a log entry and capture it.
        
        Args:
            logger: The logger instance
            method_name: The logging method name (info, warning, error, etc.)
            event_dict: The event dictionary
            
        Returns:
            The unchanged event_dict
        """
        # Capture the log
        collector = get_log_collector()
        
        # Create a copy to avoid mutations
        log_entry = dict(event_dict)
        log_entry["level"] = method_name
        
        collector.add(log_entry)
        
        # Return unchanged dict for next processor
        return event_dict


# === API Endpoints ===


@router.get(
    "/",
    response_model=LogsResponse,
    summary="Get system logs",
    description="Retrieve structured logs from CAOS for monitoring and debugging.",
)
async def get_logs(
    level: str | None = Query(
        default=None,
        description="Filter by log level (info, warning, error, debug)",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of logs to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Offset for pagination",
    ),
) -> LogsResponse:
    """Get system logs with optional filtering."""
    collector = get_log_collector()
    logs_list, total = collector.get_logs(level=level, limit=limit, offset=offset)
    
    # Convert to LogEntry models
    log_entries = []
    for log in logs_list:
        # Extract common fields
        timestamp = log.get("timestamp", "")
        level_str = log.get("level", "info")
        logger_name = log.get("logger", "unknown")
        event = log.get("event", "")
        
        # Build message from event and other fields
        message_parts = [event]
        
        # Create context dict with remaining fields
        context = {
            k: v for k, v in log.items()
            if k not in ("timestamp", "level", "logger", "event", "message")
        }
        
        # If there's a message field, use it
        if "message" in log:
            message_parts = [log["message"]]
        elif context:
            # Add some context to the message
            for key, value in list(context.items())[:3]:  # First 3 items
                message_parts.append(f"{key}={value}")
        
        message = " | ".join(filter(None, message_parts))
        
        log_entries.append(
            LogEntry(
                timestamp=timestamp,
                level=level_str,
                logger_name=logger_name,
                event=event,
                message=message,
                context=context,
            )
        )
    
    return LogsResponse(logs=log_entries, total=total)


@router.delete(
    "/",
    summary="Clear logs",
    description="Clear all logs from memory (useful for testing).",
)
async def clear_logs() -> dict[str, str]:
    """Clear all logs from memory."""
    collector = get_log_collector()
    with collector.lock:
        collector.logs.clear()
    
    logger.info("logs_cleared")
    
    return {"status": "ok", "message": "Logs cleared"}


@router.post(
    "/external",
    summary="Receive external logs",
    description="Endpoint for external services (like simulators) to send logs to CAOS.",
    status_code=201,
)
async def receive_external_log(log_data: dict[str, Any]) -> dict[str, str]:
    """Receive and store a log from an external service.
    
    This endpoint allows external services (simulators, agents, etc) to send
    their logs to CAOS for centralized monitoring.
    
    Args:
        log_data: Structured log data from external service
        
    Returns:
        Success status
    """
    collector = get_log_collector()
    
    # Add timestamp if not present
    if "timestamp" not in log_data:
        log_data["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    # Mark as external source
    if "source" not in log_data:
        log_data["source"] = "external"
    
    collector.add(log_data)
    
    return {"status": "ok", "message": "Log received"}
