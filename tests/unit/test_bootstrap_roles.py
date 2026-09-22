"""Unit tests for the Debezium-credential logic in the bootstrap-roles Job.
The script is not a package module (it lives beside its Job manifest), so it
is loaded by path. Only canonical_debezium_password is tested: the rest is
thin I/O against RDS/Secrets Manager that only a real cluster can exercise.
"""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / "k8s/overlays/aws-bootstrap/scripts/bootstrap_roles.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("bootstrap_roles", SCRIPT)
    # Both are Optional in the stdlib's typing; assert so a moved script fails
    # here with a clear message and the type checker can narrow them.
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSecrets:
    def __init__(self, stored: str | None):
        self.stored = stored
        self.put_calls: list[str] = []

    def describe_secret(self, SecretId):  # noqa: N803 - boto3 naming
        return {"VersionIdsToStages": {"v1": ["AWSCURRENT"]}} if self.stored else {}

    def get_random_password(self, **kwargs):
        assert kwargs["ExcludePunctuation"] is True
        return {"RandomPassword": "generated-password"}

    def put_secret_value(self, SecretId, SecretString):  # noqa: N803
        self.put_calls.append(SecretString)
        self.stored = SecretString

    def get_secret_value(self, SecretId):  # noqa: N803
        return {"SecretString": self.stored}


def test_generates_and_stores_a_password_on_the_first_run():
    module = load_script()
    secrets = FakeSecrets(stored=None)

    assert module.canonical_debezium_password(secrets) == "generated-password"
    assert secrets.put_calls == ["generated-password"]


def test_reuses_the_stored_password_without_writing_on_later_runs():
    module = load_script()
    secrets = FakeSecrets(stored="already-there")

    assert module.canonical_debezium_password(secrets) == "already-there"
    assert secrets.put_calls == []


class FakeConnection:
    """Stands in for an asyncpg.Connection: .fetch() returns canned rows,
    .execute() just records what it was called with."""

    def __init__(self, rows):
        self.rows = rows
        self.executed: list[tuple] = []

    async def fetch(self, query, *args):
        return self.rows

    async def execute(self, query, *args):
        self.executed.append((query, args))


async def test_drops_every_slot_rds_has_marked_lost():
    module = load_script()
    conn = FakeConnection(
        rows=[{"slot_name": "debezium_events"}, {"slot_name": "debezium_tenant_accounts"}]
    )

    await module.clear_lost_replication_slots(conn)

    dropped = [args[0] for _query, args in conn.executed]
    assert dropped == ["debezium_events", "debezium_tenant_accounts"]


async def test_does_nothing_when_no_slot_is_lost():
    module = load_script()
    conn = FakeConnection(rows=[])

    await module.clear_lost_replication_slots(conn)

    assert conn.executed == []
