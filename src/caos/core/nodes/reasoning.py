
import structlog
from caos.schemas.state import JudgeState
from caos.schemas.risk import RiskDimensions

logger = structlog.get_logger(__name__)

class Reasoner:
    """Deterministic reasoning engine (CODE path).
    
    Calculates initial risk dimensions based on trigger severity and
    available context, serving as the 'Code Opinion' in the hybrid fusion.
    """
    
    def calculate_risk(self, state: JudgeState) -> RiskDimensions:
        """Calculate risk dimensions based on heuristics."""
        trigger = state.get("trigger")
        severity = trigger.severity if trigger else "MEDIUM"
        
        # Simple heuristic mapping for PoC
        base_score = 0.5
        if severity == "CRITICAL":
            base_score = 0.9
        elif severity == "HIGH":
            base_score = 0.7
        elif severity == "LOW":
            base_score = 0.3
            
        # Refine based on specific metrics
        physical = base_score
        financial = 0.1
        contractual = 0.1
        communication = 0.1
        
        if trigger and trigger.metric == "temperature":
             physical = base_score
        elif trigger and trigger.metric == "latency":
             contractual = base_score
             
        return RiskDimensions(
            physical=physical,
            financial=financial,
            contractual=contractual,
            communication=communication,
            confidence=0.8 # Code is usually confident in its heuristics
        )
