# services/atlas_supervisor/app_simple.py
"""
Atlas Supervisor API (Simplificada) - Sem ML/LLM.

Endpoints:
- POST /audit/request - Audita requisição HTTP
- POST /audit/telemetry - Registra telemetria
- GET /rules - Lista regras CAOS
- GET /circuit-breakers - Status dos circuit breakers
- GET /health - Health check
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from shared.schemas import (
    AlertPriority,
    AuditResult,
    TelemetryEvent,
)
from services.atlas_supervisor.supervisor_simple import get_supervisor
from services.atlas_supervisor.rules import ATLAS_RULES

app = FastAPI(
    title="CAOS Atlas Supervisor (Simplificado)",
    description="Supervisão leve para o Atlas Agent: Rate Limiting, Validação, Circuit Breaker, Auditoria",
    version="1.0.0",
)


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class RequestAuditRequest(BaseModel):
    """Request para auditoria de requisição HTTP."""
    path: str = Field(..., description="Path do endpoint (ex: /assets)")
    method: str = Field("GET", description="Método HTTP")
    client: str = Field(..., description="Identificador do cliente")
    payload: Optional[Dict[str, Any]] = Field(None, description="Body da requisição")


class TelemetryAuditRequest(BaseModel):
    """Request para registro de telemetria."""
    asset_serial_number: str = Field(..., description="Serial do ativo")
    client: str = Field(..., description="Identificador do cliente")
    temperature_c: Optional[float] = Field(None, description="Temperatura em Celsius")
    avg_power_consumption_watt: Optional[float] = Field(None, description="Consumo médio em Watts")
    total_compressor_on_time_percent: Optional[float] = Field(None, description="% tempo compressor ligado")
    battery_level: Optional[int] = Field(None, description="Nível de bateria (0-100)")
    displacement_meter: Optional[float] = Field(None, description="Deslocamento em metros")
    asset_type: Optional[str] = Field(None, description="Tipo do ativo (freezer, cooler)")
    outlet_code: Optional[str] = Field(None, description="Código do ponto de venda")


class BatchRequestAuditRequest(BaseModel):
    """Request para auditoria em lote."""
    requests: List[RequestAuditRequest]


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
async def health():
    """Health check do serviço."""
    supervisor = get_supervisor()
    return supervisor.get_health_status()


@app.post("/audit/request", response_model=AuditResult)
async def audit_request(request: RequestAuditRequest):
    """Audita uma requisição HTTP.
    
    Verifica:
    1. Rate limiting (por cliente/endpoint)
    2. Validação de payload (se schema definido)
    """
    supervisor = get_supervisor()
    
    return supervisor.audit_request(
        path=request.path,
        method=request.method,
        client=request.client,
        payload=request.payload,
    )


@app.post("/audit/telemetry", response_model=AuditResult)
async def audit_telemetry(request: TelemetryAuditRequest):
    """Registra um evento de telemetria.
    
    Apenas valida campos obrigatórios e aplica rate limiting.
    NÃO faz detecção de anomalias (ML/LLM desativados).
    """
    supervisor = get_supervisor()
    
    event = TelemetryEvent(
        asset_serial_number=request.asset_serial_number,
        client=request.client,
        temperature_c=request.temperature_c,
        avg_power_consumption_watt=request.avg_power_consumption_watt,
        total_compressor_on_time_percent=request.total_compressor_on_time_percent,
        battery_level=request.battery_level,
        displacement_meter=request.displacement_meter,
        asset_type=request.asset_type,
        outlet_code=request.outlet_code,
    )
    
    return supervisor.audit_telemetry(event)


@app.post("/audit/request/batch")
async def audit_request_batch(request: BatchRequestAuditRequest):
    """Audita múltiplas requisições em lote."""
    supervisor = get_supervisor()
    results = []
    
    for req in request.requests:
        result = supervisor.audit_request(
            path=req.path,
            method=req.method,
            client=req.client,
            payload=req.payload,
        )
        results.append({
            "path": req.path,
            "client": req.client,
            "result": result.model_dump(),
        })
    
    total = len(results)
    blocked = sum(1 for r in results if r["result"]["status"] == "BLOCKED")
    
    return {
        "total": total,
        "blocked": blocked,
        "blocked_rate": blocked / total if total > 0 else 0,
        "results": results,
    }


@app.get("/rules")
async def get_rules():
    """Lista todas as regras CAOS configuradas para o Atlas."""
    return [
        {
            "name": r.name,
            "path_prefix": r.path_prefix,
            "methods": r.methods,
            "max_calls": r.max_calls,
            "window_seconds": r.window_seconds,
            "max_latency_ms": r.max_latency_ms,
            "validate_json": r.validate_json,
        }
        for r in ATLAS_RULES
    ]


@app.get("/circuit-breakers")
async def get_circuit_breakers():
    """Status dos circuit breakers."""
    supervisor = get_supervisor()
    return {
        name: cb.get_status().model_dump()
        for name, cb in supervisor.circuit_breakers.items()
    }


@app.post("/circuit-breakers/{service_name}/reset")
async def reset_circuit_breaker(service_name: str):
    """Reseta um circuit breaker para o estado CLOSED."""
    supervisor = get_supervisor()
    
    if service_name not in supervisor.circuit_breakers:
        raise HTTPException(status_code=404, detail=f"Circuit breaker '{service_name}' não encontrado")
    
    cb = supervisor.circuit_breakers[service_name]
    for _ in range(cb.half_open_max_calls + 1):
        cb.record_success()
    
    return {
        "message": f"Circuit breaker '{service_name}' resetado",
        "status": cb.get_status().model_dump(),
    }


@app.get("/rate-limit/{client}")
async def get_rate_limit_status(client: str, path: str = Query("/", description="Path do endpoint")):
    """Verifica tokens restantes de rate limit para um cliente."""
    supervisor = get_supervisor()
    rate_key = f"{client}:{path}"
    remaining = supervisor.rate_limiter.get_remaining(rate_key)
    
    return {
        "client": client,
        "path": path,
        "remaining_tokens": remaining,
        "max_tokens": supervisor.rate_limiter.max_calls,
        "window_seconds": supervisor.rate_limiter.window_seconds,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
