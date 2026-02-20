"""
Cliente de Logs para Simuladores
=================================

Permite que os simuladores enviem logs para o CAOS via HTTP.
"""

import structlog
from typing import Any

import httpx


class RemoteLogHandler:
    """Handler que envia logs para o servidor CAOS via HTTP."""
    
    def __init__(self, caos_url: str = "http://localhost:8080"):
        self.caos_url = caos_url
        self.client = httpx.AsyncClient(timeout=2.0)
    
    def __call__(self, logger, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Intercepta logs e envia para CAOS."""
        try:
            # Enviar log para CAOS de forma assíncrona (fire-and-forget)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            if not loop.is_running():
                loop.run_until_complete(self._send_log(event_dict))
        except Exception:
            # Não falhar se não conseguir enviar
            pass
        
        return event_dict
    
    async def _send_log(self, event_dict: dict[str, Any]):
        """Envia log via HTTP POST."""
        try:
            await self.client.post(
                f"{self.caos_url}/v1/logs/external",
                json=event_dict,
                timeout=1.0
            )
        except Exception:
            pass


def configure_remote_logging(caos_url: str = "http://localhost:8080"):
    """Configura structlog para enviar logs para CAOS."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            RemoteLogHandler(caos_url),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
