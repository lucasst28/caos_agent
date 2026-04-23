"""Enumerations for CAOS Agent decision system."""

from enum import Enum


class Severity(str, Enum):
    """Alert severity levels from Sentinel."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecisionBand(str, Enum):
    """Decision bands based on Verdict score (V).
    
    - BLOCKED: V <= 0.0 - Immediate block, no action taken
    - ALERT: 0.0 < V <= 0.3 - Low confidence, notify operator
    - SUGGEST: 0.3 < V <= 0.7 - Moderate confidence, suggest for approval
    - EXECUTE: V > 0.7 - High confidence, execute autonomously
    """

    BLOCKED = "BLOCKED"
    ALERT = "ALERT"
    SUGGEST = "SUGGEST"
    EXECUTE = "EXECUTE"


class RiskLevel(str, Enum):
    """Risk classification for routing decisions.
    
    - LOW: Direct to CARE
    - MEDIUM: Automatic verification
    - HIGH: Human-in-the-Loop (HITL)
    - VETO: Blocked + HITL
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VETO = "VETO"


class TriggerSource(str, Enum):
    """Source of the trigger event."""

    SENTINEL = "sentinel"
    ORACLE = "oracle"
    MANUAL = "manual"


class ActionType(str, Enum):
    """Types of actions that CAOS can dispatch."""

    SHUTDOWN = "shutdown"
    NOTIFICATION = "notification"
    TICKET = "ticket"
    MAINTENANCE = "maintenance"
    SETPOINT = "setpoint"
    READ = "read"
    OBSERVE = "observe"


class GuardrailAction(str, Enum):
    """Actions that guardrails can take."""

    VETO = "VETO"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    ALERT = "ALERT"
    LOG = "LOG"
    DEGRADE = "DEGRADE"


class GuardrailSeverity(str, Enum):
    """Guardrail severity levels."""

    BLOCKING = "BLOCKING"  # P0 - Unconditional veto
    HIGH = "HIGH"          # P1 - Requires manager approval
    MEDIUM = "MEDIUM"      # P2 - Requires operator approval
    LOW = "LOW"            # P3 - Alert only
