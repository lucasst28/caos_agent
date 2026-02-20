# services/atlas_supervisor/app.py
"""
Atlas Supervisor API - Serviço FastAPI para supervisão do Atlas.

Expõe endpoints para:
- Auditoria de telemetria
- Consulta de status e métricas
- Gerenciamento de circuit breakers
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from shared.schemas import (
    AlertPriority,
    AnomalyType,
    AuditResult,
    TelemetryEvent,
)
from services.atlas_supervisor.supervisor import get_supervisor
from services.atlas_supervisor.rules import ATLAS_RULES, TelemetryThresholds

app = FastAPI(
    title="CAOS Atlas Supervisor",
    description="Serviço de supervisão inteligente para o Atlas Agent",
    version="1.0.0",
)


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class TelemetryAuditRequest(BaseModel):
    """Request para auditoria de telemetria."""
    asset_serial_number: str = Field(..., description="Serial do ativo")
    client: str = Field(..., description="Identificador do cliente")
    temperature_c: Optional[float] = Field(None, description="Temperatura em Celsius")
    avg_power_consumption_watt: Optional[float] = Field(None, description="Consumo médio em Watts")
    total_compressor_on_time_percent: Optional[float] = Field(None, description="% tempo compressor ligado")
    battery_level: Optional[int] = Field(None, description="Nível de bateria (0-100)")
    displacement_meter: Optional[float] = Field(None, description="Deslocamento em metros")
    asset_type: Optional[str] = Field(None, description="Tipo do ativo (freezer, cooler)")
    outlet_code: Optional[str] = Field(None, description="Código do ponto de venda")


class BatchAuditRequest(BaseModel):
    """Request para auditoria em lote."""
    events: List[TelemetryAuditRequest]


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
async def health():
    """Health check do serviço."""
    supervisor = get_supervisor()
    return supervisor.get_health_status()


@app.post("/audit", response_model=AuditResult)
async def audit_telemetry(request: TelemetryAuditRequest):
    """Audita um evento de telemetria.
    
    Executa o pipeline CAOS completo:
    1. Detecção de anomalias (heurísticas ou modelo ML)
    2. Se anômalo, consulta o juiz cognitivo (LLM)
    3. Retorna decisão consolidada
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


@app.post("/audit/batch")
async def audit_telemetry_batch(request: BatchAuditRequest):
    """Audita múltiplos eventos de telemetria em lote."""
    supervisor = get_supervisor()
    results = []
    
    for req in request.events:
        event = TelemetryEvent(
            asset_serial_number=req.asset_serial_number,
            client=req.client,
            temperature_c=req.temperature_c,
            avg_power_consumption_watt=req.avg_power_consumption_watt,
            total_compressor_on_time_percent=req.total_compressor_on_time_percent,
            battery_level=req.battery_level,
            displacement_meter=req.displacement_meter,
            asset_type=req.asset_type,
            outlet_code=req.outlet_code,
        )
        result = supervisor.audit_telemetry(event)
        results.append({
            "asset_serial_number": req.asset_serial_number,
            "result": result.model_dump(),
        })
    
    total = len(results)
    anomalies = sum(1 for r in results if r["result"]["status"] in ["ANOMALY", "BLOCKED"])
    
    return {
        "total": total,
        "anomalies": anomalies,
        "anomaly_rate": anomalies / total if total > 0 else 0,
        "results": results,
    }


@app.get("/thresholds")
async def get_thresholds(
    asset_type: Optional[str] = Query(None, description="Tipo do ativo")
):
    """Retorna os thresholds de detecção de anomalias."""
    thresholds = TelemetryThresholds()
    temp_min, temp_max = thresholds.get_temp_range_for_asset(asset_type)
    
    return {
        "asset_type": asset_type or "cooler (default)",
        "temperature": {
            "normal_range": {"min": temp_min, "max": temp_max},
            "critical_high": thresholds.TEMP_CRITICAL_HIGH,
            "critical_low": thresholds.TEMP_CRITICAL_LOW,
        },
        "energy": {
            "voltage_min": thresholds.VOLTAGE_MIN,
            "voltage_max": thresholds.VOLTAGE_MAX,
            "power_max_watt": thresholds.POWER_MAX_WATT,
        },
        "compressor": {
            "max_percent": thresholds.COMPRESSOR_ON_MAX_PERCENT,
            "min_percent": thresholds.COMPRESSOR_ON_MIN_PERCENT,
        },
        "battery": {
            "critical": thresholds.BATTERY_CRITICAL,
            "low": thresholds.BATTERY_LOW,
        },
        "gps": {
            "suspicious_displacement_meters": thresholds.DISPLACEMENT_SUSPICIOUS_METERS,
            "critical_displacement_meters": thresholds.DISPLACEMENT_CRITICAL_METERS,
        },
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


@app.post("/simulate/anomaly")
async def simulate_anomaly(
    anomaly_type: AnomalyType = Query(..., description="Tipo de anomalia a simular")
):
    """Simula uma anomalia específica para testes."""
    supervisor = get_supervisor()
    
    event_params = {
        AnomalyType.TEMPERATURE_HIGH: {"temperature_c": 25.0, "asset_type": "cooler"},
        AnomalyType.TEMPERATURE_LOW: {"temperature_c": -35.0, "asset_type": "freezer"},
        AnomalyType.BATTERY_CRITICAL: {"battery_level": 5},
        AnomalyType.GPS_DISPLACEMENT: {"displacement_meter": 600.0},
        AnomalyType.COMPRESSOR_FAULT: {"total_compressor_on_time_percent": 99.0},
    }
    
    params = event_params.get(anomaly_type, {"temperature_c": 50.0})
    
    event = TelemetryEvent(
        asset_serial_number="TEST-001",
        client="test",
        **params,
    )
    
    result = supervisor.audit_telemetry(event)
    
    return {
        "simulated_anomaly": anomaly_type.value,
        "event": event.model_dump(),
        "result": result.model_dump(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
