"""Seed data for a freshly recreated cluster. Runs until it has succeeded once
per cluster lifetime; re-running after a partial failure adds a few extra demo
tenants, which is acceptable for disposable seed data.

Goes through the app's own HTTP API instead of writing SQL, so the seed
exercises tenant creation, RLS (X-Tenant-ID -> app.current_tenant) and the
whole CDC path (Postgres -> Debezium -> Kafka -> Mongo projection, realtime
SSE, BigQuery sinks). Standard library only.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

BASE_URL = os.environ.get("SEED_API_URL", "http://events-api.events-api.svc.cluster.local:8000")
TENANTS = [("Acme Analytics", "pro"), ("Globex Corp", "free")]
EVENT_TYPES = ["page_view", "signup", "purchase"]
EVENTS_PER_TENANT = 12


def build_events(now: datetime, count: int) -> list[dict]:
    """Deterministic events spread over the last three days; index 0 is `now`
    so freshness checks always see a current event."""
    events = []
    for i in range(count):
        events.append(
            {
                "event_type": EVENT_TYPES[i % len(EVENT_TYPES)],
                "user_id": f"user-{i % 4 + 1}",
                "occurred_at": (now - timedelta(days=i % 3, hours=i)).isoformat(),
                "properties": {"source": "seed", "sequence": i},
            }
        )
    events[0]["occurred_at"] = now.isoformat()
    return events


def request(method: str, path: str, body=None, tenant_id: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def wait_until_ready(timeout_seconds: int = 600) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            status, _ = request("GET", "/readyz")
            if status == 200:
                return
        except urllib.error.URLError, OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit("the API never became ready")
        time.sleep(5)


def main() -> None:
    wait_until_ready()
    now = datetime.now(UTC)
    for name, plan_tier in TENANTS:
        _, tenant = request("POST", "/admin/tenants", {"name": name, "plan_tier": plan_tier})
        for event in build_events(now, EVENTS_PER_TENANT):
            request("POST", "/events", event, tenant_id=tenant["id"])
        print(f"seeded tenant {name} ({tenant['id']})")


if __name__ == "__main__":
    main()
