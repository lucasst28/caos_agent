"""Tests for the Judge Engine and Verdict calculation."""

import pytest
from caos.core.judge import JudgeEngine, VerdictResult
from caos.schemas.enums import DecisionBand, RiskLevel
from caos.schemas.risk import RiskDimensions, RiskWeights, VerdictWeights


class TestJudgeEngine:
    """Test suite for JudgeEngine."""

    def setup_method(self):
        """Setup test fixtures."""
        self.judge = JudgeEngine()

    # === Severity Calculation Tests ===

    def test_severity_all_zeros(self):
        """Severity should be 0 when all risks are 0."""
        risk = RiskDimensions(physical=0, financial=0, contractual=0, communication=0)
        severity = self.judge.calculate_severity(risk)
        assert severity == 0.0

    def test_severity_all_ones(self):
        """Severity should be 1 when all risks are 1."""
        risk = RiskDimensions(physical=1, financial=1, contractual=1, communication=1)
        severity = self.judge.calculate_severity(risk)
        assert severity == 1.0

    def test_severity_physical_only(self):
        """Severity with only physical risk."""
        risk = RiskDimensions(physical=1.0, financial=0, contractual=0, communication=0)
        severity = self.judge.calculate_severity(risk)
        assert severity == pytest.approx(0.35, rel=0.01)  # W_F = 0.35

    def test_severity_mixed_risks(self):
        """Severity with mixed risk dimensions."""
        risk = RiskDimensions(physical=0.9, financial=0.5, contractual=0.2, communication=0.1)
        severity = self.judge.calculate_severity(risk)
        # S = 0.35*0.9 + 0.25*0.5 + 0.25*0.2 + 0.15*0.1
        # S = 0.315 + 0.125 + 0.05 + 0.015 = 0.505
        assert severity == pytest.approx(0.505, rel=0.01)

    # === Verdict Calculation Tests ===

    def test_verdict_normal_operation(self):
        """Scenario 1: Normal operation (high A, high O, low S)."""
        verdict = self.judge.calculate_verdict(
            atlas_score=1.0,
            oracle_score=0.8,
            severity_score=0.0,
            guardrail_penalty=0.0,
        )
        # V = (0.6*1.0 + 0.6*0.8 - 0.3*1.0) / 1.0
        # V = (0.6 + 0.48 - 0.3) / 1.0 = 0.78
        assert verdict == pytest.approx(0.78, rel=0.05)
        assert verdict > 0.7  # Should be EXECUTE

    def test_verdict_fire_scenario(self):
        """Scenario 2: Fire/emergency (low A, low O, high S)."""
        verdict = self.judge.calculate_verdict(
            atlas_score=0.2,
            oracle_score=0.0,
            severity_score=1.0,
            guardrail_penalty=0.0,
        )
        # V = (0.6*0.2 + 0.6*0.0 - 0.3*2.0) / 1.0
        # V = (0.12 + 0 - 0.6) / 1.0 = -0.48
        assert verdict < 0  # Should be BLOCKED

    def test_verdict_with_guardrail_penalty(self):
        """Verdict should decrease with guardrail penalties."""
        verdict_no_penalty = self.judge.calculate_verdict(
            atlas_score=0.8,
            oracle_score=0.8,
            severity_score=0.2,
        )
        verdict_with_penalty = self.judge.calculate_verdict(
            atlas_score=0.8,
            oracle_score=0.8,
            severity_score=0.2,
            guardrail_penalty=1.0,
        )
        assert verdict_with_penalty < verdict_no_penalty

    # === Decision Band Tests ===

    def test_decision_band_blocked(self):
        """Negative verdict should be BLOCKED."""
        band = self.judge.classify_decision_band(-0.5)
        assert band == DecisionBand.BLOCKED

    def test_decision_band_alert(self):
        """Low positive verdict should be ALERT."""
        band = self.judge.classify_decision_band(0.15)
        assert band == DecisionBand.ALERT

    def test_decision_band_suggest(self):
        """Medium verdict should be SUGGEST."""
        band = self.judge.classify_decision_band(0.5)
        assert band == DecisionBand.SUGGEST

    def test_decision_band_execute(self):
        """High verdict should be EXECUTE."""
        band = self.judge.classify_decision_band(0.85)
        assert band == DecisionBand.EXECUTE

    # === Risk Level Tests ===

    def test_risk_level_low(self):
        """Low risk when verdict high and all dimensions safe."""
        risk = RiskDimensions(physical=0.1, financial=0.1, contractual=0.1, communication=0.1)
        level = self.judge.classify_risk_level(0.8, risk)
        assert level == RiskLevel.LOW

    def test_risk_level_high_by_verdict(self):
        """High risk when verdict is low."""
        risk = RiskDimensions(physical=0.1, financial=0.1, contractual=0.1, communication=0.1)
        level = self.judge.classify_risk_level(0.2, risk)
        assert level == RiskLevel.HIGH

    def test_risk_level_high_by_dimension(self):
        """High risk when any dimension is critical."""
        risk = RiskDimensions(physical=0.9, financial=0.1, contractual=0.1, communication=0.1)
        level = self.judge.classify_risk_level(0.8, risk)
        assert level == RiskLevel.HIGH

    def test_risk_level_veto(self):
        """Veto when verdict is at floor."""
        risk = RiskDimensions(physical=0.5, financial=0.5, contractual=0.5, communication=0.5)
        level = self.judge.classify_risk_level(-1.0, risk)
        assert level == RiskLevel.VETO

    # === Full Judge Tests ===

    def test_judge_normal_scenario(self):
        """Full judgment for normal operation."""
        risk = RiskDimensions(physical=0.1, financial=0.2, contractual=0.1, communication=0.1)
        result = self.judge.judge(
            atlas_score=0.9,
            oracle_score=0.8,
            risk_dimensions=risk,
        )
        assert isinstance(result, VerdictResult)
        assert result.severity_score < 0.3
        assert result.verdict_score > 0.5
        assert result.decision_band in [DecisionBand.SUGGEST, DecisionBand.EXECUTE]

    def test_judge_emergency_scenario(self):
        """Full judgment for emergency."""
        risk = RiskDimensions(physical=0.95, financial=0.8, contractual=0.3, communication=0.2)
        result = self.judge.judge(
            atlas_score=0.3,
            oracle_score=0.1,
            risk_dimensions=risk,
        )
        assert result.severity_score > 0.5
        assert result.decision_band == DecisionBand.BLOCKED
        assert result.risk_level == RiskLevel.HIGH


class TestVerdictScenarios:
    """Test the 5 validation scenarios from the documentation."""

    def setup_method(self):
        """Setup with documentation weights."""
        self.judge = JudgeEngine(
            verdict_weights=VerdictWeights(w_atlas=0.6, w_oracle=0.6, w_severity=0.3)
        )

    def test_scenario_1_normal_operation(self):
        """Scenario 1: Normal Operation - A=1.0, O=0.8, S=0.0 → V≈0.78"""
        risk = RiskDimensions(physical=0, financial=0, contractual=0, communication=0)
        result = self.judge.judge(
            atlas_score=1.0,
            oracle_score=0.8,
            risk_dimensions=risk,
        )
        assert result.verdict_score == pytest.approx(0.78, rel=0.1)
        assert result.decision_band == DecisionBand.EXECUTE

    def test_scenario_2_fire(self):
        """Scenario 2: Fire - A=0.2, O=0.0, S=1.0 → V≈-0.48"""
        risk = RiskDimensions(physical=1.0, financial=0.8, contractual=0.5, communication=0.3)
        severity = self.judge.calculate_severity(risk)
        # Note: severity will be ~0.82, not 1.0
        result = self.judge.judge(
            atlas_score=0.2,
            oracle_score=0.0,
            risk_dimensions=risk,
        )
        assert result.verdict_score < 0
        assert result.decision_band == DecisionBand.BLOCKED

    def test_scenario_3_high_profit_medium_risk(self):
        """Scenario 3: High profit/medium risk - A=1.0, O=0.9, S=0.4 → V≈0.72"""
        risk = RiskDimensions(physical=0.4, financial=0.4, contractual=0.4, communication=0.4)
        result = self.judge.judge(
            atlas_score=1.0,
            oracle_score=0.9,
            risk_dimensions=risk,
        )
        assert result.verdict_score > 0.5
        assert result.decision_band in [DecisionBand.SUGGEST, DecisionBand.EXECUTE]
