"""Milestone 8's CDC consumer: reads Debezium's change events off
cdc.public.tenant_accounts and logs each one — proves the pipeline
reacts to changes from any writer (API, raw SQL, anything), not just
this app's own code path.

Run with: uv run --extra streaming python -m streaming.consumer
(needs the stack up and the connector registered — see
docker/register-tenant-accounts-connector.sh)
"""

import asyncio
import json
import logging

from confluent_kafka.aio import AIOConsumer

from app.core.logging import configure_logging
from streaming.config import get_streaming_settings

logger = logging.getLogger(__name__)


async def handle_change_event(event: dict) -> None:
    logger.info(
        "tenant_accounts change captured op=%s before=%s after=%s",
        event.get("op"),
        event.get("before"),
        event.get("after"),
    )


async def run() -> None:
    settings = get_streaming_settings()
    configure_logging(settings.log_level)

    consumer = AIOConsumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.consumer_group_id,
            "auto.offset.reset": "earliest",
        }
    )
    await consumer.subscribe([settings.topic])
    logger.info("Subscribed to %s, waiting for change events...", settings.topic)

    try:
        while True:
            messages = await consumer.consume(num_messages=100, timeout=1.0)
            for msg in messages:
                if msg.error():
                    logger.error("Consumer error: %s", msg.error())
                    continue
                if msg.value() is None:
                    # Debezium tombstone: a null-value message following a
                    # delete, for Kafka's own log compaction. Not a change
                    # to react to — the delete itself already arrived as a
                    # prior message with op="d".
                    continue

                event = json.loads(msg.value())
                await handle_change_event(event)
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(run())
