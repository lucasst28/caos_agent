"""
CAOS Daily Routine Monitor - Monitora a rotina diária do Atlas

Verifica:
- Se a rotina foi executada até às 08:00
- Se completou com sucesso
- Se a sincronização DB→Redis ocorreu após a rotina
- Tempo de execução
- Alertas se falhou ou não executou
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import redis

# ============================================================
# Configuração
# ============================================================
REDIS_URL = os.environ.get("REDIS_URL", "redis://shared-redis:6379/0")

# Stream de eventos da rotina diária
ROUTINE_STREAM = os.environ.get("CAOS_ROUTINE_STREAM", "atlas.daily_routine")
ROUTINE_GROUP = os.environ.get("CAOS_ROUTINE_GROUP", "caos-routine-monitor-cg")

# Stream de eventos de sincronização DB→Redis
SYNC_STREAM = os.environ.get("CAOS_SYNC_STREAM", "atlas.db_sync")
SYNC_GROUP = os.environ.get("CAOS_SYNC_GROUP", "caos-routine-sync-cg")

CONSUMER_NAME = os.environ.get("CAOS_ROUTINE_CONSUMER", f"routine-monitor-{os.getpid()}")

# Stream de alertas
ALERTS_STREAM = os.environ.get("CAOS_ALERTS_STREAM", "caos.alerts")

# Horário esperado para a rotina (07:00 - 08:00 BRT = 10:00 - 11:00 UTC)
EXPECTED_START_HOUR_UTC = int(os.environ.get("EXPECTED_START_HOUR_UTC", "10"))  # 07:00 BRT
DEADLINE_HOUR_UTC = int(os.environ.get("DEADLINE_HOUR_UTC", "11"))  # 08:00 BRT

# Tempo máximo após rotina para sync ocorrer (minutos)
SYNC_GRACE_PERIOD_MIN = int(os.environ.get("SYNC_GRACE_PERIOD_MIN", "30"))

READ_BLOCK_MS = int(os.environ.get("CAOS_BLOCK_MS", "5000"))
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "300"))  # 5 minutos

# ============================================================
# Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("caos.routine_monitor")

running = True


def shutdown(*_: int) -> None:
    global running
    running = False
    log.info("Shutting down routine monitor...")


class DailyRoutineMonitor:
    """Monitor da rotina diária do Atlas."""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.today_status: Dict[str, Any] = {
            "date": None,
            "started_at": None,
            "completed_at": None,
            "failed_at": None,
            "status": "PENDING",  # PENDING, STARTED, COMPLETED, FAILED, MISSED
            "sync_status": "PENDING",  # PENDING, COMPLETED, FAILED
            "sync_at": None,
            "sync_health_events": 0,
            "sync_door_events": 0,
            "events": [],
        }
        self._reset_for_today()
    
    def _reset_for_today(self) -> None:
        """Reseta o status para o dia atual."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.today_status["date"] != today:
            self.today_status = {
                "date": today,
                "started_at": None,
                "completed_at": None,
                "failed_at": None,
                "status": "PENDING",
                "sync_status": "PENDING",
                "sync_at": None,
                "sync_health_events": 0,
                "sync_door_events": 0,
                "events": [],
            }
            log.info("Reset status for new day: %s", today)
    
    def process_event(self, event: Dict[str, Any]) -> None:
        """Processa um evento da rotina diária."""
        self._reset_for_today()
        
        event_type = event.get("event_type", "")
        status = event.get("status", "")
        message = event.get("message", "")
        timestamp = event.get("timestamp", "")
        
        self.today_status["events"].append(event)
        
        if event_type == "daily_routine":
            if status == "STARTED":
                self.today_status["started_at"] = timestamp
                self.today_status["status"] = "STARTED"
                log.info("📅 Rotina diária INICIADA às %s", timestamp)
                
            elif status == "COMPLETED":
                self.today_status["completed_at"] = timestamp
                self.today_status["status"] = "COMPLETED"
                duration = self._calculate_duration()
                log.info("✅ Rotina diária CONCLUÍDA às %s (duração: %s)", timestamp, duration)
                self._publish_alert("INFO", "daily_routine_completed", 
                                   f"Rotina diária concluída com sucesso em {duration}")
                
            elif status == "FAILED":
                self.today_status["failed_at"] = timestamp
                self.today_status["status"] = "FAILED"
                log.error("❌ Rotina diária FALHOU às %s: %s", timestamp, message)
                self._publish_alert("HIGH", "daily_routine_failed", 
                                   f"Rotina diária falhou: {message}")
        
        elif event_type == "scraping":
            if status == "STARTED":
                log.info("🔄 Scraping iniciado")
            elif status == "COMPLETED":
                log.info("✓ Scraping concluído")
            elif status == "FAILED":
                log.error("✗ Scraping falhou: %s", message)
                
        elif event_type == "import":
            if status == "STARTED":
                log.info("🔄 Importação iniciada")
            elif status == "COMPLETED":
                log.info("✓ Importação concluída")
            elif status == "FAILED":
                log.error("✗ Importação falhou: %s", message)
    
    def process_sync_event(self, event: Dict[str, Any]) -> None:
        """Processa um evento de sincronização DB→Redis."""
        self._reset_for_today()
        
        event_type = event.get("event_type", "")
        status = event.get("status", "")
        message = event.get("message", "")
        timestamp = event.get("timestamp", "")
        details_str = event.get("details", "{}")
        
        if event_type != "db_sync":
            return
        
        if status == "STARTED":
            log.info("🔄 Sincronização DB→Redis INICIADA às %s", timestamp)
            
        elif status == "COMPLETED":
            self.today_status["sync_status"] = "COMPLETED"
            self.today_status["sync_at"] = timestamp
            
            try:
                import json
                details = json.loads(details_str) if details_str else {}
                self.today_status["sync_health_events"] = details.get("health_events_sent", 0)
                self.today_status["sync_door_events"] = details.get("door_sent", 0)
            except Exception:
                pass
            
            log.info(
                "✅ Sincronização CONCLUÍDA: %d health_events, %d door",
                self.today_status["sync_health_events"],
                self.today_status["sync_door_events"]
            )
            self._publish_alert(
                "INFO", 
                "db_sync_completed",
                f"Sincronização concluída: {self.today_status['sync_health_events']} health_events, {self.today_status['sync_door_events']} door"
            )
            
        elif status == "FAILED":
            self.today_status["sync_status"] = "FAILED"
            log.error("❌ Sincronização FALHOU: %s", message)
            self._publish_alert("HIGH", "db_sync_failed", f"Sincronização falhou: {message}")
    
    def check_deadline(self) -> None:
        """Verifica se a rotina foi executada antes do deadline."""
        self._reset_for_today()
        
        now = datetime.now(timezone.utc)
        
        # Só verifica após o deadline
        if now.hour < DEADLINE_HOUR_UTC:
            return
        
        # Se já completou ou falhou, não precisa verificar
        if self.today_status["status"] in ("COMPLETED", "FAILED", "MISSED"):
            return
        
        # Rotina não foi executada até o deadline
        if self.today_status["status"] == "PENDING":
            self.today_status["status"] = "MISSED"
            log.error("⚠️ ALERTA: Rotina diária NÃO FOI EXECUTADA até às %02d:00 UTC!", DEADLINE_HOUR_UTC)
            self._publish_alert("CRITICAL", "daily_routine_missed", 
                               f"Rotina diária não foi executada até o horário limite ({DEADLINE_HOUR_UTC}:00 UTC)")
        
        # Rotina iniciou mas não terminou
        elif self.today_status["status"] == "STARTED":
            started = self.today_status["started_at"]
            log.warning("⚠️ Rotina diária iniciou às %s mas ainda não terminou", started)
            self._publish_alert("MEDIUM", "daily_routine_stuck", 
                               f"Rotina diária iniciou às {started} mas ainda não terminou")
    
    def check_sync_deadline(self) -> None:
        """Verifica se a sincronização ocorreu após a rotina diária."""
        self._reset_for_today()
        
        now = datetime.now(timezone.utc)
        deadline_hour = DEADLINE_HOUR_UTC + (SYNC_GRACE_PERIOD_MIN // 60)
        
        # Só verifica após deadline + grace period
        if now.hour < deadline_hour:
            return
        
        # Se sync já completou ou falhou, não precisa verificar
        if self.today_status["sync_status"] in ("COMPLETED", "FAILED"):
            return
        
        # Se a rotina completou mas sync não ocorreu
        if self.today_status["status"] == "COMPLETED" and self.today_status["sync_status"] == "PENDING":
            log.error("⚠️ ALERTA: Rotina completou mas sincronização não ocorreu!")
            self._publish_alert(
                "HIGH", 
                "db_sync_missing",
                f"Rotina diária completou mas sincronização DB→Redis não ocorreu"
            )
            self.today_status["sync_status"] = "MISSING"
    
    def _calculate_duration(self) -> str:
        """Calcula a duração da rotina."""
        if not self.today_status["started_at"] or not self.today_status["completed_at"]:
            return "N/A"
        
        try:
            start = datetime.fromisoformat(self.today_status["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.today_status["completed_at"].replace("Z", "+00:00"))
            duration = end - start
            
            minutes = int(duration.total_seconds() // 60)
            seconds = int(duration.total_seconds() % 60)
            return f"{minutes}m {seconds}s"
        except Exception:
            return "N/A"
    
    def _publish_alert(self, severity: str, alert_type: str, message: str) -> None:
        """Publica alerta no stream de alertas."""
        try:
            self.redis.xadd(
                ALERTS_STREAM,
                {
                    "severity": severity,
                    "type": alert_type,
                    "message": message,
                    "source": "caos.routine_monitor",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "date": self.today_status["date"],
                },
            )
            log.info("📢 Alerta publicado: [%s] %s", severity, alert_type)
        except Exception as exc:
            log.error("Erro ao publicar alerta: %s", exc)
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna o status atual."""
        self._reset_for_today()
        return self.today_status.copy()


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
    monitor = DailyRoutineMonitor(r)
    
    log.info("=" * 60)
    log.info("CAOS Daily Routine Monitor starting")
    log.info("  Routine Stream: %s (group: %s)", ROUTINE_STREAM, ROUTINE_GROUP)
    log.info("  Sync Stream: %s (group: %s)", SYNC_STREAM, SYNC_GROUP)
    log.info("  Expected: %02d:00 UTC, Deadline: %02d:00 UTC", EXPECTED_START_HOUR_UTC, DEADLINE_HOUR_UTC)
    log.info("  Sync grace period: %d min after deadline", SYNC_GRACE_PERIOD_MIN)
    log.info("  Redis: %s", REDIS_URL)
    log.info("=" * 60)
    
    ensure_group(r, ROUTINE_STREAM, ROUTINE_GROUP)
    ensure_group(r, SYNC_STREAM, SYNC_GROUP)
    
    last_deadline_check = 0
    
    while running:
        try:
            # Ler eventos do stream de rotina diária
            routine_entries = r.xreadgroup(
                groupname=ROUTINE_GROUP,
                consumername=CONSUMER_NAME,
                streams={ROUTINE_STREAM: ">"},
                count=10,
                block=READ_BLOCK_MS // 2,
            )
            
            if routine_entries:
                for stream, messages in routine_entries:
                    for message_id, fields in messages:
                        msg_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                        try:
                            event = parse_message(fields)
                            monitor.process_event(event)
                            r.xack(ROUTINE_STREAM, ROUTINE_GROUP, message_id)
                        except Exception as exc:
                            log.exception("Error processing routine %s: %s", msg_id_str, exc)
            
            # Ler eventos do stream de sincronização
            sync_entries = r.xreadgroup(
                groupname=SYNC_GROUP,
                consumername=CONSUMER_NAME,
                streams={SYNC_STREAM: ">"},
                count=10,
                block=READ_BLOCK_MS // 2,
            )
            
            if sync_entries:
                for stream, messages in sync_entries:
                    for message_id, fields in messages:
                        msg_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                        try:
                            event = parse_message(fields)
                            monitor.process_sync_event(event)
                            r.xack(SYNC_STREAM, SYNC_GROUP, message_id)
                        except Exception as exc:
                            log.exception("Error processing sync %s: %s", msg_id_str, exc)
            
            # Verificar deadlines periodicamente
            now = time.time()
            if now - last_deadline_check >= CHECK_INTERVAL_SEC:
                monitor.check_deadline()
                monitor.check_sync_deadline()
                last_deadline_check = now
                
        except redis.ResponseError as exc:
            log.error("Redis error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            log.exception("Unhandled error: %s", exc)
            time.sleep(1)
    
    log.info("Routine Monitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
