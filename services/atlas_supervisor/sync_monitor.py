"""
CAOS Sync Monitor - Monitora sincronização do banco Atlas → Redis

Verifica:
- Se a sincronização está ocorrendo regularmente
- Se há falhas na sincronização
- Quantidade de registros sincronizados
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

# Stream de eventos de sincronização
SYNC_STREAM = os.environ.get("CAOS_SYNC_STREAM", "atlas.db_sync")
SYNC_GROUP = os.environ.get("CAOS_SYNC_GROUP", "caos-sync-monitor-cg")
CONSUMER_NAME = os.environ.get("CAOS_SYNC_CONSUMER", f"sync-monitor-{os.getpid()}")

# Stream de alertas
ALERTS_STREAM = os.environ.get("CAOS_ALERTS_STREAM", "caos.alerts")

# Intervalo máximo sem sincronização (minutos)
MAX_SYNC_INTERVAL_MIN = int(os.environ.get("MAX_SYNC_INTERVAL_MIN", "15"))

READ_BLOCK_MS = int(os.environ.get("CAOS_BLOCK_MS", "5000"))
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "60"))

# ============================================================
# Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("caos.sync_monitor")

running = True


def shutdown(*_: int) -> None:
    global running
    running = False
    log.info("Shutting down sync monitor...")


class SyncMonitor:
    """Monitor de sincronização Atlas → Redis."""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.last_sync_time: Optional[datetime] = None
        self.last_sync_status: str = "UNKNOWN"
        self.total_health_synced: int = 0
        self.total_door_synced: int = 0
        self.sync_count_today: int = 0
        self.failure_count_today: int = 0
        self._today: str = ""
        self._reset_for_today()
    
    def _reset_for_today(self) -> None:
        """Reseta contadores para o dia atual."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._today != today:
            self._today = today
            self.sync_count_today = 0
            self.failure_count_today = 0
            log.info("Reset counters for new day: %s", today)
    
    def process_event(self, event: Dict[str, Any]) -> None:
        """Processa um evento de sincronização."""
        self._reset_for_today()
        
        event_type = event.get("event_type", "")
        status = event.get("status", "")
        message = event.get("message", "")
        timestamp_str = event.get("timestamp", "")
        details_str = event.get("details", "{}")
        
        if event_type != "db_sync":
            return
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now(timezone.utc)
        
        if status == "STARTED":
            log.info("🔄 Sincronização INICIADA às %s", timestamp_str)
            
        elif status == "COMPLETED":
            self.last_sync_time = timestamp
            self.last_sync_status = "COMPLETED"
            self.sync_count_today += 1
            
            # Extrair detalhes
            try:
                details = json.loads(details_str) if details_str else {}
                health_sent = details.get("health_events_sent", 0)
                door_sent = details.get("door_sent", 0)
                self.total_health_synced += health_sent
                self.total_door_synced += door_sent
                log.info(
                    "✅ Sincronização CONCLUÍDA: %d health_events, %d door (total hoje: %d syncs)",
                    health_sent, door_sent, self.sync_count_today
                )
            except json.JSONDecodeError:
                log.info("✅ Sincronização CONCLUÍDA às %s", timestamp_str)
                
        elif status == "FAILED":
            self.last_sync_status = "FAILED"
            self.failure_count_today += 1
            log.error("❌ Sincronização FALHOU: %s", message)
            self._publish_alert(
                "HIGH",
                "db_sync_failed",
                f"Sincronização Atlas→Redis falhou: {message}",
            )
    
    def check_sync_health(self) -> None:
        """Verifica se a sincronização está saudável."""
        self._reset_for_today()
        
        if self.last_sync_time is None:
            # Nunca sincronizou desde que o monitor iniciou
            return
        
        now = datetime.now(timezone.utc)
        time_since_sync = now - self.last_sync_time
        max_interval = timedelta(minutes=MAX_SYNC_INTERVAL_MIN)
        
        if time_since_sync > max_interval:
            minutes_ago = int(time_since_sync.total_seconds() / 60)
            log.warning(
                "⚠️ Última sincronização há %d minutos (limite: %d min)",
                minutes_ago, MAX_SYNC_INTERVAL_MIN
            )
            self._publish_alert(
                "MEDIUM",
                "db_sync_stale",
                f"Sincronização Atlas→Redis não ocorre há {minutes_ago} minutos",
            )
    
    def _publish_alert(self, severity: str, alert_type: str, message: str) -> None:
        """Publica alerta no stream de alertas."""
        try:
            self.redis.xadd(
                ALERTS_STREAM,
                {
                    "severity": severity,
                    "type": alert_type,
                    "message": message,
                    "source": "caos.sync_monitor",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "date": self._today,
                },
            )
            log.info("📢 Alerta publicado: [%s] %s", severity, alert_type)
        except Exception as exc:
            log.error("Erro ao publicar alerta: %s", exc)
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna o status atual do monitor."""
        self._reset_for_today()
        return {
            "date": self._today,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "last_sync_status": self.last_sync_status,
            "sync_count_today": self.sync_count_today,
            "failure_count_today": self.failure_count_today,
            "total_health_synced": self.total_health_synced,
            "total_door_synced": self.total_door_synced,
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
    monitor = SyncMonitor(r)
    
    log.info("=" * 60)
    log.info("CAOS Sync Monitor starting")
    log.info("  Stream: %s (group: %s)", SYNC_STREAM, SYNC_GROUP)
    log.info("  Max interval without sync: %d minutes", MAX_SYNC_INTERVAL_MIN)
    log.info("  Redis: %s", REDIS_URL)
    log.info("=" * 60)
    
    ensure_group(r, SYNC_STREAM, SYNC_GROUP)
    
    last_health_check = 0
    
    while running:
        try:
            # Ler eventos do stream
            entries = r.xreadgroup(
                groupname=SYNC_GROUP,
                consumername=CONSUMER_NAME,
                streams={SYNC_STREAM: ">"},
                count=10,
                block=READ_BLOCK_MS,
            )
            
            if entries:
                for stream, messages in entries:
                    for message_id, fields in messages:
                        msg_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                        try:
                            event = parse_message(fields)
                            monitor.process_event(event)
                            r.xack(SYNC_STREAM, SYNC_GROUP, message_id)
                        except Exception as exc:
                            log.exception("Error processing %s: %s", msg_id_str, exc)
            
            # Verificar saúde periodicamente
            now = time.time()
            if now - last_health_check >= CHECK_INTERVAL_SEC:
                monitor.check_sync_health()
                last_health_check = now
                
        except redis.ResponseError as exc:
            log.error("Redis error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            log.exception("Unhandled error: %s", exc)
            time.sleep(1)
    
    log.info("Sync Monitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
