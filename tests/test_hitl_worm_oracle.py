"""Tests for HITL Workflow, Oracle Simulator, and WORM Storage.

Run: pytest tests/test_hitl_worm_oracle.py -v
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest


# =============================================
# WORM Storage Tests
# =============================================

class TestWORMStorage:
    """Tests for FileWORMStorage append-only hash chain."""

    @pytest.fixture(autouse=True)
    def setup_worm(self, tmp_path):
        """Create a fresh WORM storage for each test."""
        from caos.observability.worm import FileWORMStorage, GENESIS_HASH
        self.storage = FileWORMStorage(base_dir=str(tmp_path))
        self.tmp_path = tmp_path
        self.GENESIS_HASH = GENESIS_HASH

    def test_append_creates_file(self):
        """Append should create a JSONL file for today."""
        h = self.storage.append({"action_id": "act_001", "verdict_score": 0.6})
        assert h is not None
        assert len(h) == 64  # SHA-256 hex length

        files = list(self.tmp_path.glob("verdicts_*.jsonl"))
        assert len(files) == 1

        # Verify file content
        with open(files[0], "r") as f:
            line = f.readline().strip()
            record = json.loads(line)
            assert record["action_id"] == "act_001"
            assert record["_hash"] == h
            assert record["_prev_hash"] == self.GENESIS_HASH

    def test_hash_chain_integrity(self):
        """Multiple appends should form a valid hash chain."""
        hashes = []
        for i in range(5):
            h = self.storage.append({"action_id": f"act_{i:03d}", "score": i * 0.2})
            hashes.append(h)

        # Verify chain
        ok, error_pos = self.storage.verify()
        assert ok is True
        assert error_pos is None

        # Verify sequential hashing
        assert len(set(hashes)) == 5  # All unique

    def test_tamper_detection(self):
        """Modifying a record in the middle should break verification."""
        for i in range(5):
            self.storage.append({"action_id": f"act_{i:03d}", "value": i})

        # Tamper with the file — modify a byte in the middle
        files = list(self.tmp_path.glob("verdicts_*.jsonl"))
        assert len(files) == 1

        with open(files[0], "r") as f:
            lines = f.readlines()

        # Modify the 3rd record (index 2)
        record = json.loads(lines[2])
        record["value"] = 999  # Tamper!
        lines[2] = json.dumps(record) + "\n"

        with open(files[0], "w") as f:
            f.writelines(lines)

        # Verification should fail
        ok, error_pos = self.storage.verify()
        assert ok is False
        assert error_pos == 2  # Tampered record

    def test_read_all_with_filters(self):
        """read_all should filter by tenant and asset."""
        self.storage.append({"action_id": "act_001", "tenant_id": "t1", "asset_id": "A1"})
        self.storage.append({"action_id": "act_002", "tenant_id": "t1", "asset_id": "A2"})
        self.storage.append({"action_id": "act_003", "tenant_id": "t2", "asset_id": "A1"})

        records, total = self.storage.read_all(tenant_id="t1")
        assert total == 2
        assert all(r["tenant_id"] == "t1" for r in records)

        records, total = self.storage.read_all(asset_id="A1")
        assert total == 2
        assert all(r["asset_id"] == "A1" for r in records)

    def test_stats(self):
        """stats should return correct counts."""
        self.storage.append({"action_id": "act_001"})
        self.storage.append({"action_id": "act_002"})

        s = self.storage.stats()
        assert s["total_records"] == 2
        assert s["total_files"] == 1
        assert s["total_bytes"] > 0

    def test_empty_verify_succeeds(self):
        """Verifying empty storage should succeed."""
        ok, error_pos = self.storage.verify()
        assert ok is True
        assert error_pos is None


# =============================================
# HITL Workflow Tests
# =============================================

class TestHITLWorkflow:
    """Tests for HITL state machine, timeout, and approval flow."""

    @pytest.fixture(autouse=True)
    def setup_hitl(self):
        """Clear HITL state before each test."""
        from caos.api.feedback import (
            _pending_store, _feedback_log, _store_lock,
            submit_for_approval, resolve_pending, check_timeouts,
            get_pending_actions, get_resolved_actions,
            HITL_TIMEOUT_SECONDS, ApprovalState,
        )
        with _store_lock:
            _pending_store.clear()
            _feedback_log.clear()

        self.submit = submit_for_approval
        self.resolve = resolve_pending
        self.check_timeouts = check_timeouts
        self.get_pending = get_pending_actions
        self.get_resolved = get_resolved_actions
        self.ApprovalState = ApprovalState
        self._store = _pending_store
        self._lock = _store_lock

    def test_submit_creates_pending(self):
        """Submitting creates a PENDING action."""
        result = self.submit({
            "action_id": "act_test1",
            "event_id": "evt_1",
            "asset_id": "CHILLER-04",
            "tenant_id": "t1",
            "verdict_score": 0.5,
        })
        assert result.state == self.ApprovalState.PENDING
        assert result.action_id == "act_test1"

        pending = self.get_pending()
        assert len(pending) == 1

    def test_approve_resolves_pending(self):
        """Approving a pending action sets it to APPROVED."""
        self.submit({"action_id": "act_test2", "event_id": "evt_2", "asset_id": "A1", "tenant_id": "t1", "verdict_score": 0.3})

        result = self.resolve("act_test2", approved=True, resolver="operator_1")
        assert result is not None
        assert result.state == self.ApprovalState.APPROVED
        assert result.resolved_by == "operator_1"

        # No more pending
        assert len(self.get_pending()) == 0

    def test_reject_resolves_pending(self):
        """Rejecting sets state to REJECTED."""
        self.submit({"action_id": "act_test3", "event_id": "evt_3", "asset_id": "A1", "tenant_id": "t1", "verdict_score": 0.8})

        result = self.resolve("act_test3", approved=False, resolver="supervisor", notes="Too risky")
        assert result is not None
        assert result.state == self.ApprovalState.REJECTED
        assert result.notes == "Too risky"

    def test_timeout_auto_approves(self):
        """Actions past the timeout are auto-approved."""
        import caos.api.feedback as fb
        original_timeout = fb.HITL_TIMEOUT_SECONDS
        fb.HITL_TIMEOUT_SECONDS = 1  # 1 second timeout for test

        try:
            self.submit({"action_id": "act_timeout", "event_id": "evt_t", "asset_id": "A1", "tenant_id": "t1", "verdict_score": 0.5})

            # Manually set created_at to 2 seconds ago
            with self._lock:
                action = self._store["act_timeout"]
                past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
                action.created_at = past

            auto = self.check_timeouts()
            assert "act_timeout" in auto

            with self._lock:
                action = self._store["act_timeout"]
            assert action.state == self.ApprovalState.AUTO_APPROVED
        finally:
            fb.HITL_TIMEOUT_SECONDS = original_timeout

    def test_resolve_nonexistent_returns_none(self):
        """Resolving a non-existent action returns None."""
        result = self.resolve("act_nonexistent", approved=True, resolver="x")
        assert result is None

    def test_double_resolve_returns_none(self):
        """Resolving an already resolved action returns None."""
        self.submit({"action_id": "act_double", "event_id": "e", "asset_id": "A", "tenant_id": "t", "verdict_score": 0.1})
        self.resolve("act_double", approved=True, resolver="op1")
        result = self.resolve("act_double", approved=False, resolver="op2")
        assert result is None

    def test_history_includes_resolved(self):
        """get_resolved_actions should include approved/rejected actions."""
        self.submit({"action_id": "a1", "event_id": "e1", "asset_id": "A", "tenant_id": "t", "verdict_score": 0.1})
        self.submit({"action_id": "a2", "event_id": "e2", "asset_id": "A", "tenant_id": "t", "verdict_score": 0.2})
        self.submit({"action_id": "a3", "event_id": "e3", "asset_id": "A", "tenant_id": "t", "verdict_score": 0.3})

        self.resolve("a1", approved=True, resolver="op")
        self.resolve("a2", approved=False, resolver="op")
        # a3 stays pending

        resolved, total = self.get_resolved()
        assert total == 2
        assert len(resolved) == 2

        # Filter by state
        approved, _ = self.get_resolved(state_filter="APPROVED")
        assert len(approved) == 1


# =============================================
# Oracle Simulator Tests
# =============================================

class TestOracleSimulator:
    """Tests for deterministic Oracle scenarios."""

    def test_scenarios_dict_exists(self):
        """ORACLE_SCENARIOS should have 6 scenarios."""
        # Import the mock_agents module scenarios directly
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "mock_agents",
            os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # We can't fully import (it starts FastAPI), so just check the constant
        # Instead, we'll just verify the file contains the scenarios
        import ast
        mock_path = os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py")
        with open(mock_path, "r") as f:
            content = f.read()

        scenarios = [
            "imminent_failure",
            "stable_operation",
            "gradual_degradation",
            "gps_theft",
            "financial_high_impact",
            "maintenance_needed",
        ]
        for s in scenarios:
            assert f'"{s}"' in content, f"Scenario {s} not found in mock_agents.py"

    def test_imminent_failure_scenario_values(self):
        """imminent_failure scenario should have deterministic high-risk values."""
        mock_path = os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py")
        with open(mock_path, "r") as f:
            content = f.read()

        # Verify deterministic values are present
        assert '"failure_probability": 0.92' in content
        assert '"risk_level": "CRITICAL"' in content

    def test_stable_operation_scenario_values(self):
        """stable_operation scenario should have low-risk deterministic values."""
        mock_path = os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py")
        with open(mock_path, "r") as f:
            content = f.read()

        assert '"failure_probability": 0.08' in content
        assert '"risk_level": "LOW"' in content

    @pytest.mark.asyncio
    async def test_oracle_scenarios_endpoint(self):
        """The /oracle/v1/scenarios endpoint should list all scenarios."""
        from httpx import AsyncClient, ASGITransport
        # We need to import the FastAPI app from mock_agents
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mock_agents_test",
            os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        transport = ASGITransport(app=mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/oracle/v1/scenarios")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 9
            names = [s["name"] for s in data["scenarios"]]
            assert "imminent_failure" in names
            assert "stable_operation" in names

    @pytest.mark.asyncio
    async def test_oracle_scenario_trigger(self):
        """POST /oracle/v1/scenario/imminent_failure should return deterministic prediction."""
        from httpx import AsyncClient, ASGITransport
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mock_agents_test2",
            os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        transport = ASGITransport(app=mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/oracle/v1/scenario/imminent_failure?asset_id=CHILLER-04")
            assert resp.status_code == 200
            data = resp.json()
            assert data["failure_probability"] == 0.92
            assert data["risk_level"] == "CRITICAL"
            assert data["deterministic"] is True
            assert data["scenario"] == "imminent_failure"

    @pytest.mark.asyncio
    async def test_oracle_scenario_not_found(self):
        """POST /oracle/v1/scenario/invalid should return error."""
        from httpx import AsyncClient, ASGITransport
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mock_agents_test3",
            os.path.join(os.path.dirname(__file__), "..", "simulators", "mock_agents.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        transport = ASGITransport(app=mod.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/oracle/v1/scenario/invalid_scenario")
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data
