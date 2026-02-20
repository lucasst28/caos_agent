# services/atlas_supervisor/telemetry_consumer.py
"""
Consumer de telemetria do Atlas.

Processa eventos do Redis Stream e aplica supervisão CAOS:
- Validação de integridade
- Detecção de anomalias
- Encaminhamento para o juiz cognitivo quando necessário
- Geração de alertas
"""

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from redis import Redis
from redis.exceptions import RedisError

from shared.schemas import AlertPriority, AuditResult, TelemetryEvent
from services.atlas_supervisor.supervisor import get_supervisor

logger = logging.getLogger("caos.telemetry_consumer")


class TelemetryConsumer:
    """Consumer de telemetria que processa eventos do Redis Stream.
    
    Fluxo:
    1. Lê eventos do stream `telemetry.received`
    2. Valida e parseia como TelemetryEvent
    3. Executa auditoria CAOS
    4. Publica resultados no stream `telemetry.audited`
    5. Se anomalia crítica, publica em `alerts.generated`
    """
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        input_stream: Optional[str] = None,
        output_stream: Optional[str] = None,
        alert_stream: Optional[str] = None,
        consumer_group: Optional[str] = None,
        consumer_name: Optional[str] = None,
    ):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.input_stream = input_stream or os.getenv("TELEMETRY_INPUT_STREAM", "telemetry.received")
        self.output_stream = output_stream or os.getenv("TELEMETRY_OUTPUT_STREAM", "telemetry.audited")
        self.alert_stream = alert_stream or os.getenv("ALERT_STREAM", "alerts.generated")
        self.consumer_group = consumer_group or os.getenv("CAOS_CONSUMER_GROUP", "caos-auditors")
        self.consumer_name = consumer_name or os.getenv("CAOS_CONSUMER_NAME", f"caos-{os.getpid()}")
        
        self.redis: Optional[Redis] = None
        self.supervisor = get_supervisor()
        self.running = False
        
        # Métricas
        self.processed_count = 0
        self.anomaly_count = 0
        self.error_count = 0
    
    def connect(self) -> bool:
        """Conecta ao Redis e configura consumer group."""
        try:
            self.redis = Redis.from_url(self.redis_url, decode_responses=True)
            self.redis.ping()
            
            try:
                self.redis.xgroup_create(
                    self.input_stream,
                    self.consumer_group,
                    id="0",
                    mkstream=True,
                )
                logger.info(f"Consumer group '{self.consumer_group}' criado")
            except RedisError as e:
                if "BUSYGROUP" not in str(e):
                    raise
                logger.debug(f"Consumer group '{self.consumer_group}' já existe")
            
            logger.info(f"Conectado ao Redis: {self.redis_url}")
            return True
            
        except RedisError as e:
            logger.error(f"Falha ao conectar ao Redis: {e}")
            return False
    
    def disconnect(self) -> None:
        """Desconecta do Redis."""
        if self.redis:
            self.redis.close()
            self.redis = None
            logger.info("Desconectado do Redis")
    
    def _parse_telemetry(self, data: Dict[str, str]) -> Optional[TelemetryEvent]:
        """Parseia dados do stream para TelemetryEvent."""
        try:
            if "payload" in data:
                payload = json.loads(data["payload"])
            else:
                payload = data
            
            return TelemetryEvent(
                asset_serial_number=payload.get("asset_serial_number", payload.get("he_asset_serial_number", "")),
                asset_type=payload.get("asset_type", payload.get("he_asset_type")),
                outlet_code=payload.get("outlet_code", payload.get("he_outlet_code")),
                client=payload.get("client", payload.get("he_client", "")),
                temperature_c=self._safe_float(payload.get("temperature_c", payload.get("he_temperature_c"))),
                evaporator_temperature_c=self._safe_float(payload.get("evaporator_temperature_c")),
                condensor_temperature_c=self._safe_float(payload.get("condensor_temperature_c")),
                ambient_temperature_c=self._safe_float(payload.get("ambient_temperature_c")),
                cooler_voltage_v=self._safe_float(payload.get("cooler_voltage_v")),
                avg_power_consumption_watt=self._safe_float(payload.get("avg_power_consumption_watt")),
                total_compressor_on_time_percent=self._safe_float(payload.get("total_compressor_on_time_percent")),
                battery_level=self._safe_int(payload.get("battery")),
                battery_status=payload.get("battery_status"),
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
                displacement_meter=self._safe_float(payload.get("displacement_meter")),
                smart_device_mac=payload.get("smart_device_mac"),
            )
        except Exception as e:
            logger.error(f"Erro ao parsear telemetria: {e}")
            return None
    
    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    
    def _process_event(self, message_id: str, data: Dict[str, str]) -> None:
        """Processa um evento de telemetria."""
        try:
            event = self._parse_telemetry(data)
            if not event:
                logger.warning(f"Evento inválido ignorado: {message_id}")
                self.error_count += 1
                return
            
            result = self.supervisor.audit_telemetry(event)
            self.processed_count += 1
            
            self._publish_result(event, result)
            
            if result.status in ["ANOMALY", "BLOCKED"]:
                self.anomaly_count += 1
                
                if result.priority in [AlertPriority.CRITICAL, AlertPriority.HIGH]:
                    self._publish_alert(event, result)
            
            logger.debug(
                f"Evento processado: {event.asset_serial_number} - "
                f"Status: {result.status} - Priority: {result.priority}"
            )
            
        except Exception as e:
            logger.exception(f"Erro ao processar evento {message_id}: {e}")
            self.error_count += 1
    
    def _publish_result(self, event: TelemetryEvent, result: AuditResult) -> None:
        """Publica resultado da auditoria no stream de saída."""
        if not self.redis:
            return
        
        try:
            payload = {
                "asset_serial_number": event.asset_serial_number,
                "client": event.client,
                "status": result.status,
                "reason": result.reason,
                "priority": result.priority.value if result.priority else "low",
                "anomaly_type": result.anomaly_type.value if result.anomaly_type else None,
                "anomaly_score": result.anomaly_score,
                "forwarded_to_llm": result.forwarded_to_llm,
                "processing_time_ms": result.processing_time_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            if result.cognitive_decision:
                payload["cognitive_action"] = result.cognitive_decision.action
                payload["cognitive_reason"] = result.cognitive_decision.reason
            
            self.redis.xadd(self.output_stream, {"payload": json.dumps(payload)})
            
        except RedisError as e:
            logger.error(f"Erro ao publicar resultado: {e}")
    
    def _publish_alert(self, event: TelemetryEvent, result: AuditResult) -> None:
        """Publica alerta crítico no stream de alertas."""
        if not self.redis:
            return
        
        try:
            alert = {
                "alert_type": result.anomaly_type.value if result.anomaly_type else "unknown",
                "asset_serial_number": event.asset_serial_number,
                "outlet_code": event.outlet_code,
                "client": event.client,
                "priority": result.priority.value,
                "reason": result.reason,
                "source": "caos_atlas_supervisor",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            if event.temperature_c is not None:
                alert["temperature_c"] = event.temperature_c
            if event.battery_level is not None:
                alert["battery_level"] = event.battery_level
            if event.displacement_meter is not None:
                alert["displacement_meter"] = event.displacement_meter
            
            self.redis.xadd(self.alert_stream, {"payload": json.dumps(alert)})
            logger.info(f"Alerta gerado: {alert['alert_type']} - {event.asset_serial_number}")
            
        except RedisError as e:
            logger.error(f"Erro ao publicar alerta: {e}")
    
    def run(self, batch_size: int = 10, block_ms: int = 5000) -> None:
        """Executa o consumer loop."""
        if not self.connect():
            logger.error("Falha ao conectar - abortando")
            return
        
        self.running = True
        logger.info(f"Consumer iniciado: {self.consumer_name}")
        
        def signal_handler(signum, frame):
            logger.info("Sinal de parada recebido")
            self.running = False
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        try:
            while self.running:
                try:
                    messages = self.redis.xreadgroup(
                        groupname=self.consumer_group,
                        consumername=self.consumer_name,
                        streams={self.input_stream: ">"},
                        count=batch_size,
                        block=block_ms,
                    )
                    
                    if not messages:
                        continue
                    
                    for stream_name, stream_messages in messages:
                        for message_id, data in stream_messages:
                            self._process_event(message_id, data)
                            self.redis.xack(self.input_stream, self.consumer_group, message_id)
                    
                except RedisError as e:
                    logger.error(f"Erro Redis: {e}")
                    if not self.connect():
                        logger.error("Falha na reconexão - aguardando 5s")
                        asyncio.get_event_loop().run_until_complete(asyncio.sleep(5))
                        
        finally:
            self.disconnect()
            logger.info(
                f"Consumer finalizado - Processados: {self.processed_count}, "
                f"Anomalias: {self.anomaly_count}, Erros: {self.error_count}"
            )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Retorna métricas do consumer."""
        return {
            "processed_count": self.processed_count,
            "anomaly_count": self.anomaly_count,
            "error_count": self.error_count,
            "anomaly_rate": (
                self.anomaly_count / self.processed_count
                if self.processed_count > 0
                else 0
            ),
            "running": self.running,
        }


def main():
    """Entry point para execução standalone do consumer."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    consumer = TelemetryConsumer()
    consumer.run()


if __name__ == "__main__":
    main()
