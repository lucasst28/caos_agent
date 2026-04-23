# services/atlas_supervisor/supervisor_simple.py
"""
Atlas Supervisor (Simplificado) - Supervisão leve para o Atlas Agent.

Funcionalidades:
- 🛡️ Rate Limiting (Token Bucket)
- ✅ Validação de payloads
- ⚡ Circuit Breaker
- 📝 Auditoria (logs estruturados)

Não inclui:
- Detecção de anomalias ML (IsolationForest)
- Decisão LLM (Cognitive Judge)
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import redis # Added for Redis support

from shared.schemas import (
    AlertPriority,
    AuditResult,
    CircuitBreakerState,
    CircuitBreakerStatus,
    TelemetryEvent,
)
from services.atlas_supervisor.rules import AtlasRule, ATLAS_RULES

logger = logging.getLogger("caos.atlas_supervisor")


# ============================================================================
# CIRCUIT BREAKER - Proteção contra falhas em cascata
# ============================================================================

@dataclass
class CircuitBreaker:
    """Circuit Breaker para proteger integrações externas.
    
    Estados:
    - CLOSED: Operação normal
    - OPEN: Bloqueando após threshold de falhas
    - HALF_OPEN: Testando recuperação
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
    redis_client: Optional[redis.Redis] = field(default=None, init=False) # Injectable

    def set_redis(self, r: redis.Redis):
        self.redis_client = r
    
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
                    logger.info(f"Circuit [{self.service_name}] CLOSED - recuperado")
                    self._publish_state_change("CLOSED", "Circuit recovered")
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count = max(0, self._failure_count - 1)

    def _publish_state_change(self, new_state: str, reason: str):
        if self.redis_client:
            try:
                self.redis_client.xadd(
                    "caos.alerts",
                    {
                        "severity": "INFO" if new_state == "CLOSED" else "HIGH",
                        "type": "circuit_breaker_changed",
                        "message": f"Circuit Breaker {self.service_name} changed to {new_state}: {reason}",
                        "source": "caos.supervisor",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to publish CB event: {e}")
    
    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now(timezone.utc)
            
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                logger.warning(f"Circuit [{self.service_name}] OPEN - falha em recovery")
                self._publish_state_change("OPEN", "Failure during recovery")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(f"Circuit [{self.service_name}] OPEN - threshold atingido")
                self._publish_state_change("OPEN", "Failure threshold reached")
    
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
    """Rate limiter usando Token Bucket.
    
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
# PAYLOAD VALIDATOR - Validação de schemas
# ============================================================================

class PayloadValidator:
    """Valida payloads contra schemas definidos nas regras."""
    
    def validate(self, payload: Dict[str, Any], rule: AtlasRule) -> Optional[str]:
        """Valida payload contra o schema da regra.
        
        Returns:
            None se válido, mensagem de erro se inválido.
        """
        if not rule.validate_json or not rule.schema:
            return None
        
        for field_name, expected_type in rule.schema.items():
            if field_name not in payload:
                return f"Campo obrigatório ausente: {field_name}"
            
            value = payload.get(field_name)
            if value is None:
                return f"Campo '{field_name}' não pode ser nulo"
            
            if isinstance(expected_type, tuple):
                if not isinstance(value, expected_type):
                    type_names = "/".join(t.__name__ for t in expected_type)
                    return f"Campo '{field_name}' deve ser {type_names}"
            elif not isinstance(value, expected_type):
                return f"Campo '{field_name}' deve ser {expected_type.__name__}"
        
        return None


# ============================================================================
# ATLAS SUPERVISOR (SIMPLIFICADO)
# ============================================================================

class AtlasSupervisor:
    """Supervisor simplificado do CAOS para o Atlas Agent.
    
    Coordena:
    - Rate limiting por endpoint/cliente
    - Validação de payloads
    - Circuit breakers para serviços externos
    - Auditoria estruturada
    """
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis_client = redis_client
        self.rate_limiter = RateLimiter()
        self.validator = PayloadValidator()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._start_time = time.time()
        self._stats_lock = threading.Lock()
        
        # Redis Keys
        self.STATS_KEY = "caos:stats:global"
        self.EPM_PREFIX = "caos:stats:epm:"  # caos:stats:epm:{timestamp_min}
        
        # Initialize stats in Redis if needed (only if connected)
        if self.redis_client:
            try:
                # Ensure keys exist with 0 if not present
                self.redis_client.hsetnx(self.STATS_KEY, "validated", 0)
                self.redis_client.hsetnx(self.STATS_KEY, "rejected", 0)
                self.redis_client.hsetnx(self.STATS_KEY, "total_requests", 0)
                self.redis_client.hsetnx(self.STATS_KEY, "total_telemetry", 0)
            except Exception as e:
                logger.error(f"Failed to init Redis stats: {e}")
        
        logger.info("Atlas Supervisor (simplificado) inicializado")
    
    def get_circuit_breaker(self, service_name: str) -> CircuitBreaker:
        """Obtém ou cria circuit breaker para um serviço."""
        if service_name not in self.circuit_breakers:
            cb = CircuitBreaker(service_name=service_name)
            if self.redis_client:
                cb.set_redis(self.redis_client)
            self.circuit_breakers[service_name] = cb
        return self.circuit_breakers[service_name]
    
    def check_rate_limit(self, key: str, rule: Optional[AtlasRule] = None) -> bool:
        """Verifica se a requisição está dentro do rate limit."""
        max_calls = rule.max_calls if rule else 100
        window = rule.window_seconds if rule else 60
        return self.rate_limiter.allow(key=key, max_calls=max_calls, window=window)
    
    def validate_payload(self, payload: Dict[str, Any], rule: AtlasRule) -> Optional[str]:
        """Valida payload contra o schema da regra."""
        return self.validator.validate(payload, rule)
    
    def find_rule(self, path: str, method: str = "GET") -> Optional[AtlasRule]:
        """Encontra a regra CAOS aplicável para um endpoint."""
        for rule in ATLAS_RULES:
            if path.startswith(rule.path_prefix):
                if not rule.methods or method.upper() in rule.methods:
                    return rule
        return None
    
    def audit_request(
        self,
        path: str,
        method: str,
        client: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditResult:
        """Audita uma requisição HTTP.
        
        Executa:
        1. Rate limiting
        2. Validação de payload (se aplicável)
        3. Registro de auditoria
        """
        start_time = time.perf_counter()
        
        # Encontra regra aplicável
        rule = self.find_rule(path, method)
        rate_key = f"{client}:{path}"
        
        # 1. Rate limiting
        if not self.check_rate_limit(rate_key, rule):
            self._record_event(is_validated=False)
            return AuditResult(
                status="BLOCKED",
                reason=f"Rate limit excedido para {rate_key}",
                priority=AlertPriority.MEDIUM,
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
            )
            self._publish_audit(
                "BLOCKED", f"Rate limit exceeded for {rate_key}", 
                "MEDIUM", "audit_ratelimit", client, path
            )
            return AuditResult(
                status="BLOCKED",
                reason=f"Rate limit excedido para {rate_key}",
                priority=AlertPriority.MEDIUM,
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        
        # 2. Validação de payload
        # 2. Validação de payload
        if rule and payload:
            validation_error = self.validate_payload(payload, rule)
            if validation_error:
                self._record_event(is_validated=False)
                self._publish_audit(
                    "BLOCKED", f"Validation failed: {validation_error}", 
                    "LOW", "audit_validation", client, path
                )
                return AuditResult(
                    status="BLOCKED",
                    reason=f"Validação falhou: {validation_error}",
                    priority=AlertPriority.LOW,
                    processing_time_ms=(time.perf_counter() - start_time) * 1000,
                )
        
        # 3. Sucesso - requisição permitida
        self._record_event(is_validated=True)
        return AuditResult(
            status="OK",
            reason="Requisição permitida",
            priority=AlertPriority.LOW,
            processing_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    
    def audit_telemetry(self, event: TelemetryEvent) -> AuditResult:
        """Audita um evento de telemetria (registro apenas, sem detecção ML).
        
        Apenas valida e registra o evento, sem análise de anomalias.
        """
        start_time = time.perf_counter()
        
        # Validação básica
        if not event.asset_serial_number:
            self._record_event(is_validated=False)
            return AuditResult(
                status="BLOCKED",
                reason="asset_serial_number é obrigatório",
                priority=AlertPriority.LOW,
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        
        if not event.client:
            self._record_event(is_validated=False)
            return AuditResult(
                status="BLOCKED",
                reason="client é obrigatório",
                priority=AlertPriority.LOW,
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        
        # Rate limiting por asset
        rate_key = f"telemetry:{event.client}:{event.asset_serial_number}"
        if not self.rate_limiter.allow(rate_key, max_calls=60, window=60):
            self._record_event(is_validated=False)
            return AuditResult(
                status="BLOCKED",
                reason=f"Rate limit de telemetria excedido para {event.asset_serial_number}",
                priority=AlertPriority.MEDIUM,
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
            )
        
        # Sucesso - telemetria registrada
        # Sucesso - telemetria registrada
        self._record_event(is_validated=True)
        return AuditResult(
            status="OK",
            reason="Telemetria registrada com sucesso",
            priority=AlertPriority.LOW,
            processing_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    
    def get_health_status(self) -> Dict[str, Any]:
        """Retorna status de saúde do supervisor."""
        return {
            "status": "healthy",
            "service": "atlas_supervisor_simple",
            "uptime_seconds": int(time.time() - self._start_time),
            "features": {
                "rate_limiting": True,
                "payload_validation": True,
                "circuit_breaker": True,
                "audit_logging": True,
                "anomaly_detection": False,
                "llm_judge": False,
            },
            "circuit_breakers": {
                name: cb.get_status().model_dump()
                for name, cb in self.circuit_breakers.items()
            },
        }
    
    def _record_event(self, is_validated: bool):
        """Records an event for stats tracking explicitly in Redis."""
        if not self.redis_client:
            return

        try:
            pipe = self.redis_client.pipeline()
            
            # Global counters
            pipe.hincrby(self.STATS_KEY, "total_requests", 1)
            if is_validated:
                pipe.hincrby(self.STATS_KEY, "validated", 1)
                pipe.hincrby(self.STATS_KEY, "total_telemetry", 1) # Count as telemetry too for now
            else:
                pipe.hincrby(self.STATS_KEY, "rejected", 1)
            
            # Events Per Minute (EPM)
            # Key: caos:stats:epm:{timestamp_minute} -> expiration 5 mins
            minute_ts = int(time.time() / 60)
            epm_key = f"{self.EPM_PREFIX}{minute_ts}"
            
            pipe.incr(epm_key)
            pipe.expire(epm_key, 300) # Keep for 5 minutes
            
            pipe.execute()
        except Exception as e:
            logger.error(f"Failed to record event stats in Redis: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Returns dashboard statistics from Redis."""
        stats = {
            "validated": 0,
            "rejected": 0,
            "total_requests": 0,
            "total_telemetry": 0,
            "events_per_minute": 0,
            "uptime_seconds": int(time.time() - self._start_time),
        }

        if self.redis_client:
            try:
                # 1. Get Global Stats
                global_stats = self.redis_client.hgetall(self.STATS_KEY)
                if global_stats:
                    stats.update({
                        "validated": int(global_stats.get("validated", 0)),
                        "rejected": int(global_stats.get("rejected", 0)),
                        "total_requests": int(global_stats.get("total_requests", 0)),
                        "total_telemetry": int(global_stats.get("total_telemetry", 0)),
                    })
                
                # 2. Calculate EPM (Sum of last minute)
                # Actually, EPM usually means "current rate".
                # Let's count current minute + previous minute average or just last minute.
                # For simplicity/responsiveness: Current minute count * (60/current_second) approx? 
                # Or just sum of last complete minute? 
                # Let's take the count of the CURRENT minute.
                current_min_ts = int(time.time() / 60)
                epm_key = f"{self.EPM_PREFIX}{current_min_ts}"
                current_epm = self.redis_client.get(epm_key)
                
                # If current minute just started, maybe look at previous too?
                # Let's just return current minute counter for "Real Time" feel.
                stats["events_per_minute"] = int(current_epm) if current_epm else 0
                
            except Exception as e:
                logger.error(f"Failed to fetch stats from Redis: {e}")
        
        return stats

    def _publish_audit(self, status: str, message: str, severity: str, type_code: str, client: str, path: str):
        """Publishes audit event to Redis."""
        if self.redis_client:
            try:
                self.redis_client.xadd(
                    "caos.alerts",
                    {
                        "severity": severity,
                        "type": type_code,
                        "message": message,
                        "source": "caos.supervisor",
                        "client": client,
                        "path": path,
                        "status": status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to publish audit event: {e}")


# Instância global do supervisor
_supervisor: Optional[AtlasSupervisor] = None


def get_supervisor(redis_client: Optional[redis.Redis] = None) -> AtlasSupervisor:
    """Obtém a instância singleton do supervisor."""
    global _supervisor
    if _supervisor is None:
        _supervisor = AtlasSupervisor(redis_client=redis_client)
    # If redis client is provided later
    if redis_client and not _supervisor.redis_client:
        _supervisor.redis_client = redis_client
    return _supervisor
