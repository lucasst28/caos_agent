# sdk/atlas/client.py
"""
Cliente HTTP para comunicação com o CAOS Atlas Supervisor.

Uso no Atlas:
    from caos_sdk import CaosClient
    
    client = CaosClient()
    result = client.audit_telemetry({
        "asset_serial_number": "COOLER-001",
        "client": "coca-cola",
        "temperature_c": 15.5
    })
"""

import os
from typing import Any, Dict, List, Optional

import httpx

from shared.schemas import AuditResult, TelemetryEvent


class CaosClient:
    """Cliente para comunicação com o CAOS Atlas Supervisor."""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 5.0,
    ):
        self.base_url = base_url or os.getenv(
            "CAOS_SUPERVISOR_URL", 
            "http://caos-atlas-supervisor:8001"
        )
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
    
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client
    
    def close(self) -> None:
        """Fecha o cliente HTTP."""
        if self._client is not None:
            self._client.close()
            self._client = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def health(self) -> Dict[str, Any]:
        """Verifica saúde do serviço CAOS."""
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()
    
    def audit_telemetry(self, telemetry: Dict[str, Any]) -> AuditResult:
        """Audita um evento de telemetria.
        
        Args:
            telemetry: Dados de telemetria (asset_serial_number, client, temperature_c, etc)
            
        Returns:
            AuditResult com status da auditoria
        """
        response = self.client.post("/audit", json=telemetry)
        response.raise_for_status()
        return AuditResult(**response.json())
    
    def audit_telemetry_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Audita múltiplos eventos de telemetria.
        
        Args:
            events: Lista de eventos de telemetria
            
        Returns:
            Resultados agregados com estatísticas
        """
        response = self.client.post("/audit/batch", json={"events": events})
        response.raise_for_status()
        return response.json()
    
    def get_thresholds(self, asset_type: Optional[str] = None) -> Dict[str, Any]:
        """Obtém thresholds de detecção de anomalias.
        
        Args:
            asset_type: Tipo do ativo (freezer, cooler)
            
        Returns:
            Thresholds configurados
        """
        params = {}
        if asset_type:
            params["asset_type"] = asset_type
        
        response = self.client.get("/thresholds", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """Obtém regras CAOS configuradas para o Atlas."""
        response = self.client.get("/rules")
        response.raise_for_status()
        return response.json()
    
    def get_circuit_breakers(self) -> Dict[str, Any]:
        """Obtém status dos circuit breakers."""
        response = self.client.get("/circuit-breakers")
        response.raise_for_status()
        return response.json()
    
    def reset_circuit_breaker(self, service_name: str) -> Dict[str, Any]:
        """Reseta um circuit breaker.
        
        Args:
            service_name: Nome do serviço
            
        Returns:
            Status atualizado do circuit breaker
        """
        response = self.client.post(f"/circuit-breakers/{service_name}/reset")
        response.raise_for_status()
        return response.json()
    
    def simulate_anomaly(self, anomaly_type: str) -> Dict[str, Any]:
        """Simula uma anomalia para testes.
        
        Args:
            anomaly_type: Tipo de anomalia (temperature_high, gps_displacement, etc)
            
        Returns:
            Resultado da simulação
        """
        response = self.client.post(
            "/simulate/anomaly",
            params={"anomaly_type": anomaly_type}
        )
        response.raise_for_status()
        return response.json()


# Cliente global (singleton)
_client: Optional[CaosClient] = None


def get_client() -> CaosClient:
    """Obtém instância singleton do cliente CAOS."""
    global _client
    if _client is None:
        _client = CaosClient()
    return _client
