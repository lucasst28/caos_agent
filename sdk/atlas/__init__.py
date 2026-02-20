# sdk/atlas/__init__.py
"""SDK para integração do Atlas com o CAOS."""

from sdk.atlas.client import CaosClient
from sdk.atlas.middleware import CaosMiddleware, setup_caos_middleware

__all__ = [
    "CaosClient",
    "CaosMiddleware",
    "setup_caos_middleware",
]
