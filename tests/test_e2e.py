"""End-to-end test: CAOS + mock_agents via real HTTP.

Starts both FastAPI apps (mock simulators + CAOS), sends a trigger
via POST /v1/events/trigger, and validates the full pipeline response.

Run:
    pytest tests/test_e2e.py -v -s
"""

import pytest
import httpx
import asyncio
import uvicorn
import threading
import time


def _run_uvicorn(app_import: str, port: int) -> None:
    """Run a uvicorn server in a background thread."""
    uvicorn.run(app_import, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="module")
def servers():
    """Start mock_agents on 9000 and CAOS on 8080 in background threads."""
    sim_thread = threading.Thread(
        target=_run_uvicorn,
        args=("simulators.mock_agents:app", 9000),
        daemon=True,
    )
    caos_thread = threading.Thread(
        target=_run_uvicorn,
        args=("main:app", 8080),
        daemon=True,
    )

    sim_thread.start()
    caos_thread.start()

    # Wait for both servers to be ready
    for port in (9000, 8080):
        for _ in range(30):
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
                break
            except Exception:
                time.sleep(0.3)
        else:
            pytest.fail(f"Server on port {port} did not start in time")

    yield

    # Daemon threads auto-terminate when the process exits.


class TestE2EFullPipeline:
    """End-to-end: trigger → brain → verdict → action."""

    @pytest.mark.asyncio
    async def test_medium_trigger_returns_verdict(self, servers):
        """POST a MEDIUM trigger and verify the full response."""
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8080") as client:
            resp = await client.post(
                "/v1/events/trigger",
                json={
                    "tenant_id": "tenant_e2e",
                    "asset_id": "CHILLER-04",
                    "severity": "MEDIUM",
                    "metric": "temperature",
                    "value": 82.0,
                },
                timeout=15.0,
            )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Core fields must be present
        assert "event_id" in data
        assert data["verdict_score"] is not None
        assert data["decision"] in ("BLOCKED", "ALERT", "SUGGEST", "EXECUTE")
        assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "VETO")
        assert data["action_id"] is not None
        assert isinstance(data["reasoning"], list)
        assert data["processing_time_ms"] > 0
