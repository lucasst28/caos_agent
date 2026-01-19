"""
CAOS Consumer Health Monitor - Monitora saúde dos consumers

Verifica:
- Se consumers estão ativos (publicando health)
- Taxa de erro de processamento
- Lag de processamento
- Consumers inativos
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import redis

# ============================================================
# Configuração
# ============================================================
REDIS_URL = os.environ.get("REDIS_URL", "redis://shared-redis:6379/0")

# Stream de métricas dos consumers
CONSUMER_HEALTH_STREAM = os.environ.get("CAOS_CONSUMER_HEALTH_STREAM", "sentinel.consumer_health")
CONSUMER_HEALTH_GROUP = os.environ.get("CAOS_CONSUMER_HEALTH_GROUP", "caos-consumer-health-cg")
CONSUMER_NAME = os.environ.get("CAOS_CONSUMER_NAME", f"consumer-health-monitor-{os.getpid()}")

# Stream de alertas
ALERTS_STREAM = os.environ.get("CAOS_ALERTS_STREAM", "caos.alerts")

# Limites para alertas
MAX_ERROR_RATE_PERCENT = float(os.environ.get("MAX_ERROR_RATE_PERCENT", "5"))  # 5%
MAX_INACTIVE_SECONDS = int(os.environ.get("MAX_INACTIVE_SECONDS", "120"))  # 2 minutos sem health

READ_BLOCK_MS = int(os.environ.get("CAOS_BLOCK_MS", "5000"))
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "30"))

# ============================================================
# Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("caos.consumer_health_monitor")

running = True


def shutdown(*_: int) -> None:
    global running
    running = False
    log.info("Shutting down consumer health monitor...")


class ConsumerHealthMonitor:
    """Monitor de saúde dos consumers."""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.consumers: Dict[str, Dict[str, Any]] = {}
        self.alerted_consumers: set = set()  # Consumers que já receberam alerta de inativo
    
    def process_health_event(self, event: Dict[str, Any]) -> None:
        """Processa um evento de saúde de um consumer."""
        consumer_name = event.get("consumer_name", "unknown")
        timestamp_str = event.get("timestamp", "")
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now(timezone.utc)
        
        # Atualiza estado do consumer
        messages_processed = int(event.get("messages_processed", 0))
        messages_success = int(event.get("messages_success", 0))
        messages_error = int(event.get("messages_error", 0))
        messages_skipped = int(event.get("messages_skipped", 0))
        uptime = int(event.get("uptime_seconds", 0))
        last_error = event.get("last_error", "")
        
        # Calcular taxa de erro
        error_rate = 0.0
        if messages_processed > 0:
            error_rate = (messages_error / messages_processed) * 100
        
        previous_state = self.consumers.get(consumer_name, {})
        previous_errors = previous_state.get("messages_error", 0)
        new_errors = messages_error - previous_errors
        
        self.consumers[consumer_name] = {
            "stream": event.get("stream", ""),
            "group": event.get("group", ""),
            "messages_processed": messages_processed,
            "messages_success": messages_success,
            "messages_error": messages_error,
            "messages_skipped": messages_skipped,
            "uptime_seconds": uptime,
            "error_rate_percent": error_rate,
            "last_health_at": timestamp,
            "last_error": last_error,
        }
        
        # Remover de alertados (está ativo novamente)
        self.alerted_consumers.discard(consumer_name)
        
        log.info(
            "📊 Consumer [%s] - processed=%d success=%d error=%d (%.1f%%) uptime=%ds",
            consumer_name, messages_processed, messages_success, 
            messages_error, error_rate, uptime
        )
        
        # Verificar taxa de erro
        if error_rate > MAX_ERROR_RATE_PERCENT and messages_processed > 10:
            self._publish_alert(
                "HIGH",
                "consumer_high_error_rate",
                f"Consumer {consumer_name} com taxa de erro alta: {error_rate:.1f}%",
                consumer_name=consumer_name,
            )
        
        # Alertar se houve novos erros
        if new_errors > 0 and last_error:
            log.warning("⚠️ Consumer [%s] teve %d novos erros: %s", consumer_name, new_errors, last_error)
    
    def check_inactive_consumers(self) -> None:
        """Verifica se há consumers inativos."""
        now = datetime.now(timezone.utc)
        max_inactive = timedelta(seconds=MAX_INACTIVE_SECONDS)
        
        for consumer_name, state in self.consumers.items():
            last_health = state.get("last_health_at")
            if not last_health:
                continue
            
            inactive_time = now - last_health
            if inactive_time > max_inactive and consumer_name not in self.alerted_consumers:
                inactive_seconds = int(inactive_time.total_seconds())
                log.error(
                    "🔴 Consumer [%s] inativo há %d segundos",
                    consumer_name, inactive_seconds
                )
                self._publish_alert(
                    "CRITICAL",
                    "consumer_inactive",
                    f"Consumer {consumer_name} inativo há {inactive_seconds}s",
                    consumer_name=consumer_name,
                )
                self.alerted_consumers.add(consumer_name)
    
    def check_pending_messages(self) -> None:
        """Verifica mensagens pendentes nos streams monitorados."""
        try:
            # Verificar telemetry.validated
            pending = self.redis.xpending("telemetry.validated", "sentinel-cg")
            if pending:
                pending_count = pending.get("pending", 0) if isinstance(pending, dict) else (pending[0] if pending else 0)
                if isinstance(pending_count, int) and pending_count > 100:
                    log.warning("⚠️ Stream telemetry.validated tem %d mensagens pendentes", pending_count)
                    self._publish_alert(
                        "MEDIUM",
                        "stream_high_pending",
                        f"telemetry.validated tem {pending_count} mensagens pendentes",
                    )
        except redis.ResponseError as exc:
            log.debug("Não foi possível verificar pending: %s", exc)
    
    def _publish_alert(
        self, 
        severity: str, 
        alert_type: str, 
        message: str,
        consumer_name: str = None,
    ) -> None:
        """Publica alerta no stream de alertas."""
        try:
            alert_data = {
                "severity": severity,
                "type": alert_type,
                "message": message,
                "source": "caos.consumer_health_monitor",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if consumer_name:
                alert_data["consumer_name"] = consumer_name
            
            self.redis.xadd(ALERTS_STREAM, alert_data)
            log.info("📢 Alerta publicado: [%s] %s", severity, alert_type)
        except Exception as exc:
            log.error("Erro ao publicar alerta: %s", exc)
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna o status atual de todos os consumers."""
        return {
            "consumers": self.consumers.copy(),
            "total_consumers": len(self.consumers),
            "inactive_alerts": list(self.alerted_consumers),
        }


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
    """Extrai campos da mensagem Redis."""
    result = {}
    for key, value in fields.items():
        k = key.decode() if isinstance(key, bytes) else key
        v = value.decode() if isinstance(value, bytes) else value
        result[k] = v
    return result


def main() -> int:
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    r = redis.from_url(REDIS_URL, decode_responses=False)
    monitor = ConsumerHealthMonitor(r)
    
    log.info("=" * 60)
    log.info("CAOS Consumer Health Monitor starting")
    log.info("  Stream: %s (group: %s)", CONSUMER_HEALTH_STREAM, CONSUMER_HEALTH_GROUP)
    log.info("  Max error rate: %.1f%%", MAX_ERROR_RATE_PERCENT)
    log.info("  Max inactive: %d seconds", MAX_INACTIVE_SECONDS)
    log.info("  Redis: %s", REDIS_URL)
    log.info("=" * 60)
    
    ensure_group(r, CONSUMER_HEALTH_STREAM, CONSUMER_HEALTH_GROUP)
    
    last_check = 0
    
    while running:
        try:
            # Ler eventos de saúde
            entries = r.xreadgroup(
                groupname=CONSUMER_HEALTH_GROUP,
                consumername=CONSUMER_NAME,
                streams={CONSUMER_HEALTH_STREAM: ">"},
                count=10,
                block=READ_BLOCK_MS,
            )
            
            if entries:
                for stream, messages in entries:
                    for message_id, fields in messages:
                        msg_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                        try:
                            event = parse_message(fields)
                            monitor.process_health_event(event)
                            r.xack(CONSUMER_HEALTH_STREAM, CONSUMER_HEALTH_GROUP, message_id)
                        except Exception as exc:
                            log.exception("Error processing %s: %s", msg_id_str, exc)
            
            # Verificações periódicas
            now = time.time()
            if now - last_check >= CHECK_INTERVAL_SEC:
                monitor.check_inactive_consumers()
                monitor.check_pending_messages()
                last_check = now
                
        except redis.ResponseError as exc:
            log.error("Redis error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            log.exception("Unhandled error: %s", exc)
            time.sleep(1)
    
    log.info("Consumer Health Monitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
