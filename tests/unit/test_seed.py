"""Unit tests for the seed script's deterministic event builder."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "k8s/overlays/aws-seed/scripts/seed.py"


def load_script():
    spec = importlib.util.spec_from_file_location("seed", SCRIPT)
    # Both are Optional in the stdlib's typing; assert so a moved script fails
    # here with a clear message and the type checker can narrow them.
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builds_the_requested_number_of_events_with_the_newest_at_now():
    module = load_script()
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    events = module.build_events(now, 12)

    assert len(events) == 12
    stamps = [datetime.fromisoformat(e["occurred_at"]) for e in events]
    assert max(stamps) == now  # freshness checks need at least one current event
    assert min(stamps) >= now - timedelta(days=3)


def test_events_use_the_api_field_names_and_more_than_one_event_type():
    module = load_script()
    events = module.build_events(datetime(2026, 9, 19, tzinfo=UTC), 12)

    assert {"event_type", "user_id", "occurred_at", "properties"} <= set(events[0])
    assert len({e["event_type"] for e in events}) > 1
