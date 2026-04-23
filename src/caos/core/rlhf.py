"""RLHF Learning Loop — Bayesian Online Weight Optimization.

Implements a Contextual Thompson Sampling approach to automatically adjust
the CAOS verdict formula weights based on human feedback (approve/reject).

Each weight is modeled as a Beta(α, β) distribution. On approval, α increases
(weight was "correct"); on rejection, β increases (weight was "wrong").
The magnitude of the update is proportional to the verdict's proximity to
the decision boundary — edge cases get stronger learning signals.

Safety constraints:
  - KL divergence bound: rejects updates that drift too far from prior
  - Hard clamps: W_S ∈ [0.15, 0.60], all weights ∈ [0.05, 0.95]
  - Risk weights forced to sum to 1.0 after each update
  - Minimum 20 experiences before first update

Usage:
    optimizer = get_rlhf_optimizer()
    optimizer.record_experience(experience)      # called by act_node
    optimizer.process_feedback(action_id, True)   # called by feedback
    weights = optimizer.get_current_weights()     # called by judge
"""

import json
import math
import os
import random
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from caos.config import get_settings

logger = structlog.get_logger(__name__)


# =============================================
# Data Structures
# =============================================

@dataclass
class ExperienceRecord:
    """Full context vector for a single CAOS decision + feedback."""

    # Identity
    action_id: str
    event_id: str
    tenant_id: str
    asset_id: str
    timestamp: str = ""

    # Input features (what the Judge saw)
    atlas_score: float = 0.0
    oracle_score: float = 0.0
    severity_score: float = 0.0
    risk_physical: float = 0.0
    risk_financial: float = 0.0
    risk_contractual: float = 0.0
    risk_communication: float = 0.0
    guardrail_penalty: float = 0.0

    # Output (what the Judge decided)
    verdict_score: float = 0.0
    decision_band: str = ""
    risk_level: str = ""
    action_type: str = ""

    # Weights used at decision time
    weights_used: dict[str, float] = field(default_factory=dict)

    # Feedback (filled later)
    feedback_received: bool = False
    approved: bool | None = None
    resolver: str | None = None
    feedback_at: str | None = None


@dataclass
class WeightPosterior:
    """Beta(α, β) posterior for a single weight parameter."""

    name: str
    alpha: float  # "successes" — grows on approval
    beta: float   # "failures" — grows on rejection
    min_value: float = 0.05   # hard clamp floor
    max_value: float = 0.95   # hard clamp ceiling

    @property
    def mean(self) -> float:
        """Posterior mean: E[X] = α / (α + β)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        """Posterior variance."""
        a, b = self.alpha, self.beta
        return (a * b) / ((a + b) ** 2 * (a + b + 1))

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def ci_95(self) -> tuple[float, float]:
        """Approximate 95% credible interval using normal approximation."""
        m = self.mean
        s = self.std
        return (max(self.min_value, m - 1.96 * s), min(self.max_value, m + 1.96 * s))

    def sample(self) -> float:
        """Thompson Sampling: draw from Beta posterior, clamp to bounds."""
        val = random.betavariate(self.alpha, self.beta)
        return max(self.min_value, min(self.max_value, val))

    def clamped_mean(self) -> float:
        """Mean clamped to [min_value, max_value]."""
        return max(self.min_value, min(self.max_value, self.mean))


# =============================================
# Safety Guard
# =============================================

class SafetyGuard:
    """Enforces safety constraints on weight updates."""

    def __init__(
        self,
        kl_bound: float = 0.1,
        min_samples: int = 20,
        w_severity_floor: float = 0.15,
        w_severity_ceiling: float = 0.60,
    ) -> None:
        self.kl_bound = kl_bound
        self.min_samples = min_samples
        self.w_severity_floor = w_severity_floor
        self.w_severity_ceiling = w_severity_ceiling

    @staticmethod
    def _digamma(x: float) -> float:
        """Digamma function ψ(x) via asymptotic expansion.
        
        Uses recurrence ψ(x+1) = ψ(x) + 1/x to shift x ≥ 8,
        then applies the asymptotic series.
        """
        result = 0.0
        # Shift x up using recurrence relation
        while x < 8.0:
            result -= 1.0 / x
            x += 1.0
        # Asymptotic series for large x
        # ψ(x) ≈ ln(x) - 1/(2x) - 1/(12x²) + 1/(120x⁴) - 1/(252x⁶)
        result += math.log(x) - 0.5 / x
        x2 = x * x
        result -= 1.0 / (12.0 * x2)
        result += 1.0 / (120.0 * x2 * x2)
        result -= 1.0 / (252.0 * x2 * x2 * x2)
        return result

    @staticmethod
    def kl_divergence_beta(p: WeightPosterior, q_alpha: float, q_beta: float) -> float:
        """KL(P || Q) for two Beta distributions.

        Uses the closed-form: KL(Beta(α₁,β₁) || Beta(α₂,β₂))
        """
        a1, b1 = p.alpha, p.beta
        a2, b2 = q_alpha, q_beta

        ln_B = (math.lgamma(a2) + math.lgamma(b2) - math.lgamma(a2 + b2)) - \
               (math.lgamma(a1) + math.lgamma(b1) - math.lgamma(a1 + b1))

        psi_sum = SafetyGuard._digamma(a1 + b1)
        kl = ln_B + (a1 - a2) * (SafetyGuard._digamma(a1) - psi_sum) + \
             (b1 - b2) * (SafetyGuard._digamma(b1) - psi_sum)
        return max(0.0, kl)

    def check_update_safe(
        self,
        posteriors: dict[str, WeightPosterior],
        priors: dict[str, tuple[float, float]],
        total_samples: int,
    ) -> tuple[bool, str]:
        """Check if a weight update is safe to apply.

        Returns (safe, reason).
        """
        if total_samples < self.min_samples:
            return False, f"Insufficient samples: {total_samples} < {self.min_samples}"

        # Check KL divergence for each weight
        for name, post in posteriors.items():
            prior_a, prior_b = priors[name]
            kl = self.kl_divergence_beta(post, prior_a, prior_b)
            if kl > self.kl_bound:
                return False, f"KL divergence for {name} = {kl:.4f} > bound {self.kl_bound}"

        return True, "ok"

    def enforce_constraints(self, posteriors: dict[str, WeightPosterior]) -> None:
        """Apply hard constraints after update."""
        # W_S severity floor/ceiling
        ws = posteriors.get("w_severity")
        if ws:
            ws.min_value = self.w_severity_floor
            ws.max_value = self.w_severity_ceiling

        # Normalize risk weights to sum to 1.0
        risk_keys = ["w_physical", "w_financial", "w_contractual", "w_communication"]
        risk_posts = [posteriors[k] for k in risk_keys if k in posteriors]
        if risk_posts:
            total = sum(p.clamped_mean() for p in risk_posts)
            if total > 0 and abs(total - 1.0) > 0.001:
                # Scale α values to produce normalized means
                for p in risk_posts:
                    ratio = p.clamped_mean() / total
                    # Adjust α,β to produce the normalized mean while keeping α+β constant
                    concentration = p.alpha + p.beta
                    p.alpha = max(1.0, ratio * concentration)
                    p.beta = max(1.0, (1 - ratio) * concentration)


# =============================================
# Bayesian Weight Optimizer
# =============================================

class BayesianWeightOptimizer:
    """Bayesian online learner for CAOS verdict weights.

    Uses Thompson Sampling with Beta posteriors to balance exploration
    and exploitation while maintaining safety constraints.
    """

    # Default priors — set α,β to produce the correct default mean
    DEFAULT_PRIORS: dict[str, tuple[float, float, float, float]] = {
        # name: (alpha, beta, min, max)
        "w_atlas":         (12.0,  8.0, 0.05, 0.95),  # mean=0.60
        "w_oracle":        (12.0,  8.0, 0.05, 0.95),  # mean=0.60
        "w_severity":      ( 6.0, 14.0, 0.15, 0.60),  # mean=0.30
        "w_physical":      ( 7.0, 13.0, 0.05, 0.95),  # mean=0.35
        "w_financial":     ( 5.0, 15.0, 0.05, 0.95),  # mean=0.25
        "w_contractual":   ( 5.0, 15.0, 0.05, 0.95),  # mean=0.25
        "w_communication": ( 3.0, 17.0, 0.05, 0.95),  # mean=0.15
    }

    def __init__(self, data_dir: str | None = None) -> None:
        settings = get_settings()
        self._data_dir = Path(data_dir or settings.rlhf_data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._safety = SafetyGuard(
            kl_bound=settings.rlhf_kl_bound,
            min_samples=settings.rlhf_min_samples,
        )
        self._batch_size = settings.rlhf_batch_size
        self._exploration = settings.rlhf_exploration_mode
        self._frozen = False

        # Initialize posteriors (load from disk or use priors)
        self._posteriors: dict[str, WeightPosterior] = {}
        self._priors: dict[str, tuple[float, float]] = {}
        self._init_posteriors()

        # Experience buffer
        self._experiences: dict[str, ExperienceRecord] = {}  # keyed by action_id
        self._pending_feedback_count = 0
        self._total_feedbacks = 0

        # Load persisted state
        self._load_state()

    def _init_posteriors(self) -> None:
        """Initialize posteriors from priors."""
        for name, (alpha, beta, min_v, max_v) in self.DEFAULT_PRIORS.items():
            self._posteriors[name] = WeightPosterior(
                name=name, alpha=alpha, beta=beta,
                min_value=min_v, max_value=max_v,
            )
            self._priors[name] = (alpha, beta)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _weights_file(self) -> Path:
        return self._data_dir / "weights.json"

    def _history_file(self) -> Path:
        return self._data_dir / "weight_history.jsonl"

    def _experiences_file(self) -> Path:
        return self._data_dir / "experiences.jsonl"

    def _load_state(self) -> None:
        """Load posteriors from disk if available."""
        wf = self._weights_file()
        if wf.exists():
            try:
                data = json.loads(wf.read_text())
                for name, vals in data.get("posteriors", {}).items():
                    if name in self._posteriors:
                        self._posteriors[name].alpha = vals["alpha"]
                        self._posteriors[name].beta = vals["beta"]
                self._total_feedbacks = data.get("total_feedbacks", 0)
                self._frozen = data.get("frozen", False)
                logger.info("rlhf_state_loaded", total_feedbacks=self._total_feedbacks)
            except Exception as e:
                logger.warning("rlhf_state_load_failed", error=str(e))

    def _save_state(self) -> None:
        """Persist posteriors to disk."""
        data = {
            "posteriors": {
                name: {"alpha": p.alpha, "beta": p.beta, "mean": p.clamped_mean()}
                for name, p in self._posteriors.items()
            },
            "total_feedbacks": self._total_feedbacks,
            "frozen": self._frozen,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._weights_file().write_text(json.dumps(data, indent=2))

    def _log_weight_update(self, old_weights: dict, new_weights: dict, trigger: str) -> None:
        """Append a weight update record to history."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
            "total_feedbacks": self._total_feedbacks,
            "old_weights": old_weights,
            "new_weights": new_weights,
            "kl_divergences": {
                name: SafetyGuard.kl_divergence_beta(
                    self._posteriors[name], *self._priors[name]
                )
                for name in self._posteriors
            },
        }
        with open(self._history_file(), "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    # ------------------------------------------------------------------
    # Experience Management
    # ------------------------------------------------------------------

    def record_experience(self, exp: ExperienceRecord) -> None:
        """Store an experience (called by act_node after verdict)."""
        exp.timestamp = datetime.now(timezone.utc).isoformat()
        exp.weights_used = self.get_current_weights()

        with self._lock:
            self._experiences[exp.action_id] = exp

        # Persist to disk
        try:
            with open(self._experiences_file(), "a") as f:
                f.write(json.dumps(asdict(exp), default=str) + "\n")
        except Exception as e:
            logger.error("rlhf_experience_persist_error", error=str(e))

        logger.debug("rlhf_experience_recorded", action_id=exp.action_id)

    def process_feedback(self, action_id: str, approved: bool, resolver: str = "") -> bool:
        """Process human feedback for a recorded experience.

        Updates the Beta posteriors and triggers a batch update if threshold met.
        Returns True if weights were updated.
        """
        if self._frozen:
            logger.info("rlhf_frozen_skip", action_id=action_id)
            return False

        with self._lock:
            exp = self._experiences.get(action_id)
            if exp is None:
                logger.warning("rlhf_experience_not_found", action_id=action_id)
                return False

            if exp.feedback_received:
                logger.warning("rlhf_duplicate_feedback", action_id=action_id)
                return False

            # Complete the experience
            exp.feedback_received = True
            exp.approved = approved
            exp.resolver = resolver
            exp.feedback_at = datetime.now(timezone.utc).isoformat()
            self._total_feedbacks += 1
            self._pending_feedback_count += 1

        # Calculate learning signal strength
        signal_strength = self._compute_signal_strength(exp)

        # Update posteriors
        old_weights = self.get_current_weights()
        self._update_posteriors(exp, signal_strength)

        # Check if batch threshold met for safety check + persist
        updated = False
        if self._pending_feedback_count >= self._batch_size:
            safe, reason = self._safety.check_update_safe(
                self._posteriors, self._priors, self._total_feedbacks,
            )
            if safe:
                self._safety.enforce_constraints(self._posteriors)
                new_weights = self.get_current_weights()
                self._log_weight_update(old_weights, new_weights, f"batch_{self._total_feedbacks}")
                self._save_state()
                updated = True
                logger.info(
                    "rlhf_weights_updated",
                    total_feedbacks=self._total_feedbacks,
                    weights=new_weights,
                )
            else:
                logger.warning("rlhf_update_rejected", reason=reason)
                # Rollback: reload last safe state
                self._load_state()

            self._pending_feedback_count = 0

        return updated

    def _compute_signal_strength(self, exp: ExperienceRecord) -> float:
        """Compute how strong the learning signal should be.

        Stronger signals for edge cases (verdicts near decision boundaries).
        Weaker signals for clear-cut decisions.

        Returns a value between 0.1 and 2.0.
        """
        v = abs(exp.verdict_score)

        # Decision boundaries at 0.0, 0.3, 0.7
        boundaries = [0.0, 0.3, 0.7]
        min_dist = min(abs(v - b) for b in boundaries)

        # Closer to boundary = stronger signal (inverse distance)
        # max signal at boundary (dist=0), min signal far from boundary (dist≥0.3)
        if min_dist < 0.01:
            strength = 2.0
        elif min_dist < 0.1:
            strength = 1.5
        elif min_dist < 0.2:
            strength = 1.0
        else:
            strength = 0.5

        return strength

    def _update_posteriors(self, exp: ExperienceRecord, signal: float) -> None:
        """Update Beta posteriors based on feedback.

        On approval: increase α (the weight was "right")
        On rejection: increase β (the weight was "wrong")

        The update is applied to each weight proportionally to its contribution
        to the verdict.
        """
        # Compute contribution of each weight to the verdict
        contributions = self._compute_weight_contributions(exp)

        with self._lock:
            for name, post in self._posteriors.items():
                contrib = contributions.get(name, 0.0)
                # Scale update by contribution magnitude and signal strength
                update = signal * max(0.1, abs(contrib))

                if exp.approved:
                    # Approval: the weight's contribution was correct
                    if contrib > 0:
                        # Weight contributed positively to a good decision
                        post.alpha += update
                    else:
                        # Weight contributed negatively but overall was approved
                        # — mild reinforcement
                        post.alpha += update * 0.3
                else:
                    # Rejection: the weight's contribution was wrong
                    if contrib > 0:
                        # Weight contributed positively to a bad decision
                        post.beta += update
                    else:
                        # Weight contributed negatively to a bad decision
                        # — it was actually trying to prevent this
                        post.alpha += update * 0.2

    def _compute_weight_contributions(self, exp: ExperienceRecord) -> dict[str, float]:
        """Compute how much each weight contributed to the verdict.

        Uses the partial derivative of V w.r.t. each weight as the contribution.
        V = (W_A*A + W_O*O - W_S*(1+S)) / (1 + Σ P_G*V_G)
        """
        denom = 1 + exp.guardrail_penalty

        # Verdict weights — direct partial derivatives
        contributions = {
            "w_atlas": exp.atlas_score / denom,           # ∂V/∂W_A = A / denom
            "w_oracle": exp.oracle_score / denom,         # ∂V/∂W_O = O / denom
            "w_severity": -(1 + exp.severity_score) / denom,  # ∂V/∂W_S = -(1+S)/denom
        }

        # Risk weights — contribute through Severity S
        # S = W_F*R_F + W_Fin*R_Fin + W_C*R_C + W_K*R_K
        # ∂V/∂W_i = ∂V/∂S × ∂S/∂W_i = (-W_S/denom) × R_i
        ws = exp.weights_used.get("w_severity", 0.3)
        dv_ds = -ws / denom

        contributions["w_physical"] = dv_ds * exp.risk_physical
        contributions["w_financial"] = dv_ds * exp.risk_financial
        contributions["w_contractual"] = dv_ds * exp.risk_contractual
        contributions["w_communication"] = dv_ds * exp.risk_communication

        return contributions

    # ------------------------------------------------------------------
    # Weight Access
    # ------------------------------------------------------------------

    def get_current_weights(self) -> dict[str, float]:
        """Get current weight point estimates (posterior means, clamped)."""
        with self._lock:
            weights = {name: p.clamped_mean() for name, p in self._posteriors.items()}

        # Ensure risk weights sum to 1.0
        risk_keys = ["w_physical", "w_financial", "w_contractual", "w_communication"]
        risk_total = sum(weights[k] for k in risk_keys)
        if risk_total > 0:
            for k in risk_keys:
                weights[k] = weights[k] / risk_total

        return weights

    def sample_weights(self) -> dict[str, float]:
        """Thompson Sampling: draw weights from posteriors (exploration mode)."""
        with self._lock:
            weights = {name: p.sample() for name, p in self._posteriors.items()}

        # Normalize risk weights
        risk_keys = ["w_physical", "w_financial", "w_contractual", "w_communication"]
        risk_total = sum(weights[k] for k in risk_keys)
        if risk_total > 0:
            for k in risk_keys:
                weights[k] = weights[k] / risk_total

        return weights

    def get_weight_details(self) -> dict[str, dict[str, Any]]:
        """Get full posterior details for all weights (for API/monitoring)."""
        with self._lock:
            return {
                name: {
                    "alpha": round(p.alpha, 4),
                    "beta": round(p.beta, 4),
                    "mean": round(p.clamped_mean(), 4),
                    "std": round(p.std, 4),
                    "ci_95": [round(x, 4) for x in p.ci_95()],
                    "min": p.min_value,
                    "max": p.max_value,
                    "kl_from_prior": round(
                        SafetyGuard.kl_divergence_beta(p, *self._priors[name]), 6
                    ),
                }
                for name, p in self._posteriors.items()
            }

    def get_stats(self) -> dict[str, Any]:
        """Get optimizer statistics."""
        # Count feedback by outcome
        approved_count = sum(1 for e in self._experiences.values() if e.approved is True)
        rejected_count = sum(1 for e in self._experiences.values() if e.approved is False)

        return {
            "total_experiences": len(self._experiences),
            "total_feedbacks": self._total_feedbacks,
            "approved": approved_count,
            "rejected": rejected_count,
            "approval_rate": approved_count / max(1, self._total_feedbacks),
            "pending_batch": self._pending_feedback_count,
            "batch_size": self._batch_size,
            "frozen": self._frozen,
            "exploration_mode": self._exploration,
            "kl_bound": self._safety.kl_bound,
            "min_samples": self._safety.min_samples,
        }

    def get_weight_history(self, limit: int = 50) -> list[dict]:
        """Read weight update history from disk."""
        history: list[dict] = []
        hf = self._history_file()
        if not hf.exists():
            return history
        try:
            with open(hf, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        history.append(json.loads(line))
        except Exception:
            pass
        # Newest first, limited
        history.reverse()
        return history[:limit]

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def freeze(self) -> None:
        """Freeze weights — disable learning, use current means."""
        self._frozen = True
        self._save_state()
        logger.info("rlhf_frozen")

    def unfreeze(self) -> None:
        """Unfreeze — resume learning."""
        self._frozen = False
        self._save_state()
        logger.info("rlhf_unfrozen")

    def reset(self) -> None:
        """Reset posteriors to priors (emergency rollback)."""
        old_weights = self.get_current_weights()
        self._init_posteriors()
        self._total_feedbacks = 0
        self._pending_feedback_count = 0
        self._experiences.clear()
        new_weights = self.get_current_weights()
        self._log_weight_update(old_weights, new_weights, "manual_reset")
        self._save_state()
        logger.warning("rlhf_reset_to_priors")


# =============================================
# Singleton
# =============================================

_optimizer: BayesianWeightOptimizer | None = None


def get_rlhf_optimizer() -> BayesianWeightOptimizer:
    """Get or create the global RLHF optimizer singleton."""
    global _optimizer
    if _optimizer is None:
        _optimizer = BayesianWeightOptimizer()
    return _optimizer
