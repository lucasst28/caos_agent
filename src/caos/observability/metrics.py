"""Cloud Monitoring Metrics for CAOS.

Exposes Prometheus-compatible metrics for:
- Request processing latency
- Verdict distribution
- Guardrail violations
- Oracle bypass rate
"""

import time
import structlog
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import APIRouter
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/metrics", tags=["metrics"])


@dataclass
class MetricsCollector:
    """In-memory metrics collector.
    
    NOTE: This is a development-only implementation. For production,
    integrate with OpenTelemetry SDK or Prometheus client_python to
    export metrics to Cloud Monitoring / Grafana. The in-memory state
    is lost on each container restart.
    
    TODO (E7): Replace with opentelemetry-sdk counters/histograms.
    """
    
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    verdict_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    guardrail_violations: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    oracle_calls: int = 0
    oracle_bypasses: int = 0

    def record_request(self, latency_ms: float, decision: str) -> None:
        """Record a processed request."""
        self.request_count += 1
        self.total_latency_ms += latency_ms
        self.verdict_distribution[decision] += 1

    def record_error(self) -> None:
        """Record an error."""
        self.error_count += 1

    def record_guardrail(self, rule_id: str) -> None:
        """Record a guardrail violation."""
        self.guardrail_violations[rule_id] += 1

    def record_oracle(self, bypassed: bool) -> None:
        """Record Oracle call or bypass."""
        if bypassed:
            self.oracle_bypasses += 1
        else:
            self.oracle_calls += 1

    @property
    def avg_latency_ms(self) -> float:
        """Average request latency."""
        if self.request_count == 0:
            return 0.0
        return self.total_latency_ms / self.request_count

    @property
    def oracle_bypass_rate(self) -> float:
        """Oracle bypass rate."""
        total = self.oracle_calls + self.oracle_bypasses
        if total == 0:
            return 0.0
        return self.oracle_bypasses / total

    def to_dict(self) -> dict:
        """Export metrics as dict."""
        return {
            "requests": {
                "total": self.request_count,
                "errors": self.error_count,
                "avg_latency_ms": round(self.avg_latency_ms, 2),
            },
            "verdicts": dict(self.verdict_distribution),
            "guardrails": {
                "total_violations": sum(self.guardrail_violations.values()),
                "by_rule": dict(self.guardrail_violations),
            },
            "oracle": {
                "calls": self.oracle_calls,
                "bypasses": self.oracle_bypasses,
                "bypass_rate": round(self.oracle_bypass_rate, 4),
            },
        }


def get_metrics() -> MetricsCollector:
    """Get the metrics collector singleton (via registry)."""
    from caos._registry import get, put
    collector = get("metrics_collector")
    if collector is None:
        collector = MetricsCollector()
        put("metrics_collector", collector)
    return collector


# === API Endpoint ===


@router.get(
    "/",
    summary="Get CAOS metrics",
    description="Current metrics for monitoring dashboards.",
)
async def metrics_endpoint() -> dict:
    """Return current metrics."""
    return get_metrics().to_dict()
