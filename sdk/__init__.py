# CAOS SDK for Atlas Integration
"""
SDK que permite ao Atlas integrar-se com o CAOS.

Pode ser importado diretamente no Atlas ou usado como cliente HTTP.
"""

from sdk.atlas.client import CaosClient
from sdk.atlas.middleware import CaosMiddleware, setup_caos_middleware

__all__ = [
    "CaosClient",
    "CaosMiddleware",
    "setup_caos_middleware",
]
