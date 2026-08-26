"""Milestone 8's CDC consumer, extended in Milestone 10 to prove
replayability, and in Milestone 13 to also project events.properties into
MongoDB (see streaming/mongo.py) — a one-directional projection, not a
second system of record: Postgres stays authoritative, Mongo just mirrors
what already committed there.

Subscribes to both cdc.public.tenant_accounts and cdc.public.events,
dispatching each message by table name (parsed off its topic):
  - tenant_accounts: upserts into tenant_account_changes, keyed on
    source_lsn — unchanged from Milestone 10.
  - events: mirrors id/tenant_id/event_type/occurred_at/properties into
    event_properties_collection, keyed on the event's own id
    (_id=event_id). replace_one(upsert=True) is idempotent by
    construction here (see streaming/mongo.py's docstring for why this
    differs from the source_lsn approach above), so replay needs no
    separate dedup bookkeeping on this side.

Run with: uv run --extra streaming --extra mongodb python -m streaming.consumer
(needs the stack up and both connectors registered — see
docker/register-tenant-accounts-connector.sh and
docker/register-events-connector.sh)
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import cast

from confluent_kafka.aio import AIOConsumer
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult

from app.core.db import async_session_factory
from app.core.logging import configure_logging
from app.repositories.models import TenantAccountChangeORM
from streaming.config import get_streaming_settings
from streaming.mongo import event_properties_collection

logger = logging.getLogger(__name__)


async def handle_tenant_account_change_event(event: dict) -> None:
    source = event["source"]
    after = event.get("after")
    before = event.get("before")

    row = after if after is not None else before
    assert row is not None, f"change event missing both 'after' and 'before': {event}"
    tenant_account_id = row["id"]

    stmt = (
        insert(TenantAccountChangeORM)
        .values(
            source_lsn=source["lsn"],
            tenant_account_id=tenant_account_id,
            op=event["op"],
            before=before,
            after=after,
            captured_at=datetime.fromtimestamp(source["ts_ms"] / 1000, tz=UTC),
        )
        .on_conflict_do_nothing(index_elements=["source_lsn"])
    )

    async with async_session_factory() as session:
        # session.execute() is statically typed Result[Any] regardless of
        # statement shape; .rowcount only exists on CursorResult, which is
        # what SQLAlchemy actually returns at runtime for a DML statement
        # like this insert() — cast to make the two agree.
        result = cast(CursorResult, await session.execute(stmt))
        await session.commit()

    if result.rowcount:
        logger.info(
            "Recorded tenant_account change lsn=%s op=%s tenant_account_id=%s",
            source["lsn"],
            event["op"],
            tenant_account_id,
        )
    else:
        logger.info(
            "Duplicate tenant_account change lsn=%s already recorded, skipping", source["lsn"]
        )


async def handle_event_change_event(event: dict) -> None:
    op = event["op"]
    after = event.get("after")
    before = event.get("before")

    if op == "d":
        assert before is not None, f"delete event missing 'before': {event}"
        event_id = before["id"]
        await event_properties_collection.delete_one({"_id": event_id})
        logger.info("Deleted projected event_id=%s (source delete)", event_id)
        return

    assert after is not None, f"non-delete event missing 'after': {event}"
    event_id = after["id"]

    properties = after["properties"]
    # Confirmed empirically against a real captured message: with
    # schemas.enable=false and no JSON semantic type configured, Debezium/
    # pgoutput represents a Postgres JSONB column as its raw string form,
    # not a nested object — hence the parse. Still guarded by isinstance
    # rather than an unconditional json.loads, so this doesn't silently
    # break if a future Debezium/converter config ever changes that.
    if isinstance(properties, str):
        properties = json.loads(properties)

    doc = {
        "_id": event_id,
        "tenant_id": after["tenant_id"],
        "event_type": after["event_type"],
        "occurred_at": after["occurred_at"],
        "properties": properties,
    }

    await event_properties_collection.replace_one({"_id": event_id}, doc, upsert=True)
    logger.info("Projected event_id=%s op=%s into Mongo", event_id, op)


HANDLERS = {
    "tenant_accounts": handle_tenant_account_change_event,
    "events": handle_event_change_event,
}


async def run() -> None:
    settings = get_streaming_settings()
    configure_logging(settings.log_level)

    consumer = AIOConsumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.consumer_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    topics = [settings.tenant_accounts_topic, settings.events_topic]
    await consumer.subscribe(topics)
    logger.info("Subscribed to %s, waiting for change events...", topics)

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

                # Topic is "cdc.public.<table>" (topic.prefix=cdc from
                # both register-*-connector.sh scripts) — dispatch off the
                # table name itself, not which settings.*_topic matched,
                # so this generalizes if a third table joins later.
                table = msg.topic().rsplit(".", 1)[-1]
                handler = HANDLERS[table]

                event = json.loads(msg.value())
                await handler(event)
                await consumer.store_offsets(msg)
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(run())
