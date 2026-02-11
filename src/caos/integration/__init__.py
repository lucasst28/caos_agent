"""CAOS Integration - External service clients."""

from caos.integration.atlas import AtlasClient, get_atlas_client
from caos.integration.oracle import OracleClient, get_oracle_client
from caos.integration.care import CareDispatcher, get_care_dispatcher
from caos.integration.pubsub import (
    PubSubConsumer,
    create_sentinel_consumer,
    create_oracle_consumer,
)

__all__ = [
    "AtlasClient",
    "get_atlas_client",
    "OracleClient",
    "get_oracle_client",
    "CareDispatcher",
    "get_care_dispatcher",
    "PubSubConsumer",
    "create_sentinel_consumer",
    "create_oracle_consumer",
]
