# shared/utils.py
"""
Utilitários compartilhados para o CAOS Framework.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def get_logger(name: str, level: str = None) -> logging.Logger:
    """Cria logger configurado para o CAOS.
    
    Args:
        name: Nome do logger
        level: Nível de log (DEBUG, INFO, WARNING, ERROR)
        
    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        
        # Formato JSON para produção, texto para desenvolvimento
        if os.getenv("CAOS_LOG_FORMAT", "text") == "json":
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
        
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    log_level = level or os.getenv("CAOS_LOG_LEVEL", "INFO")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    return logger


class JsonFormatter(logging.Formatter):
    """Formatter que produz logs em JSON estruturado."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Adiciona campos extras se presentes
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        return json.dumps(log_data, default=str)


def utc_now() -> datetime:
    """Retorna datetime UTC atual."""
    return datetime.now(timezone.utc)


def serialize_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Serializa datetime para ISO format."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Remove campos sensíveis de um payload para logging."""
    if not isinstance(payload, dict):
        return {}
    
    sensitive_keys = {
        "password", "token", "authorization", 
        "api_key", "secret", "credential"
    }
    
    return {
        k: ("***" if k.lower() in sensitive_keys else v) 
        for k, v in payload.items()
    }
