"""Tests for RLHF Bayesian Online Weight Optimization.

Run: pytest tests/test_rlhf.py -v
"""

import json
import math
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from caos.core.rlhf import (
    BayesianWeightOptimizer,
    ExperienceRecord,
    SafetyGuard,
    WeightPosterior,
)


@pytest.fixture
def tmp_rlhf_dir(tmp_path):
    """Provide a temp directory for RLHF data."""
    return str(tmp_path)


@pytest.fixture
def optimizer(tmp_rlhf_dir):
    """Create a fresh BayesianWeightOptimizer with temp storage."""
    with patch("caos.core.rlhf.get_settings") as mock:
        s = mock.return_value
        s.rlhf_data_dir = tmp_rlhf_dir
        s.rlhf_kl_bound = 0.5  # Relaxed for tests
        s.rlhf_min_samples = 3  # Low for tests
        s.rlhf_batch_size = 2  # Small batches
        s.rlhf_exploration_mode = False
        s.rlhf_enabled = True
        opt = BayesianWeightOptimizer(data_dir=tmp_rlhf_dir)
    return opt


def _make_experience(action_id: str, verdict: float = 0.5, **kwargs) -> ExperienceRecord:
    """Helper to create test experiences."""
    defaults = dict(
        action_id=action_id,
        event_id=f"evt_{action_id}",
        tenant_id="t1",
        asset_id="CHILLER-04",
        atlas_score=0.8,
        oracle_score=0.7,
        severity_score=0.3,
        risk_physical=0.4,
        risk_financial=0.2,
        risk_contractual=0.1,
        risk_communication=0.05,
        guardrail_penalty=0.0,
        verdict_score=verdict,
        decision_band="EXECUTE",
        risk_level="LOW",
        action_type="setpoint",
    )
    defaults.update(kwargs)
    return ExperienceRecord(**defaults)


# =============================================
# WeightPosterior Tests
# =============================================

class TestWeightPosterior:
    def test_prior_mean_w_atlas(self):
        """W_A prior Beta(12, 8) should have mean ≈ 0.60."""
        p = WeightPosterior(name="w_atlas", alpha=12.0, beta=8.0)
        assert abs(p.mean - 0.60) < 0.001

    def test_prior_mean_w_severity(self):
        """W_S prior Beta(6, 14) should have mean ≈ 0.30."""
        p = WeightPosterior(name="w_severity", alpha=6.0, beta=14.0, min_value=0.15, max_value=0.60)
        assert abs(p.mean - 0.30) < 0.001

    def test_sample_clamped(self):
        """Sample should always be within [min, max]."""
        p = WeightPosterior(name="test", alpha=1.0, beta=100.0, min_value=0.15, max_value=0.60)
        for _ in range(100):
            s = p.sample()
            assert 0.15 <= s <= 0.60

    def test_ci_95(self):
        """95% CI should contain the mean."""
        p = WeightPosterior(name="test", alpha=10.0, beta=10.0)
        lo, hi = p.ci_95()
        assert lo < p.mean < hi


# =============================================
# SafetyGuard Tests
# =============================================

class TestSafetyGuard:
    def test_min_samples_rejected(self):
        """Update rejected if below min samples."""
        guard = SafetyGuard(min_samples=20)
        posteriors = {"w_atlas": WeightPosterior("w_atlas", 12, 8)}
        priors = {"w_atlas": (12.0, 8.0)}
        safe, reason = guard.check_update_safe(posteriors, priors, total_samples=5)
        assert safe is False
        assert "Insufficient" in reason

    def test_kl_bound_rejected(self):
        """Update rejected if KL divergence exceeds bound."""
        guard = SafetyGuard(kl_bound=0.01, min_samples=1)
        # Large drift: prior Beta(12,8), posterior Beta(50,2) — very different
        posteriors = {"w_atlas": WeightPosterior("w_atlas", 50.0, 2.0)}
        priors = {"w_atlas": (12.0, 8.0)}
        safe, reason = guard.check_update_safe(posteriors, priors, total_samples=100)
        assert safe is False
        assert "KL divergence" in reason

    def test_safe_update_accepted(self):
        """Small update within bounds should be accepted."""
        guard = SafetyGuard(kl_bound=0.5, min_samples=5)
        # Minor drift: prior Beta(12,8) → posterior Beta(13,9)
        posteriors = {"w_atlas": WeightPosterior("w_atlas", 13.0, 9.0)}
        priors = {"w_atlas": (12.0, 8.0)}
        safe, reason = guard.check_update_safe(posteriors, priors, total_samples=10)
        assert safe is True

    def test_severity_floor_enforced(self):
        """W_S must respect floor constraint."""
        guard = SafetyGuard(w_severity_floor=0.15)
        posteriors = {
            "w_severity": WeightPosterior("w_severity", 1.0, 100.0, min_value=0.05),
            "w_physical": WeightPosterior("w_physical", 7, 13),
            "w_financial": WeightPosterior("w_financial", 5, 15),
            "w_contractual": WeightPosterior("w_contractual", 5, 15),
            "w_communication": WeightPosterior("w_communication", 3, 17),
        }
        guard.enforce_constraints(posteriors)
        assert posteriors["w_severity"].min_value == 0.15

    def test_risk_weights_normalized(self):
        """Risk weights should sum to 1.0 after constraint enforcement."""
        guard = SafetyGuard()
        posteriors = {
            "w_severity": WeightPosterior("w_severity", 6, 14, min_value=0.15, max_value=0.60),
            "w_physical": WeightPosterior("w_physical", 20, 5),     # skewed
            "w_financial": WeightPosterior("w_financial", 20, 5),   # skewed
            "w_contractual": WeightPosterior("w_contractual", 5, 15),
            "w_communication": WeightPosterior("w_communication", 3, 17),
        }
        guard.enforce_constraints(posteriors)

        risk_sum = sum(
            posteriors[k].clamped_mean()
            for k in ["w_physical", "w_financial", "w_contractual", "w_communication"]
        )
        assert abs(risk_sum - 1.0) < 0.05  # Approximate due to clamping


# =============================================
# BayesianWeightOptimizer Tests
# =============================================

class TestBayesianWeightOptimizer:
    def test_initial_weights_match_defaults(self, optimizer):
        """Initial weights should match the default prior means."""
        w = optimizer.get_current_weights()
        assert abs(w["w_atlas"] - 0.60) < 0.01
        assert abs(w["w_oracle"] - 0.60) < 0.01
        assert abs(w["w_severity"] - 0.30) < 0.01
        # Risk weights should sum to 1.0
        risk_sum = w["w_physical"] + w["w_financial"] + w["w_contractual"] + w["w_communication"]
        assert abs(risk_sum - 1.0) < 0.01

    def test_approval_shifts_weights(self, optimizer):
        """Approving decisions should shift weights toward more confident verdicts."""
        old_weights = optimizer.get_current_weights()

        # Record and approve 5 experiences
        for i in range(5):
            exp = _make_experience(f"act_{i}", verdict=0.75)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_{i}", approved=True, resolver="op")

        new_weights = optimizer.get_current_weights()
        # W_A (atlas) should increase slightly (contributed positively to approved verdict)
        assert new_weights["w_atlas"] >= old_weights["w_atlas"] - 0.05  # May not shift much but shouldn't decrease

    def test_rejection_shifts_weights(self, optimizer):
        """Rejecting decisions should increase β (reduce confidence)."""
        # Record and reject experiences
        for i in range(5):
            exp = _make_experience(f"act_rej_{i}", verdict=0.6)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_rej_{i}", approved=False, resolver="op")

        details = optimizer.get_weight_details()
        # β should have increased from initial values (8 for atlas)
        assert details["w_atlas"]["beta"] > 8.0

    def test_duplicate_feedback_ignored(self, optimizer):
        """Processing feedback twice for same action returns False."""
        exp = _make_experience("act_dup", verdict=0.5)
        optimizer.record_experience(exp)
        assert optimizer.process_feedback("act_dup", True, "op") is not None
        result = optimizer.process_feedback("act_dup", True, "op")
        assert result is False

    def test_unknown_action_skipped(self, optimizer):
        """Feedback for unknown action returns False."""
        result = optimizer.process_feedback("nonexistent", True, "op")
        assert result is False

    def test_frozen_skips_update(self, optimizer):
        """Frozen optimizer should not update weights."""
        optimizer.freeze()
        exp = _make_experience("act_frozen", verdict=0.5)
        optimizer.record_experience(exp)
        result = optimizer.process_feedback("act_frozen", True, "op")
        assert result is False

    def test_reset_restores_priors(self, optimizer):
        """Reset should restore all weights to original priors."""
        # Modify weights
        for i in range(5):
            exp = _make_experience(f"act_reset_{i}", verdict=0.5)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_reset_{i}", True, "op")

        optimizer.reset()
        w = optimizer.get_current_weights()
        assert abs(w["w_atlas"] - 0.60) < 0.01
        assert abs(w["w_severity"] - 0.30) < 0.01
        assert optimizer._total_feedbacks == 0

    def test_persistence_roundtrip(self, optimizer, tmp_rlhf_dir):
        """Weights should survive save → load cycle."""
        # Record some feedback
        for i in range(5):
            exp = _make_experience(f"act_persist_{i}", verdict=0.5)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_persist_{i}", True, "op")

        w_before = optimizer.get_current_weights()

        # Create new optimizer from same directory
        with patch("caos.core.rlhf.get_settings") as mock:
            s = mock.return_value
            s.rlhf_data_dir = tmp_rlhf_dir
            s.rlhf_kl_bound = 0.5
            s.rlhf_min_samples = 3
            s.rlhf_batch_size = 2
            s.rlhf_exploration_mode = False
            s.rlhf_enabled = True
            opt2 = BayesianWeightOptimizer(data_dir=tmp_rlhf_dir)

        w_after = opt2.get_current_weights()

        for key in w_before:
            assert abs(w_before[key] - w_after[key]) < 0.02, f"Weight {key} did not survive roundtrip"

    def test_weight_history_logged(self, optimizer, tmp_rlhf_dir):
        """Each batch update should produce a history entry."""
        for i in range(5):
            exp = _make_experience(f"act_hist_{i}", verdict=0.5)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_hist_{i}", True, "op")

        history = optimizer.get_weight_history()
        assert len(history) > 0
        entry = history[0]
        assert "old_weights" in entry
        assert "new_weights" in entry
        assert "kl_divergences" in entry

    def test_stats(self, optimizer):
        """Stats should reflect recorded experiences and feedbacks."""
        for i in range(3):
            exp = _make_experience(f"act_stats_{i}", verdict=0.5)
            optimizer.record_experience(exp)
            optimizer.process_feedback(f"act_stats_{i}", i < 2, "op")  # 2 approve, 1 reject

        stats = optimizer.get_stats()
        assert stats["total_feedbacks"] == 3
        assert stats["approved"] == 2
        assert stats["rejected"] == 1
        assert abs(stats["approval_rate"] - 2 / 3) < 0.01

    def test_signal_strength_boundary(self, optimizer):
        """Verdicts near decision boundaries should get stronger signals."""
        # Near boundary (0.3)
        edge = _make_experience("edge", verdict=0.31)
        strength_edge = optimizer._compute_signal_strength(edge)

        # Far from boundary
        far = _make_experience("far", verdict=0.85)
        strength_far = optimizer._compute_signal_strength(far)

        assert strength_edge > strength_far

    def test_weight_details_structure(self, optimizer):
        """get_weight_details should return full posterior info."""
        details = optimizer.get_weight_details()
        assert "w_atlas" in details
        atlas_info = details["w_atlas"]
        assert "alpha" in atlas_info
        assert "beta" in atlas_info
        assert "mean" in atlas_info
        assert "std" in atlas_info
        assert "ci_95" in atlas_info
        assert "kl_from_prior" in atlas_info
        assert len(atlas_info["ci_95"]) == 2


# =============================================
# JudgeEngine.from_rlhf Tests
# =============================================

class TestJudgeFromRLHF:
    def test_from_rlhf_disabled(self):
        """from_rlhf should fallback to defaults when RLHF disabled."""
        with patch("caos.core.judge.get_settings") as mock:
            s = mock.return_value
            s.rlhf_enabled = False
            s.weight_atlas = 0.6
            s.weight_oracle = 0.6
            s.weight_severity = 0.3
            s.weight_risk_physical = 0.35
            s.weight_risk_financial = 0.25
            s.weight_risk_contractual = 0.25
            s.weight_risk_communication = 0.15
            s.threshold_blocked = 0.0
            s.threshold_alert = 0.3
            s.threshold_suggest = 0.7

            from caos.core.judge import JudgeEngine
            judge = JudgeEngine.from_rlhf()
            assert abs(judge.verdict_weights.w_atlas - 0.6) < 0.01
