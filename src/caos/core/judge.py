"""Judge Module - Verdict Algorithm Implementation.

This module implements the core decision formula:
V = (W_A × A + W_O × O - W_S × (1 + S)) / (1 + Σ(P_G × V_G))

Where:
- V: Final Verdict (-1.0 to +1.0)
- A: Atlas score (context support)
- O: Oracle score (prediction confidence)
- S: Severity score (risk level), with (1+S) as base safety cost
- P_G × V_G: Guardrail penalties

Defaults: W_A=0.6, W_O=0.6, W_S=0.3 (calibrated per LaTeX doc §4.1)
"""

from dataclasses import dataclass

from caos.config import get_settings
from caos.schemas.enums import DecisionBand, RiskLevel
from caos.schemas.risk import RiskDimensions, RiskWeights, VerdictWeights


@dataclass
class VerdictResult:
    """Result of the verdict calculation."""

    verdict_score: float  # V: -1.0 to +1.0
    severity_score: float  # S: 0.0 to 1.0
    decision_band: DecisionBand
    risk_level: RiskLevel
    reasoning: str


class JudgeEngine:
    """Engine that calculates the mathematical Verdict.
    
    The Judge doesn't "think" - it calculates. The thinking (LLM) happens
    in the Cortex node, which provides the A and O scores.
    
    This separation ensures:
    1. Auditability: The formula is deterministic and explainable
    2. Safety: The LLM can't override the math
    3. Calibration: Weights can be tuned without changing LLM behavior
    """

    def __init__(
        self,
        verdict_weights: VerdictWeights | None = None,
        risk_weights: RiskWeights | None = None,
    ) -> None:
        """Initialize the Judge with configurable weights."""
        settings = get_settings()
        
        self.verdict_weights = verdict_weights or VerdictWeights(
            w_atlas=settings.weight_atlas,
            w_oracle=settings.weight_oracle,
            w_severity=settings.weight_severity,
        )
        
        self.risk_weights = risk_weights or RiskWeights(
            w_physical=settings.weight_risk_physical,
            w_financial=settings.weight_risk_financial,
            w_contractual=settings.weight_risk_contractual,
            w_communication=settings.weight_risk_communication,
        )
        
        # Decision thresholds
        self.threshold_blocked = settings.threshold_blocked
        self.threshold_alert = settings.threshold_alert
        self.threshold_suggest = settings.threshold_suggest

    def calculate_severity(self, risk_dimensions: RiskDimensions) -> float:
        """Calculate the Severity Score (S) from 4 risk dimensions.
        
        Formula: S = W_F × R_F + W_Fin × R_Fin + W_C × R_C + W_K × R_K
        
        Args:
            risk_dimensions: The 4 risk dimension scores
            
        Returns:
            Severity score between 0.0 and 1.0
        """
        s = (
            self.risk_weights.w_physical * risk_dimensions.physical
            + self.risk_weights.w_financial * risk_dimensions.financial
            + self.risk_weights.w_contractual * risk_dimensions.contractual
            + self.risk_weights.w_communication * risk_dimensions.communication
        )
        # Clamp to [0, 1]
        return max(0.0, min(1.0, s))

    def calculate_verdict(
        self,
        atlas_score: float,
        oracle_score: float,
        severity_score: float,
        guardrail_penalty: float = 0.0,
    ) -> float:
        """Calculate the Verdict Score (V).
        
        Formula: V = (W_A × A + W_O × O - W_S × (1 + S)) / (1 + Σ(P_G × V_G))
        
        Note: The (1 + S) term makes the severity penalty always apply a base
        cost even at S=0, ensuring conservative behaviour. This is intentional
        and differs from the simplified formula in README/LaTeX which shows
        W_S × S for readability.
        
        Args:
            atlas_score: A - Context support score (0.0 to 1.0)
            oracle_score: O - Prediction confidence (0.0 to 1.0), or 1.0 if bypassed
            severity_score: S - Severity from risk dimensions (0.0 to 1.0)
            guardrail_penalty: Sum of P_G × V_G for violated guardrails
            
        Returns:
            Verdict score between -1.0 and +1.0
        """
        # Numerator: benefits minus risks
        numerator = (
            self.verdict_weights.w_atlas * atlas_score
            + self.verdict_weights.w_oracle * oracle_score
            - self.verdict_weights.w_severity * (1 + severity_score)
        )
        
        # Denominator: 1 + guardrail penalties (to avoid division by zero)
        denominator = 1 + guardrail_penalty
        
        # Calculate raw verdict
        v_raw = numerator / denominator
        
        # Clamp to [-1, 1]
        return max(-1.0, min(1.0, v_raw))

    def classify_decision_band(self, verdict_score: float) -> DecisionBand:
        """Classify the verdict into a decision band.
        
        Decision Bands:
        - BLOCKED: V <= 0.0 - Immediate block
        - ALERT: 0.0 < V <= 0.3 - Low confidence, notify operator
        - SUGGEST: 0.3 < V <= 0.7 - Suggest for approval
        - EXECUTE: V > 0.7 - Execute autonomously
        """
        if verdict_score <= self.threshold_blocked:
            return DecisionBand.BLOCKED
        elif verdict_score <= self.threshold_alert:
            return DecisionBand.ALERT
        elif verdict_score <= self.threshold_suggest:
            return DecisionBand.SUGGEST
        else:
            return DecisionBand.EXECUTE

    def classify_risk_level(
        self,
        verdict_score: float,
        risk_dimensions: RiskDimensions,
    ) -> RiskLevel:
        """Classify the overall risk level for routing.
        
        Risk Levels (combine verdict + dimension analysis):
        - LOW: V > 0.7 and all R_x < 0.3 → Direct to CARE
        - MEDIUM: 0.3 < V <= 0.7 or any R_x between 0.3 and 0.7 → Auto verification
        - HIGH: V <= 0.3 or any R_x > 0.7 → HITL
        - VETO: V = -1.0 (Guardrail violation) → BLOCKED + HITL
        """
        # Check for any critical dimension
        max_risk = max(
            risk_dimensions.physical,
            risk_dimensions.financial,
            risk_dimensions.contractual,
            risk_dimensions.communication,
        )
        
        # VETO: Guardrail violation (verdict at floor)
        if verdict_score <= -0.99:
            return RiskLevel.VETO
        
        # HIGH: Low verdict OR any critical dimension
        if verdict_score <= 0.3 or max_risk > 0.7:
            return RiskLevel.HIGH
        
        # LOW: High verdict AND all dimensions safe
        if verdict_score > 0.7 and max_risk < 0.3:
            return RiskLevel.LOW
        
        # MEDIUM: Everything else
        return RiskLevel.MEDIUM

    def judge(
        self,
        atlas_score: float,
        oracle_score: float,
        risk_dimensions: RiskDimensions,
        guardrail_penalty: float = 0.0,
    ) -> VerdictResult:
        """Execute the full judgment process.
        
        This is the main entry point that combines all calculations.
        
        Args:
            atlas_score: Context support from Atlas (0.0-1.0)
            oracle_score: Prediction confidence from Oracle (0.0-1.0)
            risk_dimensions: The 4 risk dimension scores
            guardrail_penalty: Sum of guardrail penalties
            
        Returns:
            VerdictResult with all scores and classifications
        """
        # Step 1: Calculate Severity
        severity = self.calculate_severity(risk_dimensions)
        
        # Step 2: Calculate Verdict
        verdict = self.calculate_verdict(
            atlas_score=atlas_score,
            oracle_score=oracle_score,
            severity_score=severity,
            guardrail_penalty=guardrail_penalty,
        )
        
        # Step 3: Classify Decision Band
        decision_band = self.classify_decision_band(verdict)
        
        # Step 4: Classify Risk Level
        risk_level = self.classify_risk_level(verdict, risk_dimensions)
        
        # Generate reasoning
        reasoning = self._generate_reasoning(
            atlas_score=atlas_score,
            oracle_score=oracle_score,
            severity=severity,
            verdict=verdict,
            decision_band=decision_band,
            risk_level=risk_level,
            risk_dimensions=risk_dimensions,
        )
        
        return VerdictResult(
            verdict_score=verdict,
            severity_score=severity,
            decision_band=decision_band,
            risk_level=risk_level,
            reasoning=reasoning,
        )

    def _generate_reasoning(
        self,
        atlas_score: float,
        oracle_score: float,
        severity: float,
        verdict: float,
        decision_band: DecisionBand,
        risk_level: RiskLevel,
        risk_dimensions: RiskDimensions,
    ) -> str:
        """Generate a human-readable explanation of the judgment."""
        lines = [
            f"Verdict Calculation: V = {verdict:.3f}",
            f"  - Atlas (A): {atlas_score:.2f} × {self.verdict_weights.w_atlas} = {atlas_score * self.verdict_weights.w_atlas:.3f}",
            f"  - Oracle (O): {oracle_score:.2f} × {self.verdict_weights.w_oracle} = {oracle_score * self.verdict_weights.w_oracle:.3f}",
            f"  - Severity (S): {severity:.2f} × {self.verdict_weights.w_severity} = {severity * self.verdict_weights.w_severity:.3f}",
            "",
            f"Risk Dimensions:",
            f"  - Physical (R_F): {risk_dimensions.physical:.2f}",
            f"  - Financial (R_Fin): {risk_dimensions.financial:.2f}",
            f"  - Contractual (R_C): {risk_dimensions.contractual:.2f}",
            f"  - Communication (R_K): {risk_dimensions.communication:.2f}",
            "",
            f"Classification: {decision_band.value} | Risk: {risk_level.value}",
        ]
        return "\n".join(lines)
