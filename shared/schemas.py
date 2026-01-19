# shared/schemas.py
"""
Schemas Pydantic compartilhados para o CAOS Framework.
Utilizados por todos os serviços do ecossistema.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AlertPriority(str, Enum):
    """Prioridades de alerta conforme Knowledge Graph."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnomalyType(str, Enum):
    """Tipos de anomalia detectáveis pelo CAOS."""
    TEMPERATURE_HIGH = "temperature_high"
    TEMPERATURE_LOW = "temperature_low"
    POWER_ANOMALY = "power_anomaly"
    COMPRESSOR_FAULT = "compressor_fault"
    GPS_DISPLACEMENT = "gps_displacement"
    BATTERY_CRITICAL = "battery_critical"
    SENSOR_ERROR = "sensor_error"
    NETWORK_LATENCY = "network_latency"


class TelemetryEvent(BaseModel):
    """Evento de telemetria recebido do Atlas.
    
    Representa os dados brutos de um ativo IoT (freezer, cooler, etc).
    """
    asset_serial_number: str = Field(..., description="Serial único do ativo")
    asset_type: Optional[str] = Field(None, description="Tipo do ativo (freezer, cooler)")
    outlet_code: Optional[str] = Field(None, description="Código do ponto de venda")
    client: str = Field(..., description="Identificador do cliente")
    
    # Métricas de temperatura
    temperature_c: Optional[float] = Field(None, ge=-50, le=100, description="Temperatura em Celsius")
    evaporator_temperature_c: Optional[float] = Field(None, description="Temp. evaporador")
    condensor_temperature_c: Optional[float] = Field(None, description="Temp. condensador")
    ambient_temperature_c: Optional[float] = Field(None, description="Temp. ambiente")
    
    # Métricas de energia
    cooler_voltage_v: Optional[float] = Field(None, ge=0, le=500, description="Voltagem do cooler")
    avg_power_consumption_watt: Optional[float] = Field(None, ge=0, description="Consumo médio em Watts")
    total_compressor_on_time_percent: Optional[float] = Field(None, ge=0, le=100, description="% tempo compressor ligado")
    
    # Métricas de bateria
    battery_level: Optional[int] = Field(None, ge=0, le=100, description="Nível de bateria")
    battery_status: Optional[str] = Field(None, description="Status da bateria")
    
    # Métricas de localização
    latitude: Optional[str] = Field(None, description="Latitude GPS")
    longitude: Optional[str] = Field(None, description="Longitude GPS")
    displacement_meter: Optional[float] = Field(None, description="Deslocamento em metros")
    
    # Metadados
    event_time: Optional[datetime] = Field(None, description="Timestamp do evento")
    smart_device_mac: Optional[str] = Field(None, description="MAC do smart device")
    
    @field_validator("temperature_c", mode="before")
    @classmethod
    def validate_temperature(cls, v):
        """Valida range de temperatura razoável."""
        if v is not None and (v < -50 or v > 100):
            raise ValueError(f"Temperatura fora do range físico possível: {v}°C")
        return v


class CognitiveDecision(BaseModel):
    """Decisão do Juiz Cognitivo (LLM com contexto RAG)."""
    action: Literal["BLOCK", "PASS", "REVIEW"] = Field(..., description="Ação determinada")
    reason: str = Field(..., description="Justificativa da decisão")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Confiança na decisão")
    suggested_actions: List[str] = Field(default_factory=list, description="Ações sugeridas")


class AuditResult(BaseModel):
    """Resultado da auditoria CAOS."""
    status: Literal["OK", "ANOMALY", "BLOCKED", "REVIEW"] = Field(..., description="Status da auditoria")
    reason: str = Field(..., description="Razão do status")
    anomaly_type: Optional[AnomalyType] = Field(None, description="Tipo de anomalia detectada")
    anomaly_score: Optional[float] = Field(None, description="Score do modelo de anomalia")
    priority: AlertPriority = Field(AlertPriority.LOW, description="Prioridade do alerta")
    forwarded_to_llm: bool = Field(False, description="Se foi encaminhado ao juiz cognitivo")
    cognitive_decision: Optional[CognitiveDecision] = Field(None, description="Decisão do LLM")
    processing_time_ms: Optional[float] = Field(None, description="Tempo de processamento")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RateLimitConfig(BaseModel):
    """Configuração de rate limiting por rota."""
    path_prefix: str
    methods: List[str] = ["GET", "POST"]
    max_calls: int = Field(20, ge=1, description="Máximo de chamadas")
    window_seconds: int = Field(1, ge=1, description="Janela de tempo em segundos")
    max_latency_ms: int = Field(5000, ge=100, description="Latência máxima antes de timeout")


class CaosRule(BaseModel):
    """Regra do CAOS para validação de endpoints."""
    name: str = Field(..., description="Nome identificador da regra")
    path_prefix: str = Field(..., description="Prefixo do path")
    methods: List[str] = Field(default_factory=lambda: ["GET", "POST"])
    validate_json: bool = Field(False, description="Se deve validar JSON body")
    schema: Optional[Dict[str, Any]] = Field(None, description="Schema para validação")
    rate_limit: Optional[RateLimitConfig] = Field(None, description="Configuração de rate limit")
    persist_audit: bool = Field(True, description="Se deve persistir auditoria")


class CircuitBreakerState(str, Enum):
    """Estados do Circuit Breaker - conforme documentação CAOS."""
    CLOSED = "closed"  # Operação normal
    OPEN = "open"      # Falhas detectadas, bloqueando chamadas
    HALF_OPEN = "half_open"  # Testando recuperação


class CircuitBreakerStatus(BaseModel):
    """Status do Circuit Breaker para um serviço."""
    service_name: str
    state: CircuitBreakerState
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    recovery_timeout_seconds: int = 30


class CognitiveRequest(BaseModel):
    """Request para o Cognitive Core."""
    data: TelemetryEvent
    anomaly_score: Optional[float] = None
