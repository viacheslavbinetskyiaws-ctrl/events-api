from pymongo import AsyncMongoClient

from streaming.config import get_streaming_settings

settings = get_streaming_settings()

# Same "constructed once at import time, reused for the process's whole
# lifetime" pattern as app/core/db.py's engine — AsyncMongoClient already
# pools its own connections internally, so there's nothing to gain from
# building a fresh one per call.
mongo_client = AsyncMongoClient(settings.mongo_uri)

# Milestone 13's properties projection: one document per event, _id set
# to the event's own id (see consumer.py's event-change handler). That's
# what makes replace_one(..., upsert=True) idempotent on replay without
# needing a separate dedup table the way tenant_account_changes needed
# source_lsn — there's nothing to dedup, writing the same document twice
# just overwrites it with itself.
event_properties_collection = mongo_client[settings.mongo_database]["event_properties"]
