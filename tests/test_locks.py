import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from opensansad.ingest.cache import Cache, HttpError
from opensansad.ingest.locks import LockTimeout, MemoryLock
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.store import RawStore

URL = "https://example.org/files/act.pdf"


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 21, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def _cache(tmp_path: Path, handler, now=None, **kw) -> Cache:
    return Cache(RawStore(tmp_path / "raw"), MemoryGate(sleep=lambda s: None), _Recorder(),
                 user_agent="OpenSansad/test (contact: test@example.org)",
                 transport=httpx.MockTransport(handler), sleep=lambda s: None,
                 now=now or (lambda: datetime.now(UTC)), max_attempts=2, **kw)


class _Site:
    """Serves a PDF; counts page requests (robots.txt is a 404: allow all)."""

    def __init__(self, delay: float = 0.0, body: bytes = b"%PDF-1.4 act") -> None:
        self.delay, self.body, self.hits, self.status = delay, body, 0, 200
        self._guard = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        with self._guard:
            self.hits += 1
        time.sleep(self.delay)
        return httpx.Response(self.status, content=self.body,
                              headers={"content-type": "application/pdf"})


def test_memory_lock_serializes_and_times_out() -> None:
    lock = MemoryLock(wait=0.05)
    with lock.hold("a"):
        with lock.hold("b"):  # other keys are independent
            pass
        failed = []
        t = threading.Thread(target=lambda: _try(lock, "a", failed))
        t.start()
        t.join()
    assert failed == ["timeout"]
    with lock.hold("a"):  # released
        pass


def _try(lock: MemoryLock, key: str, out: list) -> None:
    try:
        with lock.hold(key):
            out.append("held")
    except LockTimeout:
        out.append("timeout")


def test_concurrent_gets_fetch_once(tmp_path: Path) -> None:
    site = _Site(delay=0.2)
    cache = _cache(tmp_path, site)
    docs = []
    threads = [threading.Thread(target=lambda: docs.append(cache.get(URL, source="prs")))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert site.hits == 1
    assert len({d.path for d in docs}) == 1 and len(cache.store.versions(URL, "prs")) == 1
    assert sum(not d.from_cache for d in docs) == 1  # the others waited, then hit the cache


def test_max_age_refreshes_old_listing(tmp_path: Path) -> None:
    site, clock = _Site(), _Clock()
    cache = _cache(tmp_path, site, now=clock)
    cache.get(URL, source="sansad")
    clock.t += timedelta(hours=2)

    assert cache.get(URL, source="sansad", max_age=timedelta(hours=3)).from_cache
    assert cache.get(URL, source="sansad").from_cache  # documents: never refreshed
    assert site.hits == 1

    refreshed = cache.get(URL, source="sansad", max_age=timedelta(hours=1))
    assert site.hits == 2 and not refreshed.from_cache
    assert refreshed.last_confirmed == clock.t  # identical bytes: same version, re-confirmed
    assert len(cache.store.versions(URL, "sansad")) == 1
    assert cache.get(URL, source="sansad", max_age=timedelta(hours=1)).from_cache

    site.body = b"%PDF-1.4 act, next sitting"
    clock.t += timedelta(hours=2)
    cache.get(URL, source="sansad", max_age=timedelta(hours=1))
    assert len(cache.store.versions(URL, "sansad")) == 2


def test_failed_refresh_raises_not_stale(tmp_path: Path) -> None:
    site, clock = _Site(), _Clock()
    cache = _cache(tmp_path, site, now=clock)
    cache.get(URL, source="sansad")
    clock.t += timedelta(days=2)
    site.status = 503
    with pytest.raises(HttpError):
        cache.get(URL, source="sansad", max_age=timedelta(hours=24))


def test_adapter_refreshes_listings_only() -> None:
    from opensansad.ingest.sources.sansad import SansadAdapter

    calls = []

    class FakeCache:
        def get(self, url, *, source, max_age=None):
            calls.append(max_age)

    adapter = SansadAdapter(listing_max_age=timedelta(hours=24))
    adapter.fetch(FakeCache(), URL)
    adapter.fetch(FakeCache(), URL, listing=True)
    assert calls == [None, timedelta(hours=24)]
    assert SansadAdapter().listing_max_age is None


def test_adapter_from_settings_uses_listing_ttl(monkeypatch) -> None:
    from opensansad.config import get_settings
    from opensansad.ingest.sources.eparlib import EparlibAdapter

    monkeypatch.setenv("LISTING_TTL_HOURS", "6")
    get_settings.cache_clear()
    try:
        assert EparlibAdapter.from_settings().listing_max_age == timedelta(hours=6)
    finally:
        get_settings.cache_clear()
