# services/atlas_supervisor/supervisor.py
"""
Atlas Supervisor - Orquestrador de supervisão para o Atlas Agent.

Implementa o padrão descrito no documento técnico CAOS:
- Camada rápida (Reflex): Heurísticas + IsolationForest
- Camada cognitiva (Judge): LLM com contexto RAG

Seguindo a arquitetura:
    Plan → Guardrail → Execute → Observe
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx

from shared.schemas import (
    AlertPriority,
    AnomalyType,
    AuditResult,
    CircuitBreakerState,
    CircuitBreakerStatus,
    CognitiveDecision,
    TelemetryEvent,
)
from services.atlas_supervisor.rules import AtlasRule, TelemetryThresholds

logger = logging.getLogger("caos.atlas_supervisor")


# ============================================================================
# CIRCUIT BREAKER - Proteção contra falhas em cascata
# ============================================================================

@dataclass
class CircuitBreaker:
    """Implementa o padrão Circuit Breaker conforme documentação CAOS.
    
    Estados:
    - CLOSED: Operação normal, contando falhas
    - OPEN: Bloqueando chamadas após threshold de falhas
    - HALF_OPEN: Testando se o serviço se recuperou
    """
    service_name: str
    failure_threshold: int = 5
    recovery_timeout: int = 30
    half_open_max_calls: int = 3
    
    _state: CircuitBreakerState = field(default=CircuitBreakerState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: Optional[datetime] = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            if self._state == CircuitBreakerState.OPEN:
                if self._should_attempt_recovery():
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._success_count = 0
            return self._state
    
    def _should_attempt_recovery(self) -> bool:
        if self._last_failure_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_max_calls:
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_count = 0
                    logger.info(f"Circuit [{self.service_name}] CLOSED - serviço recuperado")
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count = max(0, self._failure_count - 1)
    
    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now(timezone.utc)
            
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                logger.warning(f"Circuit [{self.service_name}] OPEN - falha durante recovery")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(f"Circuit [{self.service_name}] OPEN - threshold atingido")
    
    def allow_request(self) -> bool:
        return self.state != CircuitBreakerState.OPEN
    
    def get_status(self) -> CircuitBreakerStatus:
        return CircuitBreakerStatus(
            service_name=self.service_name,
            state=self.state,
            failure_count=self._failure_count,
            last_failure_time=self._last_failure_time,
            recovery_timeout_seconds=self.recovery_timeout,
        )


# ============================================================================
# RATE LIMITER - Token Bucket Algorithm
# ============================================================================

@dataclass
class RateLimiter:
    """Rate limiter usando Token Bucket (conforme equação do documento).
    
    B(t) = min(C, B(t-Δt) + Δt * R)
    """
    max_calls: int = 20
    window_seconds: int = 1
    
    _buckets: Dict[str, List[float]] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    def allow(self, key: str, max_calls: Optional[int] = None, window: Optional[int] = None) -> bool:
        now = time.time()
        limit = max_calls or self.max_calls
        window_sec = window or self.window_seconds
        
        with self._lock:
            hits = self._buckets.get(key, [])
            hits = [t for t in hits if t > now - window_sec]
            
            if len(hits) >= limit:
                self._buckets[key] = hits
                return False
            
            hits.append(now)
            self._buckets[key] = hits
            return True
    
    def get_remaining(self, key: str) -> int:
        now = time.time()
        with self._lock:
            hits = self._buckets.get(key, [])
            hits = [t for t in hits if t > now - self.window_seconds]
            return max(0, self.max_calls - len(hits))


# ============================================================================
# ANOMALY DETECTOR - Camada Reflex
# ============================================================================

class AnomalyDetector:
    """Detector de anomalias - Camada rápida do CAOS.
    
    Tenta usar modelo IsolationForest se disponível, 
    caso contrário usa heurísticas de fallback.
    """
    
    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.model_path = model_path or os.getenv("CAOS_MODEL_PATH")
        self._load_model()
    
    def _load_model(self) -> None:
        """Carrega modelo ML se disponível."""
        if not self.model_path:
            logger.info("Modelo ML não configurado, usando heurísticas")
            return
        
        try:
            import joblib
            self.model = joblib.load(self.model_path)
            logger.info(f"Modelo carregado de {self.model_path}")
        except Exception as e:
            logger.warning(f"Falha ao carregar modelo: {e}")
            self.model = None
    
    def detect(self, event: TelemetryEvent) -> AuditResult:
        """Detecta anomalias em um evento de telemetria."""
        start_time = time.perf_counter()
        
        if self.model is not None:
            result = self._detect_with_model(event)
        else:
            result = self._detect_with_heuristics(event)
        
        result.processing_time_ms = (time.perf_counter() - start_time) * 1000
        return result
    
    def _detect_with_model(self, event: TelemetryEvent) -> AuditResult:
        """Detecção usando IsolationForest."""
        try:
            import numpy as np
            import pandas as pd
            
            features = pd.DataFrame([[
                event.temperature_c or 0,
                event.avg_power_consumption_watt or 0,
                event.total_compressor_on_time_percent or 0,
            ]], columns=["temperatura", "energia", "compressor_on_pct"])
            
            prediction = int(self.model.predict(features)[0])
            score = float(self.model.decision_function(features)[0])
            
            if prediction == 1:
                return AuditResult(
                    status="OK",
                    reason="Dentro do padrão estatístico",
                    anomaly_score=score,
                    priority=AlertPriority.LOW,
                )
            
            anomaly_type = self._classify_anomaly(event)
            return AuditResult(
                status="ANOMALY",
                reason="Modelo detectou desvio estatístico",
                anomaly_type=anomaly_type,
                anomaly_score=score,
                priority=self._determine_priority(anomaly_type),
            )
            
        except Exception as e:
            logger.error(f"Erro no modelo ML: {e}")
            return self._detect_with_heuristics(event)
    
    def _detect_with_heuristics(self, event: TelemetryEvent) -> AuditResult:
        """Detecção usando regras heurísticas de fallback."""
        thresholds = TelemetryThresholds()
        
        # Verifica temperatura
        if event.temperature_c is not None:
            temp_min, temp_max = thresholds.get_temp_range_for_asset(event.asset_type)
            
            if event.temperature_c > thresholds.TEMP_CRITICAL_HIGH:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Temperatura crítica: {event.temperature_c}°C > {thresholds.TEMP_CRITICAL_HIGH}°C",
                    anomaly_type=AnomalyType.TEMPERATURE_HIGH,
                    priority=AlertPriority.CRITICAL,
                )
            
            if event.temperature_c < thresholds.TEMP_CRITICAL_LOW:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Temperatura muito baixa: {event.temperature_c}°C < {thresholds.TEMP_CRITICAL_LOW}°C",
                    anomaly_type=AnomalyType.TEMPERATURE_LOW,
                    priority=AlertPriority.HIGH,
                )
            
            if event.temperature_c > temp_max:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Temperatura acima do range: {event.temperature_c}°C > {temp_max}°C",
                    anomaly_type=AnomalyType.TEMPERATURE_HIGH,
                    priority=AlertPriority.MEDIUM,
                )
        
        # Verifica bateria
        if event.battery_level is not None:
            if event.battery_level <= thresholds.BATTERY_CRITICAL:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Bateria crítica: {event.battery_level}%",
                    anomaly_type=AnomalyType.BATTERY_CRITICAL,
                    priority=AlertPriority.HIGH,
                )
        
        # Verifica deslocamento (potencial roubo)
        if event.displacement_meter is not None:
            if event.displacement_meter > thresholds.DISPLACEMENT_CRITICAL_METERS:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Deslocamento crítico: {event.displacement_meter}m",
                    anomaly_type=AnomalyType.GPS_DISPLACEMENT,
                    priority=AlertPriority.CRITICAL,
                )
            
            if event.displacement_meter > thresholds.DISPLACEMENT_SUSPICIOUS_METERS:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Deslocamento suspeito: {event.displacement_meter}m",
                    anomaly_type=AnomalyType.GPS_DISPLACEMENT,
                    priority=AlertPriority.MEDIUM,
                )
        
        # Verifica compressor
        if event.total_compressor_on_time_percent is not None:
            if event.total_compressor_on_time_percent > thresholds.COMPRESSOR_ON_MAX_PERCENT:
                return AuditResult(
                    status="ANOMALY",
                    reason=f"Compressor sobrecarregado: {event.total_compressor_on_time_percent}%",
                    anomaly_type=AnomalyType.COMPRESSOR_FAULT,
                    priority=AlertPriority.HIGH,
                )
        
        return AuditResult(
            status="OK",
            reason="Telemetria dentro dos parâmetros esperados",
            priority=AlertPriority.LOW,
        )
    
    def _classify_anomaly(self, event: TelemetryEvent) -> AnomalyType:
        """Classifica o tipo de anomalia com base nos dados."""
        if event.temperature_c is not None:
            temp_min, temp_max = TelemetryThresholds.get_temp_range_for_asset(event.asset_type)
            if event.temperature_c > temp_max:
                return AnomalyType.TEMPERATURE_HIGH
            if event.temperature_c < temp_min:
                return AnomalyType.TEMPERATURE_LOW
        
        if event.displacement_meter and event.displacement_meter > 50:
            return AnomalyType.GPS_DISPLACEMENT
        
        if event.battery_level and event.battery_level < 20:
            return AnomalyType.BATTERY_CRITICAL
        
        return AnomalyType.SENSOR_ERROR
    
    def _determine_priority(self, anomaly_type: Optional[AnomalyType]) -> AlertPriority:
        """Determina prioridade com base no tipo de anomalia."""
        priority_map = {
            AnomalyType.TEMPERATURE_HIGH: AlertPriority.HIGH,
            AnomalyType.TEMPERATURE_LOW: AlertPriority.MEDIUM,
            AnomalyType.POWER_ANOMALY: AlertPriority.MEDIUM,
            AnomalyType.COMPRESSOR_FAULT: AlertPriority.HIGH,
            AnomalyType.GPS_DISPLACEMENT: AlertPriority.CRITICAL,
            AnomalyType.BATTERY_CRITICAL: AlertPriority.HIGH,
            AnomalyType.SENSOR_ERROR: AlertPriority.LOW,
            AnomalyType.NETWORK_LATENCY: AlertPriority.LOW,
        }
        return priority_map.get(anomaly_type, AlertPriority.MEDIUM)


# ============================================================================
# COGNITIVE JUDGE - Camada de decisão com LLM
# ============================================================================

class CognitiveJudge:
    """Juiz Cognitivo - Consulta LLM com contexto RAG para decisões complexas."""
    
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.cognitive_core_url = os.getenv(
            "COGNITIVE_CORE_URL", 
            "http://cognitive-core:8000/judge"
        )
        self.circuit_breaker = CircuitBreaker(service_name="cognitive_core")
    
    def judge(self, event: TelemetryEvent, audit_result: AuditResult) -> CognitiveDecision:
        """Julga um evento anômalo usando o LLM."""
        if not self.circuit_breaker.allow_request():
            logger.warning("Circuit breaker OPEN - usando decisão padrão")
            return CognitiveDecision(
                action="REVIEW",
                reason="Serviço cognitivo indisponível",
                confidence=0.0,
            )
        
        try:
            response = self._call_cognitive_core(event, audit_result)
            self.circuit_breaker.record_success()
            return response
        except Exception as e:
            logger.error(f"Erro ao chamar cognitive core: {e}")
            self.circuit_breaker.record_failure()
            return self._fallback_decision(event, audit_result)
    
    def _call_cognitive_core(self, event: TelemetryEvent, audit_result: AuditResult) -> CognitiveDecision:
        """Chama o serviço cognitive core via HTTP."""
        payload = {
            "telemetry": event.model_dump(),
            "audit_result": audit_result.model_dump(),
        }

        # Garantir que objetos datetime sejam serializados para ISO strings
        def _json_default(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            try:
                return str(obj)
            except Exception:
                return None

        serialized = json.dumps(payload, default=_json_default)

        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                self.cognitive_core_url,
                content=serialized,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            return CognitiveDecision(**data)
    
    def _fallback_decision(self, event: TelemetryEvent, audit_result: AuditResult) -> CognitiveDecision:
        """Decisão de fallback quando o LLM não está disponível."""
        if audit_result.priority == AlertPriority.CRITICAL:
            return CognitiveDecision(
                action="BLOCK",
                reason="Anomalia crítica detectada - LLM indisponível, bloqueio preventivo",
                confidence=0.5,
                suggested_actions=["Verificar manualmente", "Contatar suporte técnico"],
            )
        
        return CognitiveDecision(
            action="REVIEW",
            reason="LLM indisponível - evento marcado para revisão manual",
            confidence=0.3,
            suggested_actions=["Aguardar análise humana"],
        )


# ============================================================================
# ATLAS SUPERVISOR - Orquestrador principal
# ============================================================================

class AtlasSupervisor:
    """Supervisor principal do CAOS para o Atlas Agent.
    
    Coordena:
    - Rate limiting
    - Validação de payloads
    - Detecção de anomalias
    - Decisões cognitivas
    - Auditoria
    """
    
    def __init__(self):
        self.rate_limiter = RateLimiter()
        self.anomaly_detector = AnomalyDetector()
        self.cognitive_judge = CognitiveJudge()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        
        logger.info("Atlas Supervisor inicializado")
    
    def get_circuit_breaker(self, service_name: str) -> CircuitBreaker:
        """Obtém ou cria circuit breaker para um serviço."""
        if service_name not in self.circuit_breakers:
            self.circuit_breakers[service_name] = CircuitBreaker(service_name=service_name)
        return self.circuit_breakers[service_name]
    
    def check_rate_limit(self, key: str, rule: AtlasRule) -> bool:
        """Verifica se a requisição está dentro do rate limit."""
        return self.rate_limiter.allow(
            key=key,
            max_calls=rule.max_calls,
            window=rule.window_seconds,
        )
    
    def validate_payload(self, payload: Dict[str, Any], rule: AtlasRule) -> Optional[str]:
        """Valida payload contra o schema da regra."""
        if not rule.validate_json or not rule.schema:
            return None
        
        for field, expected_type in rule.schema.items():
            if field not in payload:
                return f"Campo obrigatório ausente: {field}"
            
            value = payload.get(field)
            if value is None:
                return f"Campo '{field}' não pode ser nulo"
            
            if isinstance(expected_type, tuple):
                if not isinstance(value, expected_type):
                    type_names = "/".join(t.__name__ for t in expected_type)
                    return f"Campo '{field}' deve ser {type_names}"
            elif not isinstance(value, expected_type):
                return f"Campo '{field}' deve ser {expected_type.__name__}"
        
        return None
    
    def audit_telemetry(self, event: TelemetryEvent) -> AuditResult:
        """Audita um evento de telemetria completo.
        
        Executa o pipeline:
        1. Detecção de anomalias (Reflex)
        2. Se anômalo, consulta juiz cognitivo
        3. Retorna resultado consolidado
        """
        # Camada Reflex
        result = self.anomaly_detector.detect(event)
        
        # Se anomalia detectada, consulta juiz cognitivo
        if result.status == "ANOMALY":
            cognitive_decision = self.cognitive_judge.judge(event, result)
            result.forwarded_to_llm = True
            result.cognitive_decision = cognitive_decision
            
            # Atualiza status baseado na decisão cognitiva
            if cognitive_decision.action == "BLOCK":
                result.status = "BLOCKED"
            elif cognitive_decision.action == "PASS":
                result.status = "OK"
                result.reason = f"Liberado pelo juiz: {cognitive_decision.reason}"
        
        return result
    
    def get_health_status(self) -> Dict[str, Any]:
        """Retorna status de saúde do supervisor."""
        return {
            "status": "healthy",
            "service": "atlas_supervisor",
            "circuit_breakers": {
                name: cb.get_status().model_dump()
                for name, cb in self.circuit_breakers.items()
            },
            "anomaly_detector": {
                "model_loaded": self.anomaly_detector.model is not None,
            },
        }


# Instância global do supervisor
_supervisor: Optional[AtlasSupervisor] = None


def get_supervisor() -> AtlasSupervisor:
    """Obtém a instância singleton do supervisor."""
    global _supervisor
    if _supervisor is None:
        _supervisor = AtlasSupervisor()
    return _supervisor
