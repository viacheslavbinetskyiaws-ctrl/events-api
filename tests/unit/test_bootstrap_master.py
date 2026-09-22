"""Unit tests for the idempotent-connect logic in the bootstrap-master Job.

A freshly created RDS instance's `events` role can only authenticate with the
master password until `GRANT rds_iam TO events` has run once; after that, AWS
requires IAM auth and rejects the password (confirmed live 2026-09-22:
`InvalidAuthorizationSpecificationError: PAM authentication failed for user
"events"` on a re-run against an already-granted instance). Since this Job is
deleted and recreated on every `cluster-up`, it must work either way.
"""

import importlib.util
from pathlib import Path

import asyncpg
import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "k8s/overlays/aws-bootstrap/scripts/bootstrap_master.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("bootstrap_master", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeConnection:
    """Stands in for an asyncpg.Connection — identity is all these tests check."""


async def test_uses_iam_directly_when_events_already_has_rds_iam():
    module = load_script()
    iam_conn = FakeConnection()

    async def iam_connect():
        return iam_conn

    async def password_connect():
        raise AssertionError("must not fall back when IAM auth already works")

    conn = await module.connect_idempotently(iam_connect, password_connect)

    assert conn is iam_conn


async def test_falls_back_to_master_password_when_rds_iam_not_granted_yet():
    module = load_script()
    password_conn = FakeConnection()

    async def iam_connect():
        # SQLSTATE 28P01: what a genuinely fresh, not-yet-granted role
        # produces when an IAM token is presented as its password.
        raise asyncpg.InvalidPasswordError("no password entry")

    async def password_connect():
        return password_conn

    conn = await module.connect_idempotently(iam_connect, password_connect)

    assert conn is password_conn


async def test_a_different_postgres_error_from_iam_is_not_swallowed():
    module = load_script()

    async def iam_connect():
        # SQLSTATE 28000, the PAM error itself — must never be treated as
        # "not granted yet" or this would loop back into the exact failure
        # it's supposed to fix.
        raise asyncpg.InvalidAuthorizationSpecificationError("PAM authentication failed")

    async def password_connect():
        raise AssertionError("must not fall back for a non-password auth error")

    with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
        await module.connect_idempotently(iam_connect, password_connect)
