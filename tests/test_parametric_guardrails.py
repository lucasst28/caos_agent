
import pytest
from datetime import datetime, timezone
from caos.safety.layers import ReflexLayer, GuardrailAction, Severity
from caos.schemas.state import JudgeState
from caos.schemas.trigger import TriggerPayload, TriggerContext, TriggerSource

def make_state(
    value: float,
    max_operating_temp: float | None = None,
    metric: str = "temperature"
) -> JudgeState:
    atlas_context = {}
    if max_operating_temp is not None:
        atlas_context["max_operating_temp"] = max_operating_temp

    return JudgeState(
        trigger=TriggerPayload(
            event_id="evt_param_test",
            source=TriggerSource.SENTINEL,
            timestamp=datetime.now(timezone.utc),
            severity=Severity.HIGH,
            metric=metric,
            value=value,
            context=TriggerContext(tenant_id="t1", asset_id="a1"),
        ),
        atlas_context=atlas_context,
    )

class TestParametricReflex:
    def setup_method(self):
        self.reflex = ReflexLayer()

    def test_fallback_to_default_110(self):
        """Should use 110.0 if max_operating_temp is missing."""
        # 105 should pass (below 110)
        state_pass = make_state(value=105.0, max_operating_temp=None)
        assert not self.reflex.check(state_pass).triggered

        # 111 should fail (above 110)
        state_fail = make_state(value=111.0, max_operating_temp=None)
        res = self.reflex.check(state_fail)
        assert res.triggered
        assert res.rule_id == "PHYS_008"
        assert "110.0" in res.message  # Check message formatting

    def test_dynamic_threshold_low(self):
        """Should use dynamic threshold (e.g. 50 * 1.1 = 55)."""
        # Asset limit 50 -> Safety limit 55
        
        # 54 should pass
        state_pass = make_state(value=54.0, max_operating_temp=50.0)
        assert not self.reflex.check(state_pass).triggered

        # 56 should fail
        state_fail = make_state(value=56.0, max_operating_temp=50.0)
        res = self.reflex.check(state_fail)
        assert res.triggered
        assert res.rule_id == "PHYS_008"
        assert "55.0" in res.message

    def test_dynamic_threshold_high(self):
        """Should handle high temp assets (Furnace limit 1000 -> 1100)."""
        # Asset limit 1000 -> Safety limit 1100
        
        # 1050 should pass (even though > 110 default)
        state_pass = make_state(value=1050.0, max_operating_temp=1000.0)
        assert not self.reflex.check(state_pass).triggered

        # 1200 should fail
        state_fail = make_state(value=1200.0, max_operating_temp=1000.0)
        res = self.reflex.check(state_fail)
        assert res.triggered
