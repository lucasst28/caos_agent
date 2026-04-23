"""Audit API — WORM-backed verdict log queries and integrity checks.

Exposes the immutable verdict store for compliance and debugging.
"""

import structlog
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from caos.observability.worm import get_worm_storage

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


# === Response Models ===


class VerdictRecord(BaseModel):
    """A single verdict log record."""
    action_id: str | None = None
    event_id: str | None = None
    tenant_id: str | None = None
    asset_id: str | None = None
    verdict_score: float | None = None
    decision_band: str | None = None
    risk_level: str | None = None
    action_type: str | None = None
    justification: str | None = None
    guardrails_passed: list[str] = Field(default_factory=list)
    guardrails_violated: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    _timestamp: str | None = None
    _hash: str | None = None


class VerdictsResponse(BaseModel):
    """Paginated list of verdict records."""
    verdicts: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class VerifyResponse(BaseModel):
    """Result of hash chain integrity verification."""
    status: str  # "ok" or "tampered"
    total_records_verified: int
    error_at_line: int | None = None
    message: str


class AuditStatsResponse(BaseModel):
    """Storage statistics."""
    total_records: int
    total_files: int
    total_bytes: int
    oldest_date: str | None = None
    newest_date: str | None = None
    storage_dir: str


# === Endpoints ===


@router.get(
    "/verdicts",
    response_model=VerdictsResponse,
    summary="List verdict logs",
    description="Query the WORM verdict store with optional filters.",
)
async def list_verdicts(
    tenant_id: str | None = Query(default=None, description="Filter by tenant"),
    asset_id: str | None = Query(default=None, description="Filter by asset"),
    date_from: str | None = Query(default=None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(default=50, ge=1, le=500, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Offset"),
) -> VerdictsResponse:
    """List verdict records from the immutable WORM store."""
    storage = get_worm_storage()
    records, total = storage.read_all(
        tenant_id=tenant_id,
        asset_id=asset_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return VerdictsResponse(
        verdicts=records,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/verify",
    response_model=VerifyResponse,
    summary="Verify hash chain integrity",
    description="Walk the entire WORM chain and verify each record's SHA-256 hash.",
)
async def verify_integrity() -> VerifyResponse:
    """Verify the integrity of the WORM hash chain."""
    storage = get_worm_storage()
    ok, error_line = storage.verify()

    # Count total records for the response
    stats = storage.stats()

    if ok:
        return VerifyResponse(
            status="ok",
            total_records_verified=stats["total_records"],
            message="Hash chain integrity verified — no tampering detected.",
        )
    else:
        return VerifyResponse(
            status="tampered",
            total_records_verified=stats["total_records"],
            error_at_line=error_line,
            message=f"Hash chain broken at record {error_line}. Data may have been tampered.",
        )


@router.get(
    "/stats",
    response_model=AuditStatsResponse,
    summary="Get audit storage statistics",
)
async def audit_stats() -> AuditStatsResponse:
    """Get statistics about the WORM storage."""
    storage = get_worm_storage()
    s = storage.stats()
    return AuditStatsResponse(**s)
