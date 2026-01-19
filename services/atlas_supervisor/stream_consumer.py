"""
CAOS Stream Consumer - Filtra telemetria antes do Sentinel

Fluxo:
  telemetry.received → CAOS (valida) → telemetry.validated → Sentinel
"""

import json
import logging
import os
import signal
import sys
import time
from typing import Any, Dict, List, Tuple

import redis

from services.atlas_supervisor.supervisor_simple import AtlasSupervisor
from shared.schemas import TelemetryEvent

# ============================================================
# Configuração
# ============================================================
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Stream de entrada (dados brutos)
INPUT_STREAM = os.environ.get("CAOS_INPUT_STREAM", "telemetry.received")
INPUT_GROUP = os.environ.get("CAOS_INPUT_GROUP", "caos-filter-cg")
CONSUMER_NAME = os.environ.get("CAOS_CONSUMER_NAME", f"caos-filter-{os.getpid()}")

# Stream de saída (dados validados)
OUTPUT_STREAM = os.environ.get("CAOS_OUTPUT_STREAM", "telemetry.validated")

# Stream para dados rejeitados (opcional, para auditoria)
REJECTED_STREAM = os.environ.get("CAOS_REJECTED_STREAM", "telemetry.rejected")

READ_BLOCK_MS = int(os.environ.get("CAOS_BLOCK_MS", "5000"))
READ_COUNT = int(os.environ.get("CAOS_READ_COUNT", "10"))

# ============================================================
# Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("caos.stream_consumer")

running = True
supervisor = AtlasSupervisor()


def shutdown(*_: int) -> None:
    global running
    running = False
    log.info("Shutting down CAOS stream consumer...")


def ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    """Cria consumer group se não existir."""
    try:
        groups = r.xinfo_groups(stream)
        for g in groups:
            name = g.get("name")
            if isinstance(name, bytes):
                name = name.decode()
            if name == group:
                return
    except redis.ResponseError:
        pass

    try:
        log.info("Creating consumer group %s for stream %s", group, stream)
        r.xgroup_create(stream, group, id="$", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            log.info("Consumer group %s already exists", group)
            return
        raise


def parse_message(fields: Dict[bytes, bytes]) -> Dict[str, Any]:
    """Extrai payload JSON da mensagem Redis."""
    payload_raw = fields.get(b"payload") or fields.get("payload")
    if payload_raw is None:
        raise ValueError("Missing 'payload' field")
    
    if isinstance(payload_raw, bytes):
        payload_raw = payload_raw.decode("utf-8", errors="replace")
    
    return json.loads(payload_raw)


def extract_audit_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai campos para auditoria do payload."""
    # Tenta formato novo
    if "assetId" in payload or "eventId" in payload:
        return {
            "client": payload.get("tenantId") or payload.get("client", "unknown"),
            "asset_id": payload.get("assetId"),
            "event_type": payload.get("eventType", "telemetry"),
            "device_id": payload.get("deviceId"),
            "temperature_c": payload.get("metrics", {}).get("tempC"),
        }
    
    # Formato legado
    return {
        "client": payload.get("health_events_client") or payload.get("door_client", "unknown"),
        "asset_id": payload.get("health_events_asset_serial_number") or payload.get("door_asset_serial_number"),
        "event_type": payload.get("health_events_event_type") or payload.get("door_event_type", "telemetry"),
        "device_id": payload.get("health_events_smart_device_mac") or payload.get("door_smart_device_mac"),
        "temperature_c": payload.get("health_events_temperature_c"),
        "asset_type": payload.get("health_events_asset_type"),
        "outlet_code": payload.get("health_events_outlet_code"),
        "avg_power_consumption_watt": payload.get("health_events_avg_power_consumption_watt"),
        "total_compressor_on_time_percent": payload.get("health_events_total_compressor_on_time_percent"),
        "battery_level": payload.get("health_events_battery"),
    }


def build_telemetry_event(audit_fields: Dict[str, Any]) -> TelemetryEvent:
    """Constrói TelemetryEvent a partir dos campos extraídos."""
    # Não usar fallback "unknown" - deixar o supervisor validar
    asset_id = audit_fields.get("asset_id")
    client = audit_fields.get("client")
    
    return TelemetryEvent(
        asset_serial_number=asset_id if asset_id else "",  # String vazia falha na validação
        client=client if client and client != "unknown" else "",
        asset_type=audit_fields.get("asset_type"),
        outlet_code=audit_fields.get("outlet_code"),
        temperature_c=audit_fields.get("temperature_c"),
        avg_power_consumption_watt=audit_fields.get("avg_power_consumption_watt"),
        total_compressor_on_time_percent=audit_fields.get("total_compressor_on_time_percent"),
        battery_level=audit_fields.get("battery_level"),
    )


def handle_entries(
    r: redis.Redis,
    entries: List[Tuple[bytes, List[Tuple[bytes, Dict[bytes, bytes]]]]]
) -> None:
    """Processa mensagens do stream de entrada."""
    
    for stream, messages in entries:
        for message_id, fields in messages:
            msg_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
            
            try:
                # 1. Parse do payload
                payload = parse_message(fields)
                audit_fields = extract_audit_fields(payload)
                
                # 2. Construir TelemetryEvent para auditoria
                telemetry_event = build_telemetry_event(audit_fields)
                
                # 3. Auditoria CAOS
                result = supervisor.audit_telemetry(telemetry_event)
                
                # 4. Decisão
                if result.status in ("OK", "APPROVED"):
                    # Publica no stream de saída (dados validados)
                    r.xadd(
                        OUTPUT_STREAM,
                        {"payload": json.dumps(payload)},
                    )
                    log.info(
                        "✅ APPROVED %s → %s (client=%s, asset=%s)",
                        msg_id_str,
                        OUTPUT_STREAM,
                        audit_fields["client"],
                        audit_fields.get("asset_id", "?"),
                    )
                else:
                    # Publica no stream de rejeitados (para auditoria)
                    r.xadd(
                        REJECTED_STREAM,
                        {
                            "payload": json.dumps(payload),
                            "reason": result.reason or "unknown",
                            "status": result.status,
                        },
                    )
                    log.warning(
                        "🚫 BLOCKED %s → %s (reason=%s)",
                        msg_id_str,
                        REJECTED_STREAM,
                        result.reason or "unknown",
                    )
                
                # 5. ACK - mensagem processada
                r.xack(INPUT_STREAM, INPUT_GROUP, message_id)
                
            except json.JSONDecodeError as exc:
                log.error("Invalid JSON in %s: %s", msg_id_str, exc)
                # ACK para evitar loop infinito
                r.xack(INPUT_STREAM, INPUT_GROUP, message_id)
                
            except Exception as exc:
                log.exception("Error processing %s: %s", msg_id_str, exc)
                # Não dá ACK - será reprocessado


def main() -> int:
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    r = redis.from_url(REDIS_URL, decode_responses=False)
    
    log.info("=" * 60)
    log.info("CAOS Stream Filter starting")
    log.info("  Input:    %s (group: %s)", INPUT_STREAM, INPUT_GROUP)
    log.info("  Output:   %s", OUTPUT_STREAM)
    log.info("  Rejected: %s", REJECTED_STREAM)
    log.info("  Redis:    %s", REDIS_URL)
    log.info("=" * 60)
    
    ensure_group(r, INPUT_STREAM, INPUT_GROUP)
    
    while running:
        try:
            entries = r.xreadgroup(
                groupname=INPUT_GROUP,
                consumername=CONSUMER_NAME,
                streams={INPUT_STREAM: ">"},
                count=READ_COUNT,
                block=READ_BLOCK_MS,
            )
            if entries:
                handle_entries(r, entries)
        except redis.ResponseError as exc:
            log.error("Redis error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            log.exception("Unhandled error: %s", exc)
            time.sleep(1)
    
    log.info("CAOS Stream Filter stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
