# sdk/atlas/middleware.py
"""
Middleware FastAPI para integração do Atlas com o CAOS.

Pode ser adicionado diretamente ao Atlas para supervisão local
ou configurado para encaminhar ao serviço CAOS remoto.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("caos.sdk.middleware")

# Configurações
CAOS_MODE = os.getenv("CAOS_MODE", "remote")  # "local" ou "remote"
CAOS_SUPERVISOR_URL = os.getenv("CAOS_SUPERVISOR_URL", "http://caos-atlas-supervisor:8001")


class CaosMiddleware(BaseHTTPMiddleware):
    """Middleware que integra o CAOS ao pipeline de requisições FastAPI.
    
    Modos de operação:
    - local: Usa rate limiting e validação embutidos (sem ML/LLM)
    - remote: Encaminha para o serviço CAOS Atlas Supervisor
    """
    
    def __init__(
        self, 
        app: FastAPI, 
        mode: str = None,
        supervisor_url: str = None,
    ):
        super().__init__(app)
        self.mode = mode or CAOS_MODE
        self.supervisor_url = supervisor_url or CAOS_SUPERVISOR_URL
        
        # Rate limiter local simples
        self._rate_buckets: Dict[str, list] = {}
        self._rate_lock = None
        
        logger.info(f"CAOS Middleware ativado em modo: {self.mode}")
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Intercepta a requisição e aplica políticas CAOS."""
        start_time = time.perf_counter()
        
        # Extrai identidade
        identity = self._extract_identity(request)
        rate_key = self._build_rate_key(identity, request)
        
        # Contexto de auditoria
        audit_context = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
            "method": request.method,
            "identity": identity,
        }
        
        # Rate limiting local simples
        if not self._check_rate_limit(rate_key):
            logger.warning(f"Rate limit excedido: {rate_key}")
            return self._rate_limit_response(audit_context)
        
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            
            # Headers de observabilidade
            response.headers["X-CAOS-Mode"] = self.mode
            response.headers["X-CAOS-Duration-Ms"] = f"{elapsed_ms:.2f}"
            
            return response
            
        except Exception as exc:
            logger.exception(f"Erro durante processamento: {exc}")
            raise
    
    def _extract_identity(self, request: Request) -> Dict[str, Any]:
        """Extrai identidade do requisitante."""
        api_key = request.headers.get("X-API-Key", "")
        masked_key = api_key[:8] + "..." if len(api_key) > 8 else None
        
        forwarded_for = request.headers.get("X-Forwarded-For")
        ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
            request.client.host if request.client else "unknown"
        )
        
        client = request.query_params.get("client")
        
        return {
            "ip": ip,
            "api_key": masked_key,
            "client": client,
        }
    
    def _build_rate_key(self, identity: Dict[str, Any], request: Request) -> str:
        """Constrói chave única para rate limiting."""
        if identity.get("api_key"):
            return f"apikey:{identity['api_key']}:{request.url.path}"
        if identity.get("client"):
            return f"client:{identity['client']}:{request.url.path}"
        return f"ip:{identity['ip']}:{request.url.path}"
    
    def _check_rate_limit(self, key: str, max_calls: int = 100, window: int = 1) -> bool:
        """Rate limiting simples em memória."""
        import threading
        
        if self._rate_lock is None:
            self._rate_lock = threading.Lock()
        
        now = time.time()
        
        with self._rate_lock:
            hits = self._rate_buckets.get(key, [])
            hits = [t for t in hits if t > now - window]
            
            if len(hits) >= max_calls:
                self._rate_buckets[key] = hits
                return False
            
            hits.append(now)
            self._rate_buckets[key] = hits
            return True
    
    def _rate_limit_response(self, audit_context: Dict[str, Any]) -> JSONResponse:
        """Resposta para rate limit excedido."""
        return JSONResponse(
            status_code=429,
            content={
                "status": "blocked",
                "reason": "Rate limit excedido",
                "retry_after_seconds": 1,
            },
            headers={
                "Retry-After": "1",
                "X-CAOS-Blocked": "rate_limit",
            },
        )


def setup_caos_middleware(
    app: FastAPI,
    mode: str = None,
    supervisor_url: str = None,
) -> None:
    """Configura o CAOS middleware na aplicação FastAPI.
    
    Args:
        app: Instância do FastAPI
        mode: Modo de operação ("local" ou "remote")
        supervisor_url: URL do serviço CAOS (se mode="remote")
    """
    enabled = os.getenv("CAOS_ENABLED", "true").lower() == "true"
    
    if not enabled:
        logger.info("CAOS desabilitado via CAOS_ENABLED=false")
        return
    
    app.add_middleware(
        CaosMiddleware,
        mode=mode,
        supervisor_url=supervisor_url,
    )
    
    # Adiciona endpoint de health do CAOS
    @app.get("/caos/health", tags=["caos"], include_in_schema=False)
    async def caos_health():
        """Health check do CAOS SDK."""
        return {
            "status": "ok",
            "mode": mode or CAOS_MODE,
            "supervisor_url": supervisor_url or CAOS_SUPERVISOR_URL,
        }
    
    logger.info("CAOS middleware configurado com sucesso")
