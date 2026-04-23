# services/atlas_supervisor/app_simple.py
"""
Atlas Supervisor API (Simplificada) - Sem ML/LLM.

Endpoints:
- POST /audit/request - Audita requisição HTTP
- POST /audit/telemetry - Registra telemetria
- GET /rules - Lista regras CAOS
- GET /circuit-breakers - Status dos circuit breakers
- GET /health - Health check
- GET /stats - Statistics for dashboard
- GET /atlas-stats - Proxies stats from Atlas Agent
"""

import os
import httpx
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import redis # Added for Redis support

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.schemas import (
    AlertPriority,
    AuditResult,
    TelemetryEvent,
)
from services.atlas_supervisor.supervisor_simple import get_supervisor
from services.atlas_supervisor.rules import ATLAS_RULES

# Atlas Agent configuration
ATLAS_AGENT_URL = os.getenv("ATLAS_AGENT_URL", "http://atlas-agent:8000")
ATLAS_API_KEY = os.getenv("ATLAS_API_KEY", "")

# Redis Configuration
REDIS_URL = os.environ.get("REDIS_URL", "redis://shared-redis:6379/0")
redis_client: Optional[redis.Redis] = None

def get_redis():
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            print(f"Failed to connect to Redis: {e}")
    return redis_client

app = FastAPI(
    title="CAOS Atlas Supervisor (Simplificado)",
    description="Supervisão leve para o Atlas Agent: Rate Limiting, Validação, Circuit Breaker, Auditoria",
    version="1.0.0",
)

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    supervisor = get_supervisor(get_redis())
    return supervisor.get_health_status()


@app.get("/stats")
async def get_stats():
    """Retorna estatísticas do supervisor para o dashboard."""
    supervisor = get_supervisor(get_redis())
    return supervisor.get_stats()


@app.get("/atlas-stats")
async def get_atlas_stats(client: str = Query("viva", description="Client identifier")):
    """Busca estatísticas diretamente do Atlas Agent.
    
    Faz proxy para o endpoint /dashboard/stats do Atlas.
    """
    if not ATLAS_API_KEY:
        raise HTTPException(status_code=503, detail="Atlas API Key não configurada")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(
                f"{ATLAS_AGENT_URL}/dashboard/stats",
                params={"client": client},
                headers={"X-API-Key": ATLAS_API_KEY},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Atlas error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Atlas indisponível: {str(e)}")


@app.get("/alerts")
async def get_alerts(limit: int = 50):
    """Busca os últimos alertas/logs do sistema via Redis Stream."""
    r = get_redis()
    if not r:
        return []

    try:
        # Read from 'caos.alerts' stream (reverse order for reliability)
        # xrevrange returns list of (id, fields)
        entries = r.xrevrange("caos.alerts", max="+", min="-", count=limit)
        
        logs = []
        for msg_id, fields in entries:
            # Fields are already decoded if decode_responses=True
            entry = fields.copy()
            entry["id"] = msg_id
            logs.append(entry)
            
        return logs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# List of known clients (can be configured via environment variable)
KNOWN_CLIENTS = os.getenv("ATLAS_CLIENTS", "Embonor,Coel,viva,ambev,heineken").split(",")


@app.get("/clients")
async def get_clients():
    """Lista os clientes disponíveis para consulta no Atlas."""
    return {
        "clients": [c.strip() for c in KNOWN_CLIENTS if c.strip()],
        "default": KNOWN_CLIENTS[0] if KNOWN_CLIENTS else "viva"
    }


@app.post("/audit/request", response_model=AuditResult)
async def audit_request(request: RequestAuditRequest):
    """Audita uma requisição HTTP.
    
    Verifica:
    1. Rate limiting (por cliente/endpoint)
    2. Validação de payload (se schema definido)
    """
    supervisor = get_supervisor(get_redis())
    
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
    supervisor = get_supervisor(get_redis())
    
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
    supervisor = get_supervisor(get_redis())
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
    supervisor = get_supervisor(get_redis())
    return {
        name: cb.get_status().model_dump()
        for name, cb in supervisor.circuit_breakers.items()
    }


@app.post("/circuit-breakers/{service_name}/reset")
async def reset_circuit_breaker(service_name: str):
    """Reseta um circuit breaker para o estado CLOSED."""
    supervisor = get_supervisor(get_redis())
    
    if service_name not in supervisor.circuit_breakers:
        raise HTTPException(status_code=404, detail=f"Circuit breaker '{service_name}' não encontrado")
    
    cb = supervisor.circuit_breakers[service_name]
    for _ in range(cb.half_open_max_calls + 1):
        cb.record_success()
    
    return {
        "message": f"Circuit breaker '{service_name}' resetado",
        "status": cb.get_status().model_dump(),
    }


@app.get("/redis/status")
async def get_redis_status():
    """Retorna status completo do Redis para página de monitoramento da Fila.
    
    Inclui:
    - Status de conexão e memória
    - Estatísticas de comandos
    - Resumo de streams e consumers
    - Alertas de engarrafamento (lag alto, consumers inativos)
    """
    r = get_redis()
    if not r:
        return {
            "status": "offline",
            "error": "Redis não disponível",
            "memory": {},
            "stats": {},
            "streams_count": 0,
            "total_consumers": 0,
            "total_pending": 0,
            "bottlenecks": []
        }
    
    try:
        # 1. Redis INFO
        info = r.info()
        memory_info = r.info("memory")
        
        # 2. Streams to monitor
        stream_names = [
            "telemetry.received",
            "telemetry.validated",
            "telemetry.rejected",
            "atlas.daily_routine",
            "caos.alerts",
            "sentinel.consumer_health",
            "oracle.predictions",
            "care.actions",
        ]
        
        streams_data = []
        total_consumers = 0
        total_pending = 0
        bottlenecks = []
        
        for stream_name in stream_names:
            try:
                stream_info = r.xinfo_stream(stream_name)
                stream_length = stream_info.get("length", 0)
                
                # Get consumer groups
                groups = []
                try:
                    group_info = r.xinfo_groups(stream_name)
                    for group in group_info:
                        group_name = group.get("name", "")
                        pending = group.get("pending", 0)
                        consumers_count = group.get("consumers", 0)
                        lag = group.get("lag", 0) or 0
                        entries_read = group.get("entries-read") or 0
                        
                        total_consumers += consumers_count
                        total_pending += pending
                        
                        # Get individual consumers
                        consumers = []
                        try:
                            consumer_info = r.xinfo_consumers(stream_name, group_name)
                            for consumer in consumer_info:
                                idle_ms = consumer.get("idle", 0)
                                consumer_pending = consumer.get("pending", 0)
                                consumer_name = consumer.get("name", "")
                                
                                consumers.append({
                                    "name": consumer_name,
                                    "pending": consumer_pending,
                                    "idle_ms": idle_ms
                                })
                                
                                # Check for inactive consumer (idle > 5 min)
                                if idle_ms > 300000:
                                    bottlenecks.append({
                                        "type": "inactive_consumer",
                                        "stream": stream_name,
                                        "group": group_name,
                                        "consumer": consumer_name,
                                        "idle_minutes": round(idle_ms / 60000, 1),
                                        "severity": "warning" if idle_ms < 600000 else "critical"
                                    })
                        except Exception:
                            pass
                        
                        groups.append({
                            "name": group_name,
                            "pending": pending,
                            "consumers_count": consumers_count,
                            "lag": lag,
                            "entries_read": entries_read,
                            "consumers": consumers
                        })
                        
                        # Check for lag bottleneck
                        if lag > 100:
                            bottlenecks.append({
                                "type": "lag",
                                "stream": stream_name,
                                "group": group_name,
                                "lag": lag,
                                "severity": "warning" if lag < 1000 else "critical"
                            })
                        
                        # Check for high pending
                        if pending > 50:
                            bottlenecks.append({
                                "type": "pending",
                                "stream": stream_name,
                                "group": group_name,
                                "pending": pending,
                                "severity": "warning" if pending < 200 else "critical"
                            })
                            
                except Exception:
                    groups = []
                
                streams_data.append({
                    "name": stream_name,
                    "length": stream_length,
                    "groups": groups,
                    "active": stream_length > 0
                })
                
            except Exception:
                streams_data.append({
                    "name": stream_name,
                    "length": 0,
                    "groups": [],
                    "active": False
                })
        
        return {
            "status": "online",
            "memory": {
                "used_mb": round(memory_info.get("used_memory", 0) / 1024 / 1024, 2),
                "peak_mb": round(memory_info.get("used_memory_peak", 0) / 1024 / 1024, 2),
                "rss_mb": round(memory_info.get("used_memory_rss", 0) / 1024 / 1024, 2),
            },
            "stats": {
                "total_commands": info.get("total_commands_processed", 0),
                "commands_per_sec": info.get("instantaneous_ops_per_sec", 0),
                "connected_clients": info.get("connected_clients", 0),
                "uptime_days": round(info.get("uptime_in_seconds", 0) / 86400, 1),
            },
            "streams_count": len([s for s in streams_data if s["active"]]),
            "streams": streams_data,
            "total_consumers": total_consumers,
            "total_pending": total_pending,
            "bottlenecks": bottlenecks
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "memory": {},
            "stats": {},
            "streams_count": 0,
            "total_consumers": 0,
            "total_pending": 0,
            "bottlenecks": []
        }


@app.get("/streams")
async def get_streams():
    """Retorna informações dos Redis Streams para o dashboard.
    
    Usa XINFO STREAM para obter estatísticas de cada stream.
    """
    r = get_redis()
    if not r:
        return {"streams": [], "error": "Redis não disponível"}

    # Streams to monitor
    stream_names = [
        "telemetry.received",
        "telemetry.validated",
        "telemetry.rejected",
        "atlas.daily_routine",
        "caos.alerts",
        "sentinel.consumer_health",
        "oracle.predictions",
        "care.actions",
    ]

    streams_info = []
    
    for stream_name in stream_names:
        try:
            # Get stream info - returns dict with stream details
            info = r.xinfo_stream(stream_name)
            
            # Get consumer groups
            groups = []
            try:
                groups_raw = r.xinfo_groups(stream_name)
                groups = [
                    {
                        "name": g.get("name", ""),
                        "consumers": g.get("consumers", 0),
                        "pending": g.get("pending", 0),
                    }
                    for g in groups_raw
                ]
            except Exception:
                pass  # No groups is fine
            
            # Calculate total consumers across all groups
            total_consumers = sum(g.get("consumers", 0) for g in groups)
            
            streams_info.append({
                "name": stream_name,
                "length": info.get("length", 0),
                "first_entry_id": info.get("first-entry", [None])[0] if info.get("first-entry") else None,
                "last_entry_id": info.get("last-entry", [None])[0] if info.get("last-entry") else None,
                "groups_count": len(groups),
                "total_consumers": total_consumers,
                "groups": groups,
            })
        except redis.ResponseError:
            # Stream doesn't exist yet
            streams_info.append({
                "name": stream_name,
                "length": 0,
                "first_entry_id": None,
                "last_entry_id": None,
                "groups_count": 0,
                "total_consumers": 0,
                "groups": [],
            })
        except Exception as e:
            streams_info.append({
                "name": stream_name,
                "length": 0,
                "error": str(e),
            })

    return {"streams": streams_info}


@app.get("/streams/{stream_name}/details")
async def get_stream_details(
    stream_name: str,
    limit: int = Query(50, description="Número de mensagens recentes", ge=1),
    start_ts: Optional[int] = Query(None, description="Timestamp inicial (ms)"),
    end_ts: Optional[int] = Query(None, description="Timestamp final (ms)")
):
    """Retorna detalhes de um stream específico.
    
    Inclui:
    - Últimas mensagens (XREVRANGE)
    - Consumer groups e consumers ativos
    - Estatísticas de pending/lag
    """
    r = get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis não disponível")

    try:
        # 1. Stream Info
        try:
            info = r.xinfo_stream(stream_name)
            stream_exists = True
        except redis.ResponseError:
            raise HTTPException(status_code=404, detail=f"Stream '{stream_name}' não existe")

        # 2. Recent Messages
        # If filtering by time, we use range. Redis IDs are timestamp-seq.
        messages = []
        try:
            if start_ts is not None and end_ts is not None:
                # Use XRANGE/XREVRANGE with IDs
                # We want newest first, so XREVRANGE
                max_id = f"{end_ts}-99999"
                min_id = f"{start_ts}-0"
                raw_msgs = r.xrevrange(stream_name, max=max_id, min=min_id, count=limit)
            else:
                raw_msgs = r.xrevrange(stream_name, max="+", min="-", count=limit)
                
            for msg_id, fields in raw_msgs:
                messages.append({
                    "id": msg_id,
                    "timestamp": msg_id.split("-")[0],  # Extract timestamp from ID
                    "fields": fields
                })
        except Exception as e:
            messages = []

        # 3. Consumer Groups
        groups = []
        total_pending = 0
        try:
            groups_raw = r.xinfo_groups(stream_name)
            for g in groups_raw:
                group_name = g.get("name", "")
                pending = g.get("pending", 0)
                total_pending += pending
                
                # Get consumers in this group
                consumers = []
                try:
                    consumers_raw = r.xinfo_consumers(stream_name, group_name)
                    for c in consumers_raw:
                        consumers.append({
                            "name": c.get("name", ""),
                            "pending": c.get("pending", 0),
                            "idle_ms": c.get("idle", 0),
                        })
                except Exception:
                    pass
                
                # Get pending message IDs using XPENDING with range
                pending_ids = []
                try:
                    if pending > 0:
                        # XPENDING stream group [start end count] returns detailed info
                        pending_entries = r.xpending_range(stream_name, group_name, min="-", max="+", count=min(pending, 100))
                        for entry in pending_entries:
                            # entry is dict with 'message_id', 'consumer', 'time_since_delivered', 'times_delivered'
                            if isinstance(entry, dict):
                                pending_ids.append(entry.get("message_id", ""))
                            elif isinstance(entry, (list, tuple)) and len(entry) > 0:
                                pending_ids.append(entry[0])
                except Exception as e:
                    pass  # XPENDING might not be available or format differs
                
                groups.append({
                    "name": group_name,
                    "consumers_count": g.get("consumers", 0),
                    "pending": pending,
                    "pending_ids": pending_ids,
                    "last_delivered_id": g.get("last-delivered-id", ""),
                    "entries_read": g.get("entries-read", 0),
                    "consumers": consumers
                })
        except Exception:
            pass

        # 4. Calculate metrics
        length = info.get("length", 0)
        first_entry = info.get("first-entry")
        last_entry = info.get("last-entry")
        
        # Time range if messages exist
        time_range_ms = 0
        if first_entry and last_entry:
            try:
                first_ts = int(first_entry[0].split("-")[0])
                last_ts_val = int(last_entry[0].split("-")[0])
                time_range_ms = last_ts_val - first_ts
            except:
                pass

        return {
            "name": stream_name,
            "length": length,
            "first_entry_id": first_entry[0] if first_entry else None,
            "last_entry_id": last_entry[0] if last_entry else None,
            "time_range_hours": round(time_range_ms / 3600000, 2) if time_range_ms > 0 else 0,
            "groups_count": len(groups),
            "total_consumers": sum(g.get("consumers_count", 0) for g in groups),
            "total_pending": total_pending,
            "groups": groups,
            "messages": messages,
            "avg_msg_per_hour": round(length / (time_range_ms / 3600000), 1) if time_range_ms > 0 else 0,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/streams/{stream_name}/throughput")
async def get_stream_throughput(
    stream_name: str,
    interval: str = Query("day", regex="^(day|month)$")
):
    """Retorna throughput do stream agrupado por intervalo (day/month)."""
    r = get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis não disponível")

    # Simple aggregation implementation:
    # Fetch all keys (just IDs if possible, but xrange fetches all).
    # Since we have ~25k messages, we can fetch all and aggregate in memory.
    # For larger scale, we'd need Redis TimeSeries or background aggregation.
    try:
        # Fetch all messages (count=100000 to be safe)
        entries = r.xrange(stream_name, min="-", max="+", count=100000)
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

    from collections import defaultdict
    data_map = defaultdict(int)

    for msg_id, _ in entries:
        try:
            ts_ms = int(msg_id.split("-")[0])
            dt = datetime.fromtimestamp(ts_ms / 1000)
            
            if interval == "day":
                key = dt.strftime("%Y-%m-%d")
            else:
                key = dt.strftime("%Y-%m")
            
            data_map[key] += 1
        except:
            pass
    
    # Sort by date
    sorted_keys = sorted(data_map.keys())
    result = []
    
    for k in sorted_keys:
        result.append({
            "label": k,
            "count": data_map[k]
        })
        
    return result


@app.get("/rate-limit/{client}")
async def get_rate_limit_status(client: str, path: str = Query("/", description="Path do endpoint")):
    """Verifica tokens restantes de rate limit para um cliente."""
    supervisor = get_supervisor(get_redis())
    rate_key = f"{client}:{path}"
    remaining = supervisor.rate_limiter.get_remaining(rate_key)
    
    return {
        "client": client,
        "path": path,
        "remaining_tokens": remaining,
        "max_tokens": supervisor.rate_limiter.max_calls,
        "window_seconds": supervisor.rate_limiter.window_seconds,
    }


# ============================================================================
# ATLAS AGENT CONFIGURATION ENDPOINTS
# ============================================================================

class AtlasConfigUpdate(BaseModel):
    """Model for Atlas configuration updates."""
    database_url: Optional[str] = None
    database_pool_size: Optional[int] = None
    database_max_overflow: Optional[int] = None
    database_pool_recycle: Optional[int] = None
    redis_url: Optional[str] = None
    sentinel_stream: Optional[str] = None
    api_key: Optional[str] = None
    log_level: Optional[str] = None
    workers: Optional[int] = None
    debug: Optional[bool] = None
    cron_schedule: Optional[str] = None


ATLAS_ENV_FILE = "/Users/lucasseverino/Documents/VIVA/AGENTES/atlas_agent/.env"
ATLAS_CRONTAB_FILE = "/Users/lucasseverino/Documents/VIVA/AGENTES/atlas_agent/scripts/crontab"


def read_env_file(path: str) -> Dict[str, str]:
    """Read .env file and return as dictionary."""
    env_vars = {}
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env_vars


def write_env_file(path: str, env_vars: Dict[str, str]) -> None:
    """Write dictionary to .env file, preserving comments and structure."""
    lines = []
    existing_keys = set()
    
    try:
        with open(path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    key = stripped.split('=', 1)[0].strip()
                    if key in env_vars:
                        lines.append(f"{key}={env_vars[key]}\n")
                        existing_keys.add(key)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
    except FileNotFoundError:
        pass
    
    # Add any new keys not in original file
    for key, value in env_vars.items():
        if key not in existing_keys:
            lines.append(f"{key}={value}\n")
    
    with open(path, 'w') as f:
        f.writelines(lines)


@app.get("/atlas/config")
async def get_atlas_config():
    """Fetch current Atlas Agent configuration from .env file."""
    env_vars = read_env_file(ATLAS_ENV_FILE)
    
    # Read crontab schedule
    cron_schedule = "0 10 * * *"  # Default
    try:
        with open(ATLAS_CRONTAB_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and 'daily_routine.sh' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        cron_schedule = ' '.join(parts[:5])
                        break
    except FileNotFoundError:
        pass
    
    return {
        "database": {
            "url": env_vars.get("DATABASE_URL", "").replace(env_vars.get("DATABASE_URL", "").split("@")[0].split(":")[-1] if "@" in env_vars.get("DATABASE_URL", "") else "", "***") if "@" in env_vars.get("DATABASE_URL", "") else env_vars.get("DATABASE_URL", ""),
            "url_raw": env_vars.get("DATABASE_URL", ""),
            "pool_size": int(env_vars.get("DATABASE_POOL_SIZE", "10")),
            "max_overflow": int(env_vars.get("DATABASE_MAX_OVERFLOW", "20")),
            "pool_recycle": int(env_vars.get("DATABASE_POOL_RECYCLE", "3600")),
        },
        "redis": {
            "url": env_vars.get("REDIS_URL", "redis://shared-redis:6379/0"),
            "sentinel_stream": env_vars.get("SENTINEL_STREAM", "telemetry.received"),
        },
        "server": {
            "host": env_vars.get("HOST", "0.0.0.0"),
            "port": int(env_vars.get("PORT", "8000")),
            "workers": int(env_vars.get("WORKERS", "4")),
            "debug": env_vars.get("DEBUG", "false").lower() in ("true", "1", "yes"),
        },
        "security": {
            "api_key": env_vars.get("API_KEY", "")[:8] + "..." if len(env_vars.get("API_KEY", "")) > 8 else env_vars.get("API_KEY", ""),
            "api_key_full": env_vars.get("API_KEY", ""),
        },
        "logging": {
            "log_level": env_vars.get("LOG_LEVEL", "INFO"),
        },
        "scheduling": {
            "cron_schedule": cron_schedule,
            "cron_description": parse_cron_description(cron_schedule),
        },
        "app": {
            "name": env_vars.get("APP_NAME", "Atlas Agent API"),
            "version": env_vars.get("APP_VERSION", "1.0.0"),
        }
    }


def parse_cron_description(cron: str) -> str:
    """Convert cron expression to human-readable description."""
    parts = cron.split()
    if len(parts) < 5:
        return "Formato inválido"
    
    minute, hour = parts[0], parts[1]
    
    # Convert UTC to BRT (UTC-3)
    try:
        utc_hour = int(hour)
        brt_hour = (utc_hour - 3) % 24
        return f"Todos os dias às {brt_hour:02d}:{minute.zfill(2)} BRT"
    except ValueError:
        return f"Expressão: {cron}"


@app.post("/atlas/config")
async def update_atlas_config(config: AtlasConfigUpdate):
    """Update Atlas Agent configuration (persistent to .env file)."""
    env_vars = read_env_file(ATLAS_ENV_FILE)
    
    # Update only provided values
    if config.database_url is not None:
        env_vars["DATABASE_URL"] = config.database_url
    if config.database_pool_size is not None:
        env_vars["DATABASE_POOL_SIZE"] = str(config.database_pool_size)
    if config.database_max_overflow is not None:
        env_vars["DATABASE_MAX_OVERFLOW"] = str(config.database_max_overflow)
    if config.database_pool_recycle is not None:
        env_vars["DATABASE_POOL_RECYCLE"] = str(config.database_pool_recycle)
    if config.redis_url is not None:
        env_vars["REDIS_URL"] = config.redis_url
    if config.sentinel_stream is not None:
        env_vars["SENTINEL_STREAM"] = config.sentinel_stream
    if config.api_key is not None:
        env_vars["API_KEY"] = config.api_key
    if config.log_level is not None:
        env_vars["LOG_LEVEL"] = config.log_level
    if config.workers is not None:
        env_vars["WORKERS"] = str(config.workers)
    if config.debug is not None:
        env_vars["DEBUG"] = str(config.debug).lower()
    
    # Write to file
    write_env_file(ATLAS_ENV_FILE, env_vars)
    
    # Update crontab if provided
    if config.cron_schedule is not None:
        update_crontab_schedule(config.cron_schedule)
    
    return {
        "status": "success",
        "message": "Configuração salva. Reinicie o Atlas Agent para aplicar as alterações.",
        "requires_restart": True
    }


def update_crontab_schedule(schedule: str) -> None:
    """Update the cron schedule in the crontab file."""
    lines = []
    try:
        with open(ATLAS_CRONTAB_FILE, 'r') as f:
            for line in f:
                if 'daily_routine.sh' in line and not line.strip().startswith('#'):
                    # Replace the schedule part
                    parts = line.split()
                    if len(parts) >= 6:
                        new_parts = schedule.split() + parts[5:]
                        lines.append(' '.join(new_parts) + '\n')
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
        
        with open(ATLAS_CRONTAB_FILE, 'w') as f:
            f.writelines(lines)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar crontab: {e}")


@app.post("/atlas/test-db")
async def test_database_connection():
    """Test database connection using current configuration."""
    import subprocess
    
    env_vars = read_env_file(ATLAS_ENV_FILE)
    db_url = env_vars.get("DATABASE_URL", "")
    
    if not db_url:
        return {"status": "error", "message": "DATABASE_URL não configurada"}
    
    try:
        # Parse the URL to extract host and port
        # Format: postgresql://user:pass@host:port/dbname
        if "@" in db_url:
            host_part = db_url.split("@")[1].split("/")[0]
            if ":" in host_part:
                host, port = host_part.split(":")
            else:
                host, port = host_part, "5432"
        else:
            return {"status": "error", "message": "URL inválida"}
        
        # Convert host.docker.internal to localhost for local testing
        if host == "host.docker.internal":
            host = "localhost"
        
        # Test with nc (netcat)
        result = subprocess.run(
            ["nc", "-zv", "-w", "3", host, port],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            return {
                "status": "success",
                "message": f"Conexão com {host}:{port} estabelecida",
                "host": host,
                "port": port
            }
        else:
            return {
                "status": "error",
                "message": f"Não foi possível conectar a {host}:{port}",
                "details": result.stderr
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Timeout ao testar conexão"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/atlas/test-redis")
async def test_redis_connection():
    """Test Redis connection using current configuration."""
    env_vars = read_env_file(ATLAS_ENV_FILE)
    redis_url = env_vars.get("REDIS_URL", "redis://shared-redis:6379/0")
    
    try:
        test_client = redis.from_url(redis_url, decode_responses=True, socket_timeout=3)
        pong = test_client.ping()
        
        if pong:
            info = test_client.info("server")
            return {
                "status": "success",
                "message": "Conexão com Redis estabelecida",
                "redis_version": info.get("redis_version", "unknown"),
                "uptime_days": info.get("uptime_in_days", 0)
            }
        else:
            return {"status": "error", "message": "Redis não respondeu ao PING"}
    except redis.ConnectionError as e:
        return {"status": "error", "message": f"Erro de conexão: {e}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/atlas/restart")
async def restart_atlas_container():
    """Restart the Atlas Agent Docker container."""
    import subprocess
    
    try:
        # Restart the container
        result = subprocess.run(
            ["docker", "restart", "atlas-agent"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            return {
                "status": "success",
                "message": "Atlas Agent reiniciado com sucesso",
                "container": "atlas-agent"
            }
        else:
            return {
                "status": "error",
                "message": f"Erro ao reiniciar: {result.stderr}"
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Timeout ao reiniciar container"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/atlas/trigger-routine")
async def trigger_daily_routine():
    """Manually trigger the daily routine (scraping + import)."""
    import subprocess
    
    try:
        # Execute the routine inside the container
        result = subprocess.run(
            ["docker", "exec", "atlas-agent", "/app/scripts/daily_routine.sh"],
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes max
        )
        
        if result.returncode == 0:
            return {
                "status": "success",
                "message": "Rotina diária executada com sucesso",
                "output": result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
            }
        else:
            return {
                "status": "error",
                "message": "Rotina falhou",
                "output": result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Timeout - rotina demorou mais de 5 minutos"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/atlas/routine-status")
async def get_routine_status():
    """Get the status of the last daily routine execution from Redis stream."""
    r = get_redis()
    if not r:
        return {"status": "unknown", "message": "Redis não disponível"}
    
    try:
        # Get last events from atlas.daily_routine stream
        entries = r.xrevrange("atlas.daily_routine", max="+", min="-", count=10)
        
        if not entries:
            return {"status": "unknown", "message": "Nenhuma execução encontrada"}
        
        # Find the last COMPLETED or FAILED event
        for msg_id, fields in entries:
            if fields.get("event_type") == "daily_routine":
                status = fields.get("status", "unknown")
                timestamp = fields.get("timestamp", "")
                message = fields.get("message", "")
                
                return {
                    "status": status.lower(),
                    "message": message,
                    "timestamp": timestamp,
                    "message_id": msg_id
                }
        
        return {"status": "unknown", "message": "Status não encontrado"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/atlas/routine-history")
async def get_routine_history(limit: int = Query(20, ge=1, le=100)):
    """Get routine execution history from Redis stream for timeline display."""
    r = get_redis()
    if not r:
        return {"events": [], "error": "Redis não disponível"}
    
    try:
        entries = r.xrevrange("atlas.daily_routine", count=limit)
        
        events = []
        for msg_id, fields in entries:
            events.append({
                "id": msg_id,
                "event_type": fields.get("event_type", "unknown"),
                "status": fields.get("status", "unknown"),
                "message": fields.get("message", ""),
                "timestamp": fields.get("timestamp", ""),
                "hostname": fields.get("hostname", "")
            })
        
        return {"events": events, "count": len(events)}
    except Exception as e:
        return {"events": [], "error": str(e)}


@app.get("/atlas/logs")
async def get_atlas_logs(date: Optional[str] = None, lines: int = Query(100, ge=10, le=500)):
    """Get daily routine log events from Redis stream."""
    r = get_redis()
    if not r:
        return {"status": "error", "message": "Redis não disponível"}
    
    try:
        # Get events from atlas.daily_routine stream
        entries = r.xrevrange("atlas.daily_routine", count=lines)
        
        if not entries:
            return {
                "status": "not_found",
                "message": "Nenhum log encontrado",
                "lines": []
            }
        
        log_lines = []
        for msg_id, fields in entries:
            timestamp = fields.get("timestamp", "")
            event_type = fields.get("event_type", "unknown")
            status = fields.get("status", "")
            message = fields.get("message", "")
            
            # Format as log line
            status_icon = "✓" if status in ["COMPLETED", "SUCCESS"] else "✗" if status == "FAILED" else "→"
            log_line = f"[{timestamp}] {status_icon} [{event_type.upper()}] {status}: {message}"
            log_lines.append(log_line)
        
        return {
            "status": "success",
            "date": date or datetime.now().strftime("%Y%m%d"),
            "lines": log_lines,
            "source": "redis:atlas.daily_routine"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/atlas/scraping-status")
async def get_scraping_status():
    """Get scraping status per client from Redis stream events."""
    r = get_redis()
    if not r:
        return {"clients": [], "error": "Redis não disponível"}
    
    try:
        # Get last 50 events to find scraping status per client
        entries = r.xrevrange("atlas.daily_routine", count=50)
        
        # Find last scraping events
        scraping_events = []
        seen_scraping = False
        
        for msg_id, fields in entries:
            event_type = fields.get("event_type", "")
            if event_type == "scraping":
                scraping_events.append({
                    "status": fields.get("status", "unknown"),
                    "message": fields.get("message", ""),
                    "timestamp": fields.get("timestamp", "")
                })
                seen_scraping = True
            elif event_type == "daily_routine" and seen_scraping:
                break  # Stop after finding scraping events from last routine
        
        # Also check for client-specific events if any
        return {
            "last_scraping": scraping_events[0] if scraping_events else None,
            "events": scraping_events[:5]
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/atlas/telemetry-stats")
async def get_telemetry_stats():
    """Get telemetry events published today (health_events, door_events)."""
    r = get_redis()
    if not r:
        return {"error": "Redis não disponível"}
    
    try:
        # Get stream lengths
        received_len = r.xlen("telemetry.received") or 0
        validated_len = r.xlen("telemetry.validated") or 0
        rejected_len = r.xlen("telemetry.rejected") or 0
        
        # Get last 5 events
        last_events = r.xrevrange("telemetry.received", count=5)
        
        recent_events = []
        for msg_id, fields in last_events:
            recent_events.append({
                "id": msg_id,
                "type": fields.get("type", "unknown"),
                "client": fields.get("client", "unknown")
            })
        
        return {
            "stream_length": received_len,
            "validated_today": validated_len,
            "rejected_today": rejected_len,
            "recent_events": recent_events
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/atlas/dashboard-stats")
async def get_dashboard_stats(client: str = Query("Coel")):
    """Proxy to Atlas Agent /dashboard/stats endpoint."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            headers = {"X-API-Key": ATLAS_API_KEY} if ATLAS_API_KEY else {}
            response = await http_client.get(
                f"{ATLAS_AGENT_URL}/atlas/dashboard/stats",
                params={"client": client, "period": 30},
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                # Data can come from counts or directly from the response
                counts = data.get("counts", {})
                return {
                    "status": "success",
                    "client": client,
                    "assets": counts.get("total_assets") or data.get("total_assets", 0),
                    "outlets": counts.get("total_outlets") or data.get("total_outlets", 0),
                    "users": counts.get("total_users") or data.get("total_users", 0),
                    "smart_devices": counts.get("smart_assets") or data.get("smart_assets", 0),
                    "alerts": counts.get("active_alerts") or data.get("alerts_period_count", 0),
                    "data": data
                }
            else:
                return {"status": "error", "message": f"Atlas retornou {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/atlas/api-keys")
async def list_api_keys():
    """List API keys from Atlas Agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            headers = {"X-API-Key": ATLAS_API_KEY} if ATLAS_API_KEY else {}
            response = await http_client.get(
                f"{ATLAS_AGENT_URL}/atlas/admin/api-keys",
                headers=headers
            )
            
            if response.status_code == 200:
                return {"status": "success", "keys": response.json()}
            else:
                return {"status": "error", "message": f"Erro: {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/atlas/api-keys")
async def create_api_key(name: str = Query(...), client: Optional[str] = Query(None)):
    """Create new API key via Atlas Agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            headers = {"X-API-Key": ATLAS_API_KEY, "Content-Type": "application/json"} if ATLAS_API_KEY else {"Content-Type": "application/json"}
            response = await http_client.post(
                f"{ATLAS_AGENT_URL}/atlas/admin/api-keys",
                json={"name": name, "client": client},
                headers=headers
            )
            
            if response.status_code in [200, 201]:
                return {"status": "success", "key": response.json()}
            else:
                return {"status": "error", "message": f"Erro: {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/atlas/performance")
async def get_performance_stats():
    """Get performance stats for last 7 routine executions."""
    r = get_redis()
    if not r:
        return {"executions": [], "error": "Redis não disponível"}
    
    try:
        entries = r.xrevrange("atlas.daily_routine", count=100)
        
        # Parse events to calculate execution times
        executions = []
        current_routine = None
        
        for msg_id, fields in entries:
            event_type = fields.get("event_type", "")
            status = fields.get("status", "")
            timestamp = fields.get("timestamp", "")
            
            if event_type == "daily_routine":
                if status == "COMPLETED" or status == "FAILED":
                    if current_routine:
                        current_routine["end"] = timestamp
                        current_routine["status"] = status.lower()
                        executions.append(current_routine)
                    current_routine = None
                elif status == "STARTED":
                    current_routine = {"start": timestamp, "steps": []}
            elif current_routine and event_type in ["scraping", "import"]:
                current_routine["steps"].append({
                    "step": event_type,
                    "status": status,
                    "timestamp": timestamp
                })
            
            if len(executions) >= 7:
                break
        
        return {"executions": executions[:7]}
    except Exception as e:
        return {"executions": [], "error": str(e)}


@app.get("/atlas/validation-log")
async def get_validation_log(limit: int = Query(50, ge=1, le=200)):
    """Get validation audit log combining validated and rejected telemetry events.
    
    Returns a unified view of all validation decisions made by CAOS.
    """
    r = get_redis()
    if not r:
        return {"entries": [], "error": "Redis não disponível"}
    
    try:
        entries = []
        
        # Get recent validated events
        try:
            validated_msgs = r.xrevrange("telemetry.validated", count=limit)
            for msg_id, fields in validated_msgs:
                timestamp = msg_id.split("-")[0]
                payload = {}
                try:
                    import json
                    payload = json.loads(fields.get("payload", "{}"))
                except:
                    pass
                
                # Extract client and asset info
                client = (payload.get("health_events_client") or 
                         payload.get("door_client") or 
                         payload.get("tenantId") or 
                         "unknown")
                asset = (payload.get("health_events_asset_serial_number") or 
                        payload.get("door_asset_serial_number") or 
                        payload.get("assetId") or 
                        "?")
                event_type = (payload.get("health_events_event_type") or 
                             payload.get("door_event_type") or 
                             payload.get("eventType") or 
                             "telemetry")
                
                entries.append({
                    "id": msg_id,
                    "timestamp": timestamp,
                    "status": "approved",
                    "client": client,
                    "asset": asset,
                    "event_type": event_type,
                    "reason": None,
                    "stream": "telemetry.validated"
                })
        except Exception as e:
            pass  # Stream might not exist
        
        # Get recent rejected events
        try:
            rejected_msgs = r.xrevrange("telemetry.rejected", count=limit)
            for msg_id, fields in rejected_msgs:
                timestamp = msg_id.split("-")[0]
                payload = {}
                try:
                    import json
                    payload = json.loads(fields.get("payload", "{}"))
                except:
                    pass
                
                # Extract client and asset info
                client = (payload.get("health_events_client") or 
                         payload.get("door_client") or 
                         payload.get("tenantId") or 
                         "unknown")
                asset = (payload.get("health_events_asset_serial_number") or 
                        payload.get("door_asset_serial_number") or 
                        payload.get("assetId") or 
                        "?")
                
                entries.append({
                    "id": msg_id,
                    "timestamp": timestamp,
                    "status": "rejected",
                    "client": client,
                    "asset": asset,
                    "event_type": "telemetry",
                    "reason": fields.get("reason", "Desconhecido"),
                    "stream": "telemetry.rejected"
                })
        except Exception as e:
            pass  # Stream might not exist
        
        # Sort by timestamp (newest first)
        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        
        # Get stats from stream lengths (more reliable than counters)
        try:
            validated_count = r.xlen("telemetry.validated")
        except:
            validated_count = 0
        try:
            rejected_count = r.xlen("telemetry.rejected")
        except:
            rejected_count = 0
        
        total = validated_count + rejected_count
        approval_rate = round(validated_count / max(1, total) * 100, 1) if total > 0 else 0
        
        return {
            "entries": entries[:limit],
            "stats": {
                "total_validated": validated_count,
                "total_rejected": rejected_count,
                "approval_rate": approval_rate
            }
        }
    except Exception as e:
        return {"entries": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
