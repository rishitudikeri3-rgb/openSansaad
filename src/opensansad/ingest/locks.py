"""Per-entry locks for the raw store, so two workers never fetch or write one URL at once.

A raw-store entry (source + URL) owns one meta.json; concurrent writers to it would lose
each other's versions. Cache takes the entry's lock around every fetch / import, and
re-checks the store once it holds it (another worker may have just fetched the file).
"""

import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from redis import Redis


class LockTimeout(Exception):
    """Waited too long for another worker to finish with an entry."""


class EntryLock(Protocol):
    def hold(self, key: str) -> AbstractContextManager[None]:
        """Hold the lock for `key` for the duration of the with-block."""


class MemoryLock:
    """Single-process lock (threads). Default for a Cache built by hand, and for tests."""

    def __init__(self, wait: float = 900.0) -> None:
        self._wait = wait
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        if not lock.acquire(timeout=self._wait):
            raise LockTimeout(f"{key}: still locked after {self._wait:.0f}s")
        try:
            yield
        finally:
            lock.release()


# Delete the key only if it still holds our token: a lock that expired and was taken by
# another worker must not be released by us.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisLock:
    """Lock shared by all Celery workers via Redis.

    The lock expires after `ttl` seconds even if its holder dies, so a crashed worker
    cannot wedge a URL. `ttl` must exceed the longest single fetch (retries, backoff and
    crawl delay included); it is not renewed.
    """

    def __init__(
        self,
        client: Redis,
        *,
        prefix: str = "opensansad:lock",
        ttl: float = 900.0,
        wait: float = 900.0,
        poll: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = client
        self._prefix = prefix
        self._ttl_ms = max(1, int(ttl * 1000))
        self._wait = wait
        self._poll = poll
        self._sleep = sleep
        self._clock = clock
        self._release = client.register_script(_RELEASE_LUA)

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        name = f"{self._prefix}:{key}"
        token = secrets.token_hex(16)
        deadline = self._clock() + self._wait
        while not self._r.set(name, token, nx=True, px=self._ttl_ms):
            if self._clock() >= deadline:
                raise LockTimeout(f"{key}: still locked after {self._wait:.0f}s")
            self._sleep(self._poll)
        try:
            yield
        finally:
            self._release(keys=[name], args=[token])
