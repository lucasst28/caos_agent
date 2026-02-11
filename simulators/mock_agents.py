"""
Simulador Local dos Agentes Externos
=====================================

Simula Atlas (Digital Twin), Sentinel (Alertas) e Oracle (Predições)
em um único servidor FastAPI para desenvolvimento local do CAOS.

Uso:
    uvicorn simulators.mock_agents:app --port 9000 --reload

Os 3 agentes rodam no mesmo servidor, diferenciados por prefixo:
    - /atlas/v1/...    (Digital Twin)
    - /sentinel/v1/... (Alertas)
    - /oracle/v1/...   (Predições)
"""

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

app = FastAPI(
    title="CAOS - Simuladores Locais",
    description="Mock servers para Atlas, Sentinel e Oracle",
    version="0.1.0-sim",
)

# =============================================
# Base de Dados Simulada (in-memory)
# =============================================

ASSETS = {
    "CHILLER-04": {
        "name": "Chiller Central #04",
        "type": "HVAC",
        "location": "Sala de Máquinas B",
        "status": "operational",
        "temperature": 72.5,
        "pressure": 120.0,
        "vibration": 3.2,
        "max_operating_temp": 90.0,
        "min_operating_temp": -20.0,
        "max_pressure": 150.0,
        "max_vibration": 8.5,
        "contract_id": "CTR-2026-001",
        "contract_tier": "PREMIUM",
        "sla_response_minutes": 30,
        "allowed_actions": ["shutdown", "notification", "ticket", "setpoint", "restart"],
        "under_maintenance": False,
        "last_maintenance": "2026-01-15T10:00:00",
    },
    "PUMP-01": {
        "name": "Bomba Hidráulica #01",
        "type": "HYDRAULIC",
        "location": "Subsolo A",
        "status": "operational",
        "temperature": 45.0,
        "pressure": 85.0,
        "vibration": 2.1,
        "max_operating_temp": 70.0,
        "min_operating_temp": 5.0,
        "max_pressure": 120.0,
        "max_vibration": 6.0,
        "contract_id": "CTR-2026-002",
        "contract_tier": "STANDARD",
        "sla_response_minutes": 60,
        "allowed_actions": ["notification", "ticket"],
        "under_maintenance": False,
        "last_maintenance": "2026-02-01T08:00:00",
    },
    "GENSET-02": {
        "name": "Gerador Diesel #02",
        "type": "POWER",
        "location": "Cobertura",
        "status": "standby",
        "temperature": 35.0,
        "pressure": 0.0,
        "vibration": 0.5,
        "max_operating_temp": 95.0,
        "min_operating_temp": -10.0,
        "max_pressure": 200.0,
        "max_vibration": 10.0,
        "contract_id": "CTR-2026-003",
        "contract_tier": "PREMIUM",
        "sla_response_minutes": 15,
        "allowed_actions": ["shutdown", "notification", "ticket", "setpoint", "restart", "fuel_check"],
        "under_maintenance": False,
        "last_maintenance": "2026-01-28T14:00:00",
    },
}

MANUALS = {
    "CHILLER-04": [
        "Se a temperatura exceder 90°C, desligar imediatamente o compressor.",
        "Verificar nível de fluido refrigerante a cada 500h de operação.",
        "Vibração acima de 8.5 mm/s indica desalinhamento do eixo.",
        "Pressão acima de 150 PSI: acionar válvula de alívio automática.",
    ],
    "PUMP-01": [
        "Temperatura acima de 70°C: reduzir carga e verificar lubrificação.",
        "Pressão diferencial > 35 PSI: verificar filtro obstruído.",
        "Cavitação detectada: reduzir velocidade da bomba.",
    ],
    "GENSET-02": [
        "Temperatura do motor acima de 95°C: desligar imediatamente.",
        "Nível de combustível < 20%: emitir alerta para reabastecimento.",
        "Frequência fora da faixa 59.5-60.5 Hz: verificar governador.",
    ],
}


# =============================================
# ATLAS - Digital Twin Simulator
# =============================================

@app.get("/atlas/v1/tenants/{tenant_id}/assets/{asset_id}/context")
async def atlas_get_context(tenant_id: str, asset_id: str) -> dict[str, Any]:
    """Simula o contexto do Digital Twin (Atlas)."""
    asset = ASSETS.get(asset_id, ASSETS["CHILLER-04"])

    # Adicionar variação realista
    temp_variation = random.uniform(-2.0, 5.0)
    vibration_variation = random.uniform(-0.5, 1.0)

    return {
        "asset_id": asset_id,
        "tenant_id": tenant_id,
        "current_state": {
            "temperature": round(asset["temperature"] + temp_variation, 1),
            "pressure": asset["pressure"],
            "vibration": round(asset["vibration"] + vibration_variation, 2),
            "status": asset["status"],
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "max_operating_temp": asset["max_operating_temp"],
        "min_operating_temp": asset["min_operating_temp"],
        "max_pressure": asset["max_pressure"],
        "max_vibration": asset["max_vibration"],
        "manual_excerpts": MANUALS.get(asset_id, ["Sem manual disponível."]),
        "contract_id": asset["contract_id"],
        "contract_tier": asset["contract_tier"],
        "allowed_actions": asset["allowed_actions"],
        "sla_response_time_minutes": asset["sla_response_minutes"],
        "under_maintenance": asset["under_maintenance"],
        "maintenance_window_active": False,
        "location": asset["location"],
        "asset_type": asset["type"],
        "last_maintenance": asset["last_maintenance"],
    }


@app.get("/atlas/v1/tenants/{tenant_id}/assets/{asset_id}/manuals")
async def atlas_search_manuals(
    tenant_id: str,
    asset_id: str,
    query: str = Query(default=""),
) -> dict[str, Any]:
    """Simula busca em manuais técnicos."""
    all_excerpts = MANUALS.get(asset_id, ["Sem dados."])
    if query:
        results = [e for e in all_excerpts if query.lower() in e.lower()]
    else:
        results = all_excerpts
    return {
        "asset_id": asset_id,
        "query": query,
        "results": results,
        "total": len(results),
    }


@app.get("/atlas/v1/tenants/{tenant_id}/assets/{asset_id}/contracts")
async def atlas_get_contracts(tenant_id: str, asset_id: str) -> dict[str, Any]:
    """Simula dados de contrato."""
    asset = ASSETS.get(asset_id, ASSETS["CHILLER-04"])
    return {
        "contract_id": asset["contract_id"],
        "tier": asset["contract_tier"],
        "sla_response_minutes": asset["sla_response_minutes"],
        "allowed_actions": asset["allowed_actions"],
        "valid_until": "2027-12-31",
        "penalties": {
            "sla_breach_per_hour": 500.0,
            "unauthorized_action": 2000.0,
        },
    }


# =============================================
# SENTINEL - Alert Simulator
# =============================================

class AlertRequest(BaseModel):
    """Request para gerar alerta simulado."""
    asset_id: str = "CHILLER-04"
    metric: str = "temperature"
    severity: str = "MEDIUM"
    value: float | None = None


@app.post("/sentinel/v1/alerts/generate")
async def sentinel_generate_alert(request: AlertRequest) -> dict[str, Any]:
    """Gera um alerta simulado do Sentinel."""
    asset = ASSETS.get(request.asset_id, ASSETS["CHILLER-04"])

    # Auto-generate value based on severity
    if request.value is None:
        max_val = asset.get(f"max_{request.metric}", asset["max_operating_temp"])
        if request.severity == "CRITICAL":
            value = max_val * random.uniform(1.1, 1.3)
        elif request.severity == "HIGH":
            value = max_val * random.uniform(0.95, 1.1)
        elif request.severity == "MEDIUM":
            value = max_val * random.uniform(0.8, 0.95)
        else:
            value = max_val * random.uniform(0.5, 0.8)
    else:
        value = request.value

    alert = {
        "alert_id": f"alert_{uuid.uuid4().hex[:8]}",
        "source": "SENTINEL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": request.severity,
        "asset_id": request.asset_id,
        "tenant_id": "tenant_local",
        "metric": request.metric,
        "value": round(value, 2),
        "threshold_violated": asset.get(f"max_{request.metric}", asset["max_operating_temp"]),
        "message": f"Alerta {request.severity}: {request.metric} = {round(value, 2)} no ativo {request.asset_id}",
        "payload": {
            request.metric: round(value, 2),
            "unit": "celsius" if request.metric == "temperature" else "PSI",
            "asset_location": asset["location"],
        },
    }
    return alert


@app.get("/sentinel/v1/alerts/scenarios")
async def sentinel_list_scenarios() -> dict[str, Any]:
    """Lista cenários de alerta pré-configurados para testes."""
    return {
        "scenarios": [
            {
                "name": "chiller_overtemp",
                "description": "Chiller-04 com sobreaquecimento",
                "asset_id": "CHILLER-04",
                "metric": "temperature",
                "severity": "HIGH",
                "value": 95.0,
            },
            {
                "name": "chiller_critical",
                "description": "Chiller-04 em temperatura crítica",
                "asset_id": "CHILLER-04",
                "metric": "temperature",
                "severity": "CRITICAL",
                "value": 110.0,
            },
            {
                "name": "pump_pressure",
                "description": "Bomba-01 com pressão alta",
                "asset_id": "PUMP-01",
                "metric": "pressure",
                "severity": "MEDIUM",
                "value": 110.0,
            },
            {
                "name": "genset_overtemp",
                "description": "Gerador-02 sobreaquecendo",
                "asset_id": "GENSET-02",
                "metric": "temperature",
                "severity": "HIGH",
                "value": 98.0,
            },
            {
                "name": "chiller_vibration",
                "description": "Chiller-04 vibração excessiva",
                "asset_id": "CHILLER-04",
                "metric": "vibration",
                "severity": "MEDIUM",
                "value": 7.5,
            },
            {
                "name": "chiller_normal",
                "description": "Chiller-04 operação normal (LOW)",
                "asset_id": "CHILLER-04",
                "metric": "temperature",
                "severity": "LOW",
                "value": 65.0,
            },
        ],
    }


@app.post("/sentinel/v1/alerts/scenario/{scenario_name}")
async def sentinel_trigger_scenario(scenario_name: str) -> dict[str, Any]:
    """Dispara um cenário de alerta pré-configurado."""
    scenarios = (await sentinel_list_scenarios())["scenarios"]
    scenario = next((s for s in scenarios if s["name"] == scenario_name), None)
    if not scenario:
        return {"error": f"Cenário '{scenario_name}' não encontrado"}

    return await sentinel_generate_alert(AlertRequest(
        asset_id=scenario["asset_id"],
        metric=scenario["metric"],
        severity=scenario["severity"],
        value=scenario["value"],
    ))


# =============================================
# ORACLE - Prediction Simulator
# =============================================

class PredictionRequest(BaseModel):
    """Request para predição simulada."""
    tenant_id: str = "tenant_local"
    asset_id: str = "CHILLER-04"
    metric: str = "failure_probability"
    horizon_hours: int = 24
    current_value: float | None = None


@app.post("/oracle/v1/predict")
async def oracle_predict(request: PredictionRequest) -> dict[str, Any]:
    """Simula predição do Oracle."""
    asset = ASSETS.get(request.asset_id, ASSETS["CHILLER-04"])

    # Calcular probabilidade baseada na proximity ao limite
    if request.current_value:
        max_val = asset["max_operating_temp"]
        proximity = request.current_value / max_val
        failure_prob = min(0.95, max(0.05, proximity * 0.9))
    else:
        failure_prob = random.uniform(0.3, 0.85)

    confidence = random.uniform(0.7, 0.95)
    financial_impact = round(random.uniform(1000, 15000), 2)

    # Gerar recomendação baseada na probabilidade
    if failure_prob > 0.8:
        recommendation = f"URGENTE: Desligamento preventivo do {request.asset_id} em {request.horizon_hours}h. Risco de falha > 80%."
        recommended_actions = ["shutdown", "notification", "ticket"]
    elif failure_prob > 0.5:
        recommendation = f"Reduzir carga do {request.asset_id} e agendar manutenção em {request.horizon_hours}h."
        recommended_actions = ["setpoint", "notification", "ticket"]
    else:
        recommendation = f"Monitorar {request.asset_id}. Tendência estável nas próximas {request.horizon_hours}h."
        recommended_actions = ["notification"]

    return {
        "prediction_id": f"pred_{uuid.uuid4().hex[:8]}",
        "asset_id": request.asset_id,
        "tenant_id": request.tenant_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "forecast_metric": request.metric,
        "predicted_value": round(failure_prob, 4),
        "confidence": round(confidence, 4),
        "horizon_hours": request.horizon_hours,
        "financial_impact": financial_impact,
        "recommendation": recommendation,
        "recommended_actions": recommended_actions,
        "trend": "increasing" if failure_prob > 0.5 else "stable",
        "model_version": "oracle-v2.1-sim",
    }


@app.post("/oracle/v1/simulate")
async def oracle_simulate(request: PredictionRequest) -> dict[str, Any]:
    """Simula cenários what-if do Oracle."""
    scenarios = []
    for action in ["nenhuma_ação", "reduzir_carga", "manutenção_preventiva", "desligamento"]:
        reduction = {"nenhuma_ação": 0, "reduzir_carga": 0.3, "manutenção_preventiva": 0.6, "desligamento": 0.95}
        base_risk = random.uniform(0.5, 0.9)
        scenarios.append({
            "action": action,
            "residual_risk": round(base_risk * (1 - reduction[action]), 4),
            "cost": round(random.uniform(100, 5000) * (1 + reduction[action]), 2),
            "downtime_hours": round(random.uniform(0, 8) * reduction[action], 1),
        })

    return {
        "asset_id": request.asset_id,
        "scenarios": scenarios,
        "recommended": "manutenção_preventiva",
    }


# =============================================
# Root & Health
# =============================================

@app.get("/")
async def root() -> dict[str, Any]:
    """Root do simulador."""
    return {
        "service": "CAOS Simuladores Locais",
        "agents": {
            "atlas": "/atlas/v1/...",
            "sentinel": "/sentinel/v1/...",
            "oracle": "/oracle/v1/...",
        },
        "assets": list(ASSETS.keys()),
        "docs": "/docs",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "mode": "simulator"}
