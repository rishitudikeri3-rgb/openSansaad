import time
import uuid

import pytest
from redis import Redis

from opensansad.config import get_settings
from opensansad.ingest.ratelimit import RedisGate

pytestmark = pytest.mark.integration


@pytest.fixture
def gate() -> RedisGate:
    client = Redis.from_url(get_settings().redis_url)
    prefix = f"opensansad:test:{uuid.uuid4().hex}"
    yield RedisGate(client, prefix=prefix)
    for key in client.scan_iter(f"{prefix}:*"):
        client.delete(key)


def test_acquire_spaces_requests_across_gates(gate: RedisGate) -> None:
    # A second gate on the same prefix stands in for another Celery worker.
    other = RedisGate(Redis.from_url(get_settings().redis_url), prefix=gate._prefix)
    start = time.monotonic()
    gate.acquire("prs.test", 0.4)
    other.acquire("prs.test", 0.4)
    gate.acquire("prs.test", 0.4)
    assert time.monotonic() - start >= 0.75


def test_domains_are_independent(gate: RedisGate) -> None:
    start = time.monotonic()
    gate.acquire("a.test", 5)
    gate.acquire("b.test", 5)
    assert time.monotonic() - start < 0.5


def test_block_expires(gate: RedisGate) -> None:
    assert gate.blocked_for("indiacode.test") == 0
    gate.block("indiacode.test", 0.3)
    assert 0 < gate.blocked_for("indiacode.test") <= 0.3
    time.sleep(0.4)
    assert gate.blocked_for("indiacode.test") == 0


def test_failure_count_and_reset(gate: RedisGate) -> None:
    assert gate.record_failure("drops.test", 60) == 1
    assert gate.record_failure("drops.test", 60) == 2
    gate.reset_failures("drops.test")
    assert gate.record_failure("drops.test", 60) == 1


def test_failure_count_expires(gate: RedisGate) -> None:
    gate.record_failure("drops.test", 0.2)
    time.sleep(0.3)
    assert gate.record_failure("drops.test", 0.2) == 1
