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
        """Initialize the engine with a policy file or directory of per-category files."""
        self.rules: list[GuardrailRule] = []
        self.categories: dict[str, dict[str, Any]] = {}
        
        if policy_path is None:
            # Default to built-in policies directory
            policy_path = Path(__file__).parent / "policies"
        
        policy_path = Path(policy_path)
        if policy_path.is_dir():
            self._load_policy_directory(policy_path)
        else:
            self._load_policy(policy_path)

    def _load_policy_directory(self, directory: Path) -> None:
        """Load guardrail rules from per-category JSON files in a directory.
        
        Falls back to the monolithic guardrails.json if no category files found.
        Category files are named: physical.json, financial.json, etc.
        """
        category_files = sorted(directory.glob("*.json"))
        # Filter out the monolithic file and __init__
        cat_files = [f for f in category_files if f.name != "guardrails.json"]
        
        if not cat_files:
            # Fallback to monolithic file
            mono = directory / "guardrails.json"
            if mono.exists():
                self._load_policy(mono)
            return
        
        for cat_file in cat_files:
            try:
                with open(cat_file) as f:
                    data = json.load(f)
                
                cat_name = data.get("category", cat_file.stem.upper())
                if "metadata" in data:
                    self.categories[cat_name] = data["metadata"]
                
                for rule_data in data.get("rules", []):
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
            except Exception as e:
                logger.warning("guardrails_category_load_error", file=str(cat_file), error=str(e))
        
        logger.info(
            "guardrails_policy_loaded",
            rule_count=len(self.rules),
            category_files=len(cat_files),
        )

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

    # Metrics that represent temperature measurements
    TEMPERATURE_METRICS = {
        "temperature", "cabinet_temperature", "temp", "motor_temp",
        "coolant_temp", "ambient_temp", "exhaust_temp",
    }

    def _is_temperature_metric(self, state: JudgeState) -> bool:
        """Check if the trigger metric is a temperature measurement."""
        trigger = state.get("trigger")
        if not trigger or not trigger.metric:
            return False
        return trigger.metric.lower() in self.TEMPERATURE_METRICS

    def _build_context(self, state: JudgeState) -> dict[str, Any]:
        """Build evaluation context from JudgeState.
        
        Populates ALL variables referenced by guardrails.json rules to ensure
        no rules are left as dead/inert.
        """
        import time as _time
        from datetime import datetime, timezone

        trigger = state.get("trigger")
        atlas = state.get("atlas_context") or {}
        oracle = state.get("oracle_forecast") or {}
        proposed_action = state.get("proposed_action")

        # --- Financial context (FIN_001-006) ---
        # Pull live numbers from Circuit Breaker if available
        daily_cost = 0.0
        daily_tokens = 0
        try:
            from caos.safety.runtime import get_circuit_breaker
            cb = get_circuit_breaker()
            cb_status = cb.get_status()
            daily_cost = cb_status.get("cost_used_usd", 0.0)
            daily_tokens = cb_status.get("tokens_used", 0)
        except Exception:
            pass

        from caos.config import get_settings
        settings = get_settings()

        # estimated_cost from proposed action or oracle forecast
        estimated_cost = 0.0
        if proposed_action and hasattr(proposed_action, "estimated_cost"):
            estimated_cost = proposed_action.estimated_cost or 0.0
        elif oracle and oracle.get("financial_impact"):
            estimated_cost = float(oracle["financial_impact"])

        # --- Contractual context (CONTR_003-005) ---
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        processing_time_ms = 0.0
        started_at = state.get("processing_started_at")
        if started_at:
            try:
                t0 = datetime.fromisoformat(started_at)
                processing_time_ms = (now_utc - t0).total_seconds() * 1000
            except Exception:
                pass

        # daily_actions — tracked via rate limiter if available
        daily_actions = 0
        try:
            from caos.safety.runtime import get_rate_limiter
            rl = get_rate_limiter()
            daily_actions = rl.get_daily_count()
        except Exception:
            pass

        # api_calls_per_minute — from rate limiter
        api_calls_per_minute = 0
        try:
            from caos.safety.runtime import get_rate_limiter
            rl = get_rate_limiter()
            api_calls_per_minute = rl.get_requests_per_minute()
        except Exception:
            pass

        # --- Communication context (COMM_003-005) ---
        notifications_last_hour = 0
        try:
            from caos.safety.runtime import get_anti_spam
            spam = get_anti_spam()
            digests = spam.get_digest()
            notifications_last_hour = sum(d.get("total_alerts", 0) for d in digests)
        except Exception:
            pass

        # --- Robustness context (ROBUST_001, 003, 005) ---
        oracle_response_time_ms = state.get("_oracle_response_time_ms", 0.0)
        llm_response_empty = state.get("_llm_response_empty", False)
        circuit_breaker_status = "closed"
        try:
            from caos.safety.runtime import get_circuit_breaker
            cb = get_circuit_breaker()
            if cb.is_tripped():
                circuit_breaker_status = "open"
        except Exception:
            pass

        retry_count = state.get("recycle_count", 0)
        consecutive_failures = state.get("_consecutive_failures", 0)

        # --- Timestamp context (SECOPS_004, 005) ---
        current_time = _time.time()

        return {
            "trigger": trigger,
            "atlas_context": atlas,
            "oracle_forecast": oracle,
            "risk_level": state.get("risk_level"),
            "verdict_score": state.get("verdict_score", 0.0),
            "severity_score": state.get("severity_score", 0.0),
            "proposed_action": proposed_action,
            # Metric-aware flag
            "is_temperature_metric": self._is_temperature_metric(state),
            # Financial (FIN_001-006)
            "daily_cost": daily_cost,
            "daily_tokens": daily_tokens,
            "estimated_cost": estimated_cost,
            "llm_daily_cost_budget_usd": settings.llm_daily_cost_budget_usd,
            "llm_daily_token_budget": settings.llm_daily_token_budget,
            "api_calls_per_minute": api_calls_per_minute,
            # Contractual (CONTR_003-005)
            "processing_time_ms": processing_time_ms,
            "current_hour": current_hour,
            "daily_actions": daily_actions,
            # Communication (COMM_005)
            "notifications_last_hour": notifications_last_hour,
            # Robustness (ROBUST_001-007)
            "oracle_response_time_ms": oracle_response_time_ms,
            "llm_response_empty": llm_response_empty,
            "circuit_breaker_status": circuit_breaker_status,
            "retry_count": retry_count,
            "consecutive_failures": consecutive_failures,
            # Timestamps (SECOPS_004, 005)
            "current_time": current_time,
        }

    def _safe_eval(self, condition: str, context: dict[str, Any]) -> bool:
        """Safely evaluate a condition.
        
        Instead of using eval(), we use pattern matching for known conditions.
        This is more secure and predictable.
        
        Supports:
        - Compound conditions with 'and' / 'or'
        - Comparison operators: ==, !=, >=, <=, >, <
        - Membership: 'in', 'not in'
        - Arithmetic: * (for threshold calculations)
        
        Operator precedence matters: check multi-char operators (>=, <=, !=,
        'not in') BEFORE single-char ones (>, <, 'in').
        """
        # Handle len() function calls: len(x.y) > N
        import re as _re
        len_match = _re.match(r'len\(([^)]+)\)\s*(>|<|>=|<=|==|!=)\s*(.+)', condition)
        if len_match:
            inner_path = len_match.group(1).strip()
            op = len_match.group(2)
            threshold_str = len_match.group(3).strip()
            val = self._get_value(inner_path, context)
            length = len(val) if val is not None and hasattr(val, '__len__') else 0
            threshold = self._get_value(threshold_str, context)
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                return False
            if op == '>': return length > threshold
            if op == '<': return length < threshold
            if op == '>=': return length >= threshold
            if op == '<=': return length <= threshold
            if op == '==': return length == threshold
            if op == '!=': return length != threshold
            return False

        # Handle compound 'and' conditions
        if " and " in condition:
            parts = condition.split(" and ")
            return all(self._safe_eval(p.strip(), context) for p in parts)

        # Handle compound 'or' conditions
        if " or " in condition:
            parts = condition.split(" or ")
            return any(self._safe_eval(p.strip(), context) for p in parts)

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
