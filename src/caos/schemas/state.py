"""JudgeState - The shared state in LangGraph, enriched step by step.

This is the cognitive working memory of the CAOS brain.
"""

from typing import Any, TypedDict

from caos.schemas.action import ActionSchema
from caos.schemas.enums import DecisionBand, RiskLevel
from caos.schemas.risk import RiskDimensions
from caos.schemas.trigger import TriggerPayload


class AssetContext(TypedDict, total=False):
    """Context retrieved from Atlas (Digital Twin + Manuals + Contracts)."""

    # Digital Twin state
    current_state: dict[str, Any]
    last_updated: str
    
    # Technical specifications
    max_operating_temp: float | None
    min_operating_temp: float | None
    max_pressure: float | None
    max_vibration: float | None
    
    # Manual excerpts (RAG results)
    manual_excerpts: list[str]
    
    # Contract information
    contract_id: str | None
    contract_tier: str | None  # MONITORING_ONLY, BASIC, PREMIUM
    allowed_actions: list[str]
    sla_response_time_minutes: int | None
    
    # Maintenance status
    under_maintenance: bool
    maintenance_window_active: bool


class OraclePrediction(TypedDict, total=False):
    """Prediction from Oracle agent (optional, bypassed in Fast-Track)."""

    forecast_metric: str
    predicted_value: float
    confidence: float
    horizon_hours: int
    financial_impact: float | None
    recommendation: str | None


class JudgeState(TypedDict, total=False):
    """The state shared across LangGraph nodes.
    
    This TypedDict represents the cognitive working memory, enriched
    step-by-step as the brain processes information.
    
    Flow:
    1. Trigger arrives from Sentinel
    2. Sense node adds atlas_context
    3. Oracle node (optionally) adds oracle_forecast
    4. Cortex node calculates risk_dimensions, severity_score, verdict_score
    5. Guardrails node validates and adds guardrail_violations
    6. Act node prepares proposed_action for CARE
    """

    # === Identity and Trigger ===
    trigger: TriggerPayload
    
    # === Layer 1 - Sense (Perception) ===
    atlas_context: AssetContext | None  # Digital Twin + Manuals + Contracts
    
    # === Layer 2 - Oracle (Optional Simulation) ===
    oracle_forecast: OraclePrediction | None
    is_fast_track: bool  # True if Oracle was bypassed
    
    # === Layer 3 - Cortex (Reasoning & Decision) ===
    # Risk calculation (hybrid: CODE + LLM)
    risk_dimensions: RiskDimensions  # {R_F, R_Fin, R_C, R_K}
    
    # Scores
    atlas_score: float  # Normalized A score (0.0 to 1.0)
    oracle_score: float  # Normalized O score (0.0 to 1.0), or 1.0 if bypassed
    severity_score: float  # S score (0.0 to 1.0)
    verdict_score: float  # V score (-1.0 to +1.0)
    
    # Classification
    decision_band: DecisionBand  # BLOCKED, ALERT, SUGGEST, EXECUTE
    risk_level: RiskLevel  # LOW, MEDIUM, HIGH, VETO
    
    # Explanation
    reasoning_trace: list[str]  # Chain-of-Thought steps
    
    # === Layer 4 - Guardrails (Validation) ===
    guardrail_violations: list[str]  # List of IDs (e.g., PHYS_001)
    guardrails_checked: list[str]  # All guardrails that were evaluated
    
    # === Layer 5 - Act (Output) ===
    proposed_action: ActionSchema | None  # Final action for CARE
    requires_human_approval: bool
    audit_issues: list[str] | None  # Layer 2 audit findings (if any)
    
    # === Metadata ===
    processing_started_at: str
    processing_completed_at: str | None
    error: str | None
