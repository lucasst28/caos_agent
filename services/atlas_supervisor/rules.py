# services/atlas_supervisor/rules.py
"""
Regras específicas do Atlas para o CAOS.

Define validações, rate limits e schemas para cada endpoint crítico.
Baseado no documento técnico CAOS Framework v8.0.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class AtlasRule:
    """Regra de validação para endpoints Atlas."""
    name: str
    path_prefix: str
    methods: List[str]
    validate_json: bool = False
    schema: Optional[Dict[str, type]] = None
    max_calls: int = 20
    window_seconds: int = 1
    max_latency_ms: int = 5000
    persist_audit: bool = True
    requires_client: bool = True


# ============================================================================
# REGRAS POR DOMÍNIO DO ATLAS
# ============================================================================

ATLAS_RULES: List[AtlasRule] = [
    # -------------------------------------------------------------------------
    # ASSETS - Endpoints de ativos (freezers, coolers, etc)
    # -------------------------------------------------------------------------
    AtlasRule(
        name="assets_list",
        path_prefix="/assets/",
        methods=["GET"],
        max_calls=60,
        window_seconds=1,
        max_latency_ms=4000,
    ),
    AtlasRule(
        name="assets_create",
        path_prefix="/assets/",
        methods=["POST"],
        validate_json=True,
        schema={
            "oem_serial_number": str,
            "client": str,
        },
        max_calls=10,
        window_seconds=1,
        max_latency_ms=5000,
    ),
    AtlasRule(
        name="assets_detail",
        path_prefix="/assets/detail",
        methods=["GET"],
        max_calls=100,
        window_seconds=1,
        max_latency_ms=3000,
    ),
    AtlasRule(
        name="assets_health",
        path_prefix="/assets/health",
        methods=["GET"],
        max_calls=80,
        window_seconds=1,
        max_latency_ms=4000,
    ),
    AtlasRule(
        name="assets_movements",
        path_prefix="/assets/movements",
        methods=["GET"],
        max_calls=50,
        window_seconds=1,
        max_latency_ms=5000,
    ),

    # -------------------------------------------------------------------------
    # ALERTS - Endpoints de alertas
    # -------------------------------------------------------------------------
    AtlasRule(
        name="alerts_list",
        path_prefix="/alerts/",
        methods=["GET"],
        max_calls=40,
        window_seconds=1,
        max_latency_ms=4000,
    ),
    AtlasRule(
        name="alerts_create",
        path_prefix="/alerts/",
        methods=["POST"],
        validate_json=True,
        schema={
            "alert_type": str,
            "asset_serial_number": str,
            "client": str,
        },
        max_calls=20,
        window_seconds=1,
        max_latency_ms=3000,
    ),

    # -------------------------------------------------------------------------
    # OUTLETS - Pontos de venda
    # -------------------------------------------------------------------------
    AtlasRule(
        name="outlets_list",
        path_prefix="/outlets/",
        methods=["GET"],
        max_calls=50,
        window_seconds=1,
        max_latency_ms=4000,
    ),
    AtlasRule(
        name="outlets_nearby",
        path_prefix="/outlets/nearby",
        methods=["POST"],
        validate_json=True,
        schema={
            "latitude": (float, str),
            "longitude": (float, str),
        },
        max_calls=30,
        window_seconds=1,
        max_latency_ms=5000,
    ),

    # -------------------------------------------------------------------------
    # SMART DEVICES - Dispositivos IoT
    # -------------------------------------------------------------------------
    AtlasRule(
        name="smartdevices_list",
        path_prefix="/smartdevices/",
        methods=["GET"],
        max_calls=60,
        window_seconds=1,
        max_latency_ms=4000,
    ),
    AtlasRule(
        name="smartdevices_telemetry",
        path_prefix="/smartdevices/telemetry",
        methods=["POST"],
        validate_json=True,
        schema={
            "mac_address": str,
        },
        max_calls=100,
        window_seconds=1,
        max_latency_ms=2000,
    ),

    # -------------------------------------------------------------------------
    # DASHBOARD - Métricas agregadas
    # -------------------------------------------------------------------------
    AtlasRule(
        name="dashboard_stats",
        path_prefix="/dashboard/stats",
        methods=["GET"],
        max_calls=30,
        window_seconds=1,
        max_latency_ms=6000,
    ),
    AtlasRule(
        name="dashboard_technicians",
        path_prefix="/dashboard/technicians",
        methods=["GET"],
        max_calls=20,
        window_seconds=1,
        max_latency_ms=5000,
    ),

    # -------------------------------------------------------------------------
    # TRACKING - Rastreamento de movimentações
    # -------------------------------------------------------------------------
    AtlasRule(
        name="tracking_events",
        path_prefix="/tracking/",
        methods=["GET"],
        max_calls=40,
        window_seconds=1,
        max_latency_ms=5000,
    ),

    # -------------------------------------------------------------------------
    # USERS - Gestão de usuários
    # -------------------------------------------------------------------------
    AtlasRule(
        name="users_list",
        path_prefix="/users/",
        methods=["GET"],
        max_calls=30,
        window_seconds=1,
        max_latency_ms=4000,
    ),

    # -------------------------------------------------------------------------
    # INVENTORY - Operações de inventário
    # -------------------------------------------------------------------------
    AtlasRule(
        name="inventory_operations",
        path_prefix="/inventory/",
        methods=["GET", "POST"],
        validate_json=True,
        max_calls=20,
        window_seconds=1,
        max_latency_ms=5000,
    ),

    # -------------------------------------------------------------------------
    # FALLBACK - Regra padrão para rotas não mapeadas
    # -------------------------------------------------------------------------
    AtlasRule(
        name="default",
        path_prefix="/",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        max_calls=100,
        window_seconds=1,
        max_latency_ms=10000,
        persist_audit=False,
    ),
]


def get_rule_by_name(name: str) -> Optional[AtlasRule]:
    """Busca regra por nome."""
    for rule in ATLAS_RULES:
        if rule.name == name:
            return rule
    return None


def get_rule_for_path(path: str, method: str) -> AtlasRule:
    """Resolve a regra CAOS aplicável para um path/method.
    
    Args:
        path: Path da requisição
        method: Método HTTP
        
    Returns:
        AtlasRule aplicável
    """
    method = method.upper()
    
    # Remove o root_path (/atlas) se presente
    if path.startswith("/atlas"):
        path = path[6:]
    
    # Busca regra mais específica primeiro (path mais longo)
    matching_rules = [
        rule for rule in ATLAS_RULES
        if path.startswith(rule.path_prefix) and method in rule.methods
    ]
    
    if matching_rules:
        return max(matching_rules, key=lambda r: len(r.path_prefix))
    
    return next(r for r in ATLAS_RULES if r.name == "default")


# ============================================================================
# THRESHOLDS DE TELEMETRIA (Heurísticas de fallback)
# ============================================================================

class TelemetryThresholds:
    """Limiares para detecção de anomalias quando modelo ML não está disponível."""
    
    # Temperatura
    TEMP_MIN_FREEZER: float = -25.0
    TEMP_MAX_FREEZER: float = -12.0
    TEMP_MIN_COOLER: float = 0.0
    TEMP_MAX_COOLER: float = 7.0
    TEMP_CRITICAL_HIGH: float = 15.0
    TEMP_CRITICAL_LOW: float = -30.0
    
    # Energia
    VOLTAGE_MIN: float = 100.0
    VOLTAGE_MAX: float = 260.0
    POWER_MAX_WATT: float = 500.0
    
    # Compressor
    COMPRESSOR_ON_MAX_PERCENT: float = 95.0
    COMPRESSOR_ON_MIN_PERCENT: float = 5.0
    
    # Bateria
    BATTERY_CRITICAL: int = 10
    BATTERY_LOW: int = 25
    
    # GPS
    DISPLACEMENT_SUSPICIOUS_METERS: float = 100.0
    DISPLACEMENT_CRITICAL_METERS: float = 500.0
    
    @classmethod
    def get_temp_range_for_asset(cls, asset_type: Optional[str]) -> Tuple[float, float]:
        """Retorna range de temperatura aceitável por tipo de ativo."""
        if asset_type and "freezer" in asset_type.lower():
            return (cls.TEMP_MIN_FREEZER, cls.TEMP_MAX_FREEZER)
        return (cls.TEMP_MIN_COOLER, cls.TEMP_MAX_COOLER)
