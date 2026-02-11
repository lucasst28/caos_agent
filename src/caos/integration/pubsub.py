"""Pub/Sub Consumer - Message handling from Sentinel and Oracle.

Handles incoming messages from:
- sentinel.alerts topic: Real-time anomaly alerts
- oracle.predictions topic: Proactive predictions
"""

import json
import structlog
from typing import Any, Callable, Awaitable
from datetime import datetime, timezone

from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.subscriber.message import Message

from caos.config import get_settings
from caos.schemas.enums import Severity, TriggerSource
from caos.schemas.trigger import TriggerContext, TriggerPayload

logger = structlog.get_logger(__name__)


class PubSubConsumer:
    """Consumer for Google Cloud Pub/Sub topics."""

    def __init__(
        self,
        subscription_id: str,
        handler: Callable[[TriggerPayload], Awaitable[Any]],
    ) -> None:
        """Initialize the consumer.
        
        Args:
            subscription_id: Full subscription path
            handler: Async function to process triggers
        """
        self.subscription_id = subscription_id
        self.handler = handler
        self._subscriber: pubsub_v1.SubscriberClient | None = None
        self._streaming_pull_future = None

    def _parse_message(self, message: Message) -> TriggerPayload | None:
        """Parse a Pub/Sub message into a TriggerPayload."""
        try:
            data = json.loads(message.data.decode("utf-8"))
            
            # Map Pub/Sub attributes to trigger fields
            source = data.get("source", message.attributes.get("source", "sentinel"))
            severity = data.get("severity", message.attributes.get("severity", "MEDIUM"))
            
            trigger = TriggerPayload(
                event_id=data.get("event_id", f"evt_{message.message_id}"),
                source=TriggerSource(source),
                timestamp=datetime.fromisoformat(
                    data.get("timestamp", datetime.now(timezone.utc).isoformat())
                ),
                severity=Severity(severity),
                payload=data.get("payload", {}),
                context=TriggerContext(
                    tenant_id=data.get("tenant_id", data.get("context", {}).get("tenant_id", "unknown")),
                    asset_id=data.get("asset_id", data.get("context", {}).get("asset_id", "unknown")),
                    location=data.get("location"),
                ),
                metric=data.get("metric"),
                value=data.get("value"),
                threshold_violated=data.get("threshold"),
            )
            
            return trigger
        
        except Exception as e:
            logger.error(
                "pubsub_parse_error",
                message_id=message.message_id,
                error=str(e),
            )
            return None

    async def process_message(self, message: Message) -> None:
        """Process a single message."""
        logger.info(
            "pubsub_message_received",
            message_id=message.message_id,
            subscription=self.subscription_id,
        )
        
        trigger = self._parse_message(message)
        
        if trigger is None:
            logger.warning("pubsub_message_invalid", message_id=message.message_id)
            message.nack()
            return
        
        try:
            await self.handler(trigger)
            message.ack()
            
            logger.info(
                "pubsub_message_processed",
                event_id=trigger.event_id,
                severity=trigger.severity.value,
            )
        
        except Exception as e:
            logger.error(
                "pubsub_handler_error",
                event_id=trigger.event_id,
                error=str(e),
            )
            message.nack()

    def start(self) -> None:
        """Start consuming messages (blocking).
        
        NOTE: This method uses asyncio.run_coroutine_threadsafe to safely
        dispatch async handlers from the synchronous Pub/Sub callback thread
        into the running event loop. Do NOT call this from the main async
        thread — run it via asyncio.to_thread() or in the lifespan startup.
        """
        import asyncio
        
        settings = get_settings()
        self._subscriber = pubsub_v1.SubscriberClient()
        
        subscription_path = self._subscriber.subscription_path(
            settings.google_cloud_project,
            self.subscription_id,
        )
        
        # Capture the running event loop so the sync callback can
        # schedule coroutines on it safely.
        loop = asyncio.get_event_loop()
        
        def callback(message: Message) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self.process_message(message), loop
            )
            # Block the callback thread until the coroutine finishes
            # so back-pressure is propagated to Pub/Sub.
            try:
                future.result(timeout=30)
            except Exception as exc:
                logger.error("pubsub_callback_error", error=str(exc))
                message.nack()
        
        self._streaming_pull_future = self._subscriber.subscribe(
            subscription_path,
            callback=callback,
        )
        
        logger.info(
            "pubsub_consumer_started",
            subscription=subscription_path,
        )
        
        try:
            self._streaming_pull_future.result()
        except Exception as e:
            self._streaming_pull_future.cancel()
            logger.error("pubsub_consumer_error", error=str(e))

    def stop(self) -> None:
        """Stop consuming messages."""
        if self._streaming_pull_future:
            self._streaming_pull_future.cancel()
            self._streaming_pull_future = None
        
        if self._subscriber:
            self._subscriber.close()
            self._subscriber = None
        
        logger.info("pubsub_consumer_stopped")


def create_sentinel_consumer(
    handler: Callable[[TriggerPayload], Awaitable[Any]],
) -> PubSubConsumer:
    """Create a consumer for Sentinel alerts."""
    settings = get_settings()
    return PubSubConsumer(
        subscription_id=f"{settings.pubsub_sentinel_alerts_topic}-sub",
        handler=handler,
    )


def create_oracle_consumer(
    handler: Callable[[TriggerPayload], Awaitable[Any]],
) -> PubSubConsumer:
    """Create a consumer for Oracle predictions."""
    settings = get_settings()
    return PubSubConsumer(
        subscription_id=f"{settings.pubsub_oracle_predictions_topic}-sub",
        handler=handler,
    )
