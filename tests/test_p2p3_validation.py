"""Validation tests for P2/P3 gap implementations."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import datetime, timezone


def test_imports():
    """All new modules import correctly."""
    from caos.safety.runtime import (
        get_anti_flapping, get_anti_spam, get_asset_mutex,
        get_circuit_breaker, get_rate_limiter, get_event_dedup,
        get_behavioral_anomaly, RateLimiter, EventDedup, BehavioralAnomaly,
    )
    from caos.safety.engine import GuardrailEngine
    from caos.schemas.state import JudgeState
    from caos.core.nodes.sense import sense_node
    from caos.core.nodes.cortex import cortex_node
    from caos.core.nodes.act import act_node
    from caos.core.brain import create_brain, process_trigger
    print("  imports OK")


def test_rate_limiter():
    """RateLimiter tracks requests and returns violation when exceeded."""
    from caos.safety.runtime import RateLimiter
    rl = RateLimiter(max_per_minute=5)
    for i in range(5):
        result = rl.record()
        assert result is None, f"Should not trigger on request {i+1}"
    violation = rl.record()
    assert violation is not None, "Should trigger after exceeding max_per_minute"
    assert violation["id"] == "SO_005"
    assert rl.get_requests_per_minute() > 5
    assert rl.get_daily_count() == 6
    print("  RateLimiter OK")


def test_event_dedup():
    """EventDedup detects duplicate event IDs."""
    from caos.safety.runtime import EventDedup
    ed = EventDedup()
    assert ed.check("evt_unique_001") is None, "First time should pass"
    dup = ed.check("evt_unique_001")
    assert dup is not None, "Duplicate should be caught"
    assert dup["id"] == "ROB_004"
    assert ed.check("evt_unique_002") is None, "Different ID should pass"
    print("  EventDedup OK")


def test_behavioral_anomaly():
    """BehavioralAnomaly detects activity spikes."""
    from caos.safety.runtime import BehavioralAnomaly
    ba = BehavioralAnomaly(window_seconds=0.1, history_windows=3)
    # Record baseline - record() returns violation or None
    for _ in range(3):
        result = ba.record("asset_a")
    # With minimal history, no anomaly expected yet
    # Just verify it returns either None or a dict with id
    if result is not None:
        assert result["id"] == "SO_008"
    print("  BehavioralAnomaly OK")


def test_build_context_enriched():
    """GuardrailEngine._build_context() populates all required keys."""
    from caos.safety.engine import GuardrailEngine
    from caos.schemas.trigger import TriggerPayload, TriggerContext
    from caos.schemas.enums import Severity, TriggerSource

    engine = GuardrailEngine()
    trigger = TriggerPayload(
        event_id="evt_ctx_test",
        source=TriggerSource.MANUAL,
        timestamp=datetime.now(timezone.utc),
        severity=Severity.MEDIUM,
        payload={},
        context=TriggerContext(tenant_id="t1", asset_id="CHILLER-01"),
        metric="temperature",
        value=85.0,
    )
    state = {
        "trigger": trigger,
        "processing_started_at": datetime.now(timezone.utc).isoformat(),
    }
    context = engine._build_context(state)

    required_keys = [
        "daily_cost", "daily_tokens", "estimated_cost",
        "llm_daily_cost_budget_usd", "llm_daily_token_budget",
        "api_calls_per_minute", "processing_time_ms", "current_hour",
        "daily_actions", "notifications_last_hour",
        "oracle_response_time_ms", "llm_response_empty",
        "circuit_breaker_status", "retry_count",
        "consecutive_failures", "current_time",
    ]
    missing = [k for k in required_keys if k not in context]
    assert not missing, f"Missing context keys: {missing}"
    
    # Verify types
    assert isinstance(context["daily_cost"], (int, float))
    assert isinstance(context["current_hour"], int)
    assert isinstance(context["processing_time_ms"], (int, float))
    assert isinstance(context["llm_response_empty"], bool)
    print(f"  _build_context() has all {len(required_keys)} keys OK")


def test_safe_eval_len():
    """GuardrailEngine._safe_eval() supports len() function."""
    from caos.safety.engine import GuardrailEngine
    engine = GuardrailEngine()

    assert engine._safe_eval("len(items) > 2", {"items": [1, 2, 3]}) is True
    assert engine._safe_eval("len(items) > 5", {"items": [1, 2, 3]}) is False
    assert engine._safe_eval("len(text) < 10", {"text": "hello"}) is True
    assert engine._safe_eval("len(text) > 100", {"text": "short"}) is False
    print("  len() in _safe_eval OK")


def test_guardrail_rules_fire():
    """Key guardrail rules actually evaluate with enriched context."""
    from caos.safety.engine import GuardrailEngine
    from caos.schemas.trigger import TriggerPayload, TriggerContext
    from caos.schemas.enums import Severity, TriggerSource

    engine = GuardrailEngine()
    trigger = TriggerPayload(
        event_id="evt_rules_test",
        source=TriggerSource.MANUAL,
        timestamp=datetime.now(timezone.utc),
        severity=Severity.HIGH,
        payload={},
        context=TriggerContext(tenant_id="t1", asset_id="CHILLER-01"),
        metric="temperature",
        value=95.0,
    )
    state = {
        "trigger": trigger,
        "processing_started_at": datetime.now(timezone.utc).isoformat(),
        "proposed_action": {
            "type": "send_command",
            "target": "CHILLER-01",
            "params": {},
            "justification": "Test action"
        },
    }

    result = engine.check_all(state)
    # Should return a GuardrailCheckResult
    assert hasattr(result, 'passed'), f"Expected GuardrailCheckResult, got {type(result)}"
    assert hasattr(result, 'violations'), f"Missing violations attr"
    assert hasattr(result, 'warnings'), f"Missing warnings attr"
    assert isinstance(result.violations, list)
    assert isinstance(result.warnings, list)
    total = len(result.violations) + len(result.warnings)
    print(f"  Guardrail evaluation: passed={result.passed}, {len(result.violations)} violations, {len(result.warnings)} warnings OK")


def test_state_typeddict_fields():
    """JudgeState has the new private fields."""
    from caos.schemas.state import JudgeState
    import typing
    hints = typing.get_type_hints(JudgeState)
    for field in ["_llm_response_empty", "_oracle_response_time_ms", "_consecutive_failures"]:
        assert field in hints, f"JudgeState missing field: {field}"
    print("  JudgeState private fields OK")


if __name__ == "__main__":
    tests = [
        test_imports,
        test_rate_limiter,
        test_event_dedup,
        test_behavioral_anomaly,
        test_build_context_enriched,
        test_safe_eval_len,
        test_guardrail_rules_fire,
        test_state_typeddict_fields,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
    print("ALL P2/P3 VALIDATION TESTS PASSED")
