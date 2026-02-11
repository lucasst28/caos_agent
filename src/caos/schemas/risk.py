"""Risk Dimensions schema for CAOS Agent.

The 4 fundamental risk dimensions that guide all CAOS deliberation:
- R_F (Physical): Risk of physical damage to equipment, infrastructure, or people
- R_Fin (Financial): Risk of financial loss (downtime, repairs, contract loss)
- R_C (Contractual): Risk of violating SLAs, contracts, or regulations
- R_K (Communication): Risk of communication failure with stakeholders
"""

from typing import Any

from pydantic import BaseModel, Field


class RiskDimensions(BaseModel):
    """The 4 risk dimensions evaluated for every action.
    
    Each dimension is a normalized score between 0.0 and 1.0:
    - 0.0 = No risk
    - 1.0 = Maximum/Catastrophic risk
    """

    physical: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="R_F",
        description="Physical risk: damage to equipment, infrastructure, or people",
    )
    financial: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="R_Fin",
        description="Financial risk: direct or indirect monetary loss",
    )
    contractual: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="R_C",
        description="Contractual risk: SLA, contract, or compliance violation",
    )
    communication: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="R_K",
        description="Communication risk: failure to notify stakeholders",
    )

    model_config = {"populate_by_name": True}


class RiskWeights(BaseModel):
    """Configurable weights for each risk dimension.
    
    Weights must sum to 1.0 and can be customized per tenant/vertical.
    
    Default values (Standard profile):
    - W_F = 0.35 (Physical)
    - W_Fin = 0.25 (Financial)
    - W_C = 0.25 (Contractual)
    - W_K = 0.15 (Communication)
    """

    w_physical: float = Field(default=0.35, ge=0.0, le=1.0, alias="W_F")
    w_financial: float = Field(default=0.25, ge=0.0, le=1.0, alias="W_Fin")
    w_contractual: float = Field(default=0.25, ge=0.0, le=1.0, alias="W_C")
    w_communication: float = Field(default=0.15, ge=0.0, le=1.0, alias="W_K")

    model_config = {"populate_by_name": True}

    def model_post_init(self, __context: Any) -> None:
        """Validate that weights sum to approximately 1.0 after init."""
        total = self.w_physical + self.w_financial + self.w_contractual + self.w_communication
        if abs(total - 1.0) >= 0.001:
            raise ValueError(
                f"RiskWeights must sum to 1.0 (got {total:.4f}): "
                f"W_F={self.w_physical}, W_Fin={self.w_financial}, "
                f"W_C={self.w_contractual}, W_K={self.w_communication}"
            )

    def validate_sum(self) -> bool:
        """Check that weights sum to approximately 1.0."""
        total = self.w_physical + self.w_financial + self.w_contractual + self.w_communication
        return abs(total - 1.0) < 0.001


class VerdictWeights(BaseModel):
    """Weights for the main Verdict formula.
    
    V = (W_A * A + W_O * O - W_S * (1 + S)) / (1 + Σ(P_G * V_G))
    
    Default values:
    - W_A = 0.6 (Atlas context weight)
    - W_O = 0.6 (Oracle prediction weight)
    - W_S = 0.3 (Severity penalty weight)
    """

    w_atlas: float = Field(default=0.6, ge=0.0, le=1.0, alias="W_A")
    w_oracle: float = Field(default=0.6, ge=0.0, le=1.0, alias="W_O")
    w_severity: float = Field(default=0.3, ge=0.0, le=1.0, alias="W_S")

    model_config = {"populate_by_name": True}
