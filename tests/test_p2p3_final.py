"""Validation tests for FINAL P2/P3 gap implementations (all 4 remaining items)."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_all_imports():
    """All new modules import correctly."""
    from caos.safety.runtime import (
        FailSafe, BackpressureGuard, NotificationRouter,
        get_fail_safe, get_backpressure_guard, get_notification_router,
    )
    from caos.safety.engine import GuardrailEngine
    from caos.core.nodes.sense import sense_node
    from caos.core.nodes.act import act_node
    from caos.core.nodes.oracle import oracle_node
    from caos.core.brain import create_brain, process_trigger
    print("  imports OK")


def test_failsafe():
    """SO_003/SO_004: FailSafe monitors service health and triggers degraded mode."""
    from caos.safety.runtime import FailSafe

    fs = FailSafe()
    # Should not be degraded initially
    assert not fs.is_degraded()
    assert fs.get_degraded_action("CRITICAL") is None

    # Mark atlas as down
    fs.report_service_down("atlas", "connection timeout")
    assert fs.is_degraded()

    # CRITICAL → hardcoded shutdown
    act = fs.get_degraded_action("CRITICAL")
    assert act is not None
    assert act["id"] == "SO_003"
    assert act["fallback_action"] == "hardcoded_shutdown"

    # HIGH → forward to copilot
    act_h = fs.get_degraded_action("HIGH")
    assert act_h["id"] == "SO_003"
    assert act_h["fallback_action"] == "forward_to_copilot"

    # LOW → log and ignore (SO_004)
    act_l = fs.get_degraded_action("LOW")
    assert act_l["id"] == "SO_004"
    assert act_l["fallback_action"] == "log_and_ignore"

    # Recovery
    fs.report_service_up("atlas")
    assert not fs.is_degraded()
    assert fs.get_degraded_action("CRITICAL") is None

    # Status check
    status = fs.get_status()
    assert "degraded" in status
    assert "services" in status
    print("  FailSafe OK")


def test_backpressure():
    """ROB_005: BackpressureGuard tracks in-flight events."""
    from caos.safety.runtime import BackpressureGuard

    bp = BackpressureGuard(max_inflight=3)
    assert bp.get_depth() == 0

    # Acquire 3 slots successfully
    for i in range(3):
        result = bp.try_acquire()
        assert result is None, f"Should accept request {i+1}"
    assert bp.get_depth() == 3

    # 4th should be rejected
    violation = bp.try_acquire()
    assert violation is not None
    assert violation["id"] == "ROB_005"
    assert bp.get_depth() == 3  # Still 3, not 4

    # Release one slot
    bp.release()
    assert bp.get_depth() == 2

    # Now should accept again
    assert bp.try_acquire() is None
    assert bp.get_depth() == 3
    print("  BackpressureGuard OK")


def test_notification_router():
    """COMM: NotificationRouter routes by severity per doc §8.3."""
    from caos.safety.runtime import NotificationRouter

    nr = NotificationRouter()

    # CRITICAL → WhatsApp + SMS + Dashboard
    dispatches = nr.route("CRITICAL", "CHILLER-01", "Compressor failure!", "t1", "act_001")
    channels = [d["channel"] for d in dispatches]
    assert "whatsapp" in channels
    assert "sms" in channels
    assert "dashboard" in channels
    assert len(dispatches) == 3

    # HIGH → WhatsApp + Dashboard
    dispatches_h = nr.route("HIGH", "CHILLER-02", "High temp", "t1", "act_002")
    channels_h = [d["channel"] for d in dispatches_h]
    assert "whatsapp" in channels_h
    assert "dashboard" in channels_h
    assert len(dispatches_h) == 2

    # MEDIUM → Email + Dashboard
    dispatches_m = nr.route("MEDIUM", "CHILLER-03", "Temp warning", "t1", "act_003")
    channels_m = [d["channel"] for d in dispatches_m]
    assert "email" in channels_m
    assert "dashboard" in channels_m

    # LOW → Dashboard only (with auto-approve)
    dispatches_l = nr.route("LOW", "CHILLER-04", "Info msg")
    assert len(dispatches_l) == 1
    assert dispatches_l[0]["channel"] == "dashboard"
    assert dispatches_l[0]["auto_approve_on_timeout"] is True

    # Check timeout values
    crit_whatsapp = [d for d in dispatches if d["channel"] == "whatsapp"][0]
    assert crit_whatsapp["timeout_minutes"] == 30
    crit_sms = [d for d in dispatches if d["channel"] == "sms"][0]
    assert crit_sms["timeout_minutes"] == 15

    # Verify recent dispatches retrieval
    recent = nr.get_recent_dispatches(limit=10)
    assert len(recent) > 0
    print("  NotificationRouter OK")


def test_engine_loads_category_files():
    """Engine loads from per-category JSON files in policies directory."""
    from caos.safety.engine import GuardrailEngine
    engine = GuardrailEngine()
    assert len(engine.rules) == 41, f"Expected 41 rules, got {len(engine.rules)}"
    
    # Verify categories loaded
    cats = set(r.category for r in engine.rules)
    expected_cats = {"PHYSICAL", "FINANCIAL", "CONTRACTUAL", "COMMUNICATION", "SECOPS", "ROBUSTNESS"}
    assert cats == expected_cats, f"Categories mismatch: {cats} vs {expected_cats}"
    print(f"  Engine loaded {len(engine.rules)} rules from {len(cats)} category files OK")


def test_engine_fallback_to_monolithic():
    """Engine falls back to guardrails.json if no category files exist."""
    from caos.safety.engine import GuardrailEngine
    from pathlib import Path
    
    # Load from monolithic file directly
    mono_path = Path(__file__).parent / ".." / "src" / "caos" / "safety" / "policies" / "guardrails.json"
    engine = GuardrailEngine(policy_path=mono_path)
    assert len(engine.rules) == 41
    print("  Monolithic fallback OK")


def test_singleton_getters():
    """Singleton getters work for all new components."""
    from caos.safety.runtime import get_fail_safe, get_backpressure_guard, get_notification_router
    
    fs1 = get_fail_safe()
    fs2 = get_fail_safe()
    assert fs1 is fs2, "FailSafe should be singleton"
    
    bp1 = get_backpressure_guard()
    bp2 = get_backpressure_guard()
    assert bp1 is bp2, "BackpressureGuard should be singleton"
    
    nr1 = get_notification_router()
    nr2 = get_notification_router()
    assert nr1 is nr2, "NotificationRouter should be singleton"
    print("  Singletons OK")


if __name__ == "__main__":
    tests = [
        test_all_imports,
        test_failsafe,
        test_backpressure,
        test_notification_router,
        test_engine_loads_category_files,
        test_engine_fallback_to_monolithic,
        test_singleton_getters,
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
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
    print("ALL P2/P3 FINAL VALIDATION TESTS PASSED")
