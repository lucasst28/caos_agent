"""Guardrail Engine - The Immune System of CAOS.

This module implements the 38 guardrail rules that protect the system
from unsafe actions. Guardrails are organized in 3 safety layers:

Layer 0 - Reflex: Hard rules that bypass LLM (< 10ms)
Layer 1 - Cognitive: LLM-enhanced validation (~2s)  
Layer 2 - Audit: Post-check before execution (< 50ms)
"""

import json
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from caos.schemas.enums import GuardrailAction, GuardrailSeverity, RiskLevel
from caos.schemas.state import JudgeState

logger = structlog.get_logger(__name__)


@dataclass
class GuardrailRule:
    """A single guardrail rule definition."""

    id: str
    category: str
    name: str
    condition: str
    action: GuardrailAction
    severity: GuardrailSeverity
    message: str


@dataclass
class GuardrailResult:
    """Result of checking a single guardrail."""

    rule_id: str
    passed: bool
    action: GuardrailAction | None = None
    severity: GuardrailSeverity | None = None
    message: str | None = None


@dataclass
class GuardrailCheckResult:
    """Result of checking all guardrails."""

    passed: bool
    violations: list[GuardrailResult] = field(default_factory=list)
    warnings: list[GuardrailResult] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    penalty: float = 0.0
    final_action: GuardrailAction = GuardrailAction.LOG


class GuardrailEngine:
    """Engine that evaluates all guardrail rules against a state.
    
    The engine supports:
    - Loading rules from JSON policy files
    - Evaluating conditions against JudgeState
    - Calculating penalty scores for the Verdict formula
    - Determining the most restrictive action needed
    """

    # Penalty values for Verdict formula denominator
    SEVERITY_PENALTIES = {
        GuardrailSeverity.BLOCKING: 1.0,
        GuardrailSeverity.HIGH: 0.5,
        GuardrailSeverity.MEDIUM: 0.2,
        GuardrailSeverity.LOW: 0.05,
    }

    def __init__(self, policy_path: str | Path | None = None) -> None:
        """Initialize the engine with a policy file."""
        self.rules: list[GuardrailRule] = []
        self.categories: dict[str, dict[str, Any]] = {}
        
        if policy_path is None:
            # Default to built-in policy
            policy_path = Path(__file__).parent / "policies" / "guardrails.json"
        
        self._load_policy(Path(policy_path))

    def _load_policy(self, path: Path) -> None:
        """Load guardrail rules from JSON policy file."""
        if not path.exists():
            logger.warning("guardrails_policy_not_found", path=str(path))
            return
        
        with open(path) as f:
            data = json.load(f)
        
        self.categories = data.get("categories", {})
        
        for rule_data in data.get("guardrails", []):
            rule = GuardrailRule(
                id=rule_data["id"],
                category=rule_data["category"],
                name=rule_data["name"],
                condition=rule_data["condition"],
                action=GuardrailAction(rule_data["action"]),
                severity=GuardrailSeverity(rule_data["severity"]),
                message=rule_data["message"],
            )
            self.rules.append(rule)
        
        logger.info("guardrails_policy_loaded", rule_count=len(self.rules))

    def _evaluate_condition(self, condition: str, state: JudgeState) -> bool:
        """Evaluate a condition string against the state.
        
        This uses a safe evaluation context with limited access.
        For production, consider using a proper expression evaluator.
        """
        try:
            # Build evaluation context from state
            context = self._build_context(state)
            
            # Use simple pattern matching for common conditions
            return self._safe_eval(condition, context)
        except Exception as e:
            logger.error(
                "guardrail_condition_error",
                condition=condition,
                error=str(e),
            )
            # On error, assume condition is not met (fail open for non-blocking)
            return False

    def _build_context(self, state: JudgeState) -> dict[str, Any]:
        """Build evaluation context from JudgeState."""
        trigger = state.get("trigger")
        atlas = state.get("atlas_context") or {}
        oracle = state.get("oracle_forecast") or {}
        
        return {
            "trigger": trigger,
            "atlas_context": atlas,
            "oracle_forecast": oracle,
            "risk_level": state.get("risk_level"),
            "verdict_score": state.get("verdict_score", 0.0),
            "severity_score": state.get("severity_score", 0.0),
            "proposed_action": state.get("proposed_action"),
        }

    def _safe_eval(self, condition: str, context: dict[str, Any]) -> bool:
        """Safely evaluate a condition.
        
        Instead of using eval(), we use pattern matching for known conditions.
        This is more secure and predictable.
        
        Operator precedence matters: check multi-char operators (>=, <=, !=,
        'not in') BEFORE single-char ones (>, <, 'in').
        """
        import re

        # 1. Handle "not in" BEFORE "in"
        if " not in " in condition:
            parts = condition.split(" not in ", 1)
            if len(parts) == 2:
                left = self._get_value(parts[0].strip(), context)
                right = self._parse_literal(parts[1].strip())
                if isinstance(right, (list, tuple)):
                    return left not in right
                return False

        # 2. Handle "in"
        if " in " in condition:
            parts = condition.split(" in ", 1)
            if len(parts) == 2:
                left = self._get_value(parts[0].strip(), context)
                right = self._parse_literal(parts[1].strip())
                if isinstance(right, (list, tuple)):
                    return left in right
                return False

        # 3. Two-char comparison operators (before single-char)
        for op in ("==", "!=", ">=", "<="):
            if op in condition:
                parts = condition.split(op, 1)
                if len(parts) == 2:
                    left = self._get_value(parts[0].strip(), context)
                    right_raw = parts[1].strip()
                    # Try literal first, then context lookup
                    right = self._parse_literal(right_raw)
                    if right is None and not right_raw.startswith(("'", '"', "[")):
                        right = self._get_value(right_raw, context)

                    if op == "==":
                        return left == right
                    elif op == "!=":
                        return left != right
                    else:
                        # Numeric comparisons
                        if left is None or right is None:
                            return False
                        try:
                            lf, rf = float(left), float(right)
                        except (TypeError, ValueError):
                            return False
                        return lf >= rf if op == ">=" else lf <= rf

        # 4. Single-char comparison operators
        # Use regex to avoid matching inside >=, <=, ==, !=
        for op, char in [("gt", ">"), ("lt", "<")]:
            # Match standalone > or < not preceded/followed by =
            pattern = rf"(?<!=){re.escape(char)}(?!=)"
            match = re.search(pattern, condition)
            if match:
                idx = match.start()
                left_str = condition[:idx].strip()
                right_str = condition[idx + 1:].strip()
                left = self._get_value(left_str, context)
                right = self._get_value(right_str, context)
                if left is None or right is None:
                    return False
                try:
                    lf, rf = float(left), float(right)
                except (TypeError, ValueError):
                    return False
                return lf > rf if char == ">" else lf < rf

        return False

    def _get_value(self, path: str, context: dict[str, Any]) -> Any:
        """Get a value from context using dot notation."""
        # Handle numeric literals
        if path.replace(".", "").replace("-", "").isdigit():
            return float(path)
        
        # Handle expressions like "x * 1.2"
        if "*" in path:
            parts = path.split("*")
            left = self._get_value(parts[0].strip(), context)
            right = self._get_value(parts[1].strip(), context)
            if left is not None and right is not None:
                return float(left) * float(right)
            return None
        
        # Navigate the context
        parts = path.split(".")
        value = context
        
        for part in parts:
            if value is None:
                return None
            if isinstance(value, dict):
                value = value.get(part)
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                return None
        
        return value

    def _parse_literal(self, value: str) -> Any:
        """Parse a literal value from condition string."""
        value = value.strip()
        
        if value == "true":
            return True
        if value == "false":
            return False
        if value == "null" or value == "None":
            return None
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1]
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            # Parse simple list
            items = value[1:-1].split(",")
            return [self._parse_literal(i.strip()) for i in items if i.strip()]
        
        try:
            return float(value)
        except ValueError:
            return value

    def check_rule(self, rule: GuardrailRule, state: JudgeState) -> GuardrailResult:
        """Check a single guardrail rule."""
        try:
            violated = self._evaluate_condition(rule.condition, state)
            
            if violated:
                return GuardrailResult(
                    rule_id=rule.id,
                    passed=False,
                    action=rule.action,
                    severity=rule.severity,
                    message=rule.message,
                )
            
            return GuardrailResult(rule_id=rule.id, passed=True)
        
        except Exception as e:
            logger.error(
                "guardrail_check_error",
                rule_id=rule.id,
                error=str(e),
            )
            return GuardrailResult(rule_id=rule.id, passed=True)

    def check_all(
        self,
        state: JudgeState,
        categories: list[str] | None = None,
    ) -> GuardrailCheckResult:
        """Check all guardrail rules against the state.
        
        Args:
            state: The JudgeState to check
            categories: Optional list of categories to check (default: all)
            
        Returns:
            GuardrailCheckResult with all violations and penalties
        """
        violations: list[GuardrailResult] = []
        warnings: list[GuardrailResult] = []
        checked: list[str] = []
        total_penalty = 0.0
        most_severe_action = GuardrailAction.LOG
        
        for rule in self.rules:
            # Filter by category if specified
            if categories and rule.category not in categories:
                continue
            
            checked.append(rule.id)
            result = self.check_rule(rule, state)
            
            if not result.passed:
                # Calculate penalty
                penalty = self.SEVERITY_PENALTIES.get(rule.severity, 0.0)
                total_penalty += penalty
                
                # Categorize by action
                if rule.action in [GuardrailAction.VETO, GuardrailAction.REQUIRE_APPROVAL]:
                    violations.append(result)
                else:
                    warnings.append(result)
                
                # Track most severe action
                if self._is_more_severe(rule.action, most_severe_action):
                    most_severe_action = rule.action
                
                logger.warning(
                    "guardrail_violation",
                    rule_id=rule.id,
                    category=rule.category,
                    action=rule.action.value,
                    severity=rule.severity.value,
                )
        
        passed = len([v for v in violations if v.action == GuardrailAction.VETO]) == 0
        
        logger.info(
            "guardrails_check_complete",
            checked_count=len(checked),
            violation_count=len(violations),
            warning_count=len(warnings),
            total_penalty=total_penalty,
            passed=passed,
        )
        
        return GuardrailCheckResult(
            passed=passed,
            violations=violations,
            warnings=warnings,
            checked=checked,
            penalty=total_penalty,
            final_action=most_severe_action,
        )

    def _is_more_severe(
        self, action: GuardrailAction, current: GuardrailAction
    ) -> bool:
        """Check if an action is more severe than the current one."""
        severity_order = [
            GuardrailAction.LOG,
            GuardrailAction.ALERT,
            GuardrailAction.DEGRADE,
            GuardrailAction.REQUIRE_APPROVAL,
            GuardrailAction.VETO,
        ]
        return severity_order.index(action) > severity_order.index(current)

    def get_reflex_rules(self) -> list[GuardrailRule]:
        """Get Layer 0 (Reflex) rules - hardcoded safety checks."""
        return [r for r in self.rules if r.severity == GuardrailSeverity.BLOCKING]

    def get_rules_by_category(self, category: str) -> list[GuardrailRule]:
        """Get all rules in a specific category."""
        return [r for r in self.rules if r.category == category]
