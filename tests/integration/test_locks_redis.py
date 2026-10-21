import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from redis import Redis

from opensansad.config import get_settings
from opensansad.ingest.cache import Cache
from opensansad.ingest.locks import LockTimeout, RedisLock
from opensansad.ingest.ratelimit import RedisGate
from opensansad.ingest.store import RawStore

pytestmark = pytest.mark.integration


@pytest.fixture
def prefix():
    client = Redis.from_url(get_settings().redis_url)
    p = f"opensansad:test:{uuid.uuid4().hex}"
    yield p
    for key in client.scan_iter(f"{p}*"):
        client.delete(key)


def _lock(prefix: str, **kw) -> RedisLock:
    # A separate client per lock stands in for another Celery worker.
    return RedisLock(Redis.from_url(get_settings().redis_url), prefix=f"{prefix}:lock", **kw)


def test_second_worker_waits_then_times_out(prefix: str) -> None:
    a, b = _lock(prefix), _lock(prefix, wait=0.3, poll=0.05)
    with a.hold("k"):
        start = time.monotonic()
        with pytest.raises(LockTimeout):
            with b.hold("k"):
                pass
        assert time.monotonic() - start >= 0.3
    with b.hold("k"):  # released
        pass


def test_expired_lock_is_not_released_by_its_old_holder(prefix: str) -> None:
    a, b = _lock(prefix, ttl=0.2), _lock(prefix, wait=2, poll=0.05)
    client = Redis.from_url(get_settings().redis_url)
    with a.hold("k"):
        time.sleep(0.3)  # a's lock expires: a crashed or slow worker
        with b.hold("k"):
            pass  # b takes it over...
        b_holds = threading.Event()

        def b_takes_again() -> None:
            with b.hold("k"):
                b_holds.set()
                time.sleep(0.3)

        t = threading.Thread(target=b_takes_again)
        t.start()
        b_holds.wait(2)
    # ...a's exit happened while b held the lock: b's key must survive it.
    assert client.get(f"{prefix}:lock:k") is not None
    t.join()


def test_two_caches_on_one_redis_fetch_once(prefix: str, tmp_path: Path) -> None:
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits.append(request.url)
        time.sleep(0.3)
        return httpx.Response(200, content=b"%PDF-1.4 x",
                              headers={"content-type": "application/pdf"})

    def cache() -> Cache:
        redis = Redis.from_url(get_settings().redis_url)
        return Cache(RawStore(tmp_path / "raw"), RedisGate(redis, prefix=f"{prefix}:gate"),
                     type("R", (), {"record": lambda *a: None})(),
                     user_agent="OpenSansad/test (contact: test@example.org)",
                     transport=httpx.MockTransport(handler), default_delay=0,
                     lock=RedisLock(redis, prefix=f"{prefix}:lock", poll=0.05),
                     now=lambda: datetime.now(UTC))

    workers = [cache(), cache()]
    threads = [threading.Thread(target=w.get, args=("https://example.org/a.pdf",),
                                kwargs={"source": "prs"}) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(hits) == 1
    assert len(RawStore(tmp_path / "raw").versions("https://example.org/a.pdf", "prs")) == 1
