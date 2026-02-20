"""RLHF Monitoring & Control API.

Endpoints for monitoring weight evolution, viewing statistics,
and emergency controls (freeze/reset).
"""

from fastapi import APIRouter, Query

from caos.config import get_settings

router = APIRouter(prefix="/rlhf", tags=["rlhf"])


@router.get("/weights")
async def get_weights() -> dict:
    """Current posterior distributions for all 7 weights.
    
    Returns α, β, mean, std, 95% CI, and KL divergence from prior.
    """
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False, "message": "RLHF desabilitado"}

    from caos.core.rlhf import get_rlhf_optimizer
    optimizer = get_rlhf_optimizer()
    return {
        "enabled": True,
        "exploration_mode": settings.rlhf_exploration_mode,
        "weights": optimizer.get_weight_details(),
        "point_estimates": optimizer.get_current_weights(),
    }


@router.get("/history")
async def get_weight_history(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Weight evolution timeline for dashboard visualization."""
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False, "history": []}

    from caos.core.rlhf import get_rlhf_optimizer
    history = get_rlhf_optimizer().get_weight_history(limit=limit)
    return {"enabled": True, "history": history, "total": len(history)}


@router.get("/stats")
async def get_stats() -> dict:
    """RLHF optimizer statistics: feedbacks, approval rate, batch info."""
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False}

    from caos.core.rlhf import get_rlhf_optimizer
    stats = get_rlhf_optimizer().get_stats()
    return {"enabled": True, **stats}


@router.post("/reset")
async def reset_weights() -> dict:
    """Emergency rollback: reset all posteriors to priors."""
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False, "message": "RLHF desabilitado"}

    from caos.core.rlhf import get_rlhf_optimizer
    optimizer = get_rlhf_optimizer()
    optimizer.reset()
    return {
        "status": "reset",
        "weights": optimizer.get_current_weights(),
        "message": "Posteriors resetados para priors originais",
    }


@router.post("/freeze")
async def freeze_weights() -> dict:
    """Freeze weights — disable learning, use current means."""
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False}

    from caos.core.rlhf import get_rlhf_optimizer
    optimizer = get_rlhf_optimizer()
    optimizer.freeze()
    return {
        "status": "frozen",
        "weights": optimizer.get_current_weights(),
        "message": "Aprendizado congelado. Pesos fixos nos valores atuais.",
    }


@router.post("/unfreeze")
async def unfreeze_weights() -> dict:
    """Unfreeze — resume Bayesian learning from feedback."""
    settings = get_settings()
    if not settings.rlhf_enabled:
        return {"enabled": False}

    from caos.core.rlhf import get_rlhf_optimizer
    optimizer = get_rlhf_optimizer()
    optimizer.unfreeze()
    return {
        "status": "active",
        "message": "Aprendizado retomado. Posteriors serão atualizados com novos feedbacks.",
    }
