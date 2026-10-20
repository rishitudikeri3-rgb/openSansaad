"""Per-domain politeness state shared by every process that fetches.

A gate enforces a minimum interval between requests to a domain, and holds
domain-wide blocks (e.g. after a 403) that outlive the process that set them.
"""

import time
from collections.abc import Callable
from typing import Protocol

from redis import Redis


class DomainGate(Protocol):
    def acquire(self, domain: str, min_interval: float) -> None:
        """Block until a request to `domain` is allowed, then claim the slot."""

    def block(self, domain: str, seconds: float) -> None:
        """Refuse all requests to `domain` for `seconds`."""

    def blocked_for(self, domain: str) -> float:
        """Seconds remaining on a block, 0 if none."""

    def record_failure(self, domain: str, window: float) -> int:
        """Count a connection failure; returns consecutive failures within `window` seconds."""

    def reset_failures(self, domain: str) -> None:
        """The domain answered; clear its consecutive-failure count."""


class MemoryGate:
    """Single-process gate. Used in tests with a fake clock; not safe across workers."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}
        self._failures: dict[str, tuple[int, float]] = {}  # domain -> (count, expires_at)

    def acquire(self, domain: str, min_interval: float) -> None:
        wait = self._next.get(domain, 0.0) - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._next[domain] = self._clock() + min_interval

    def block(self, domain: str, seconds: float) -> None:
        self._blocked_until[domain] = self._clock() + seconds

    def blocked_for(self, domain: str) -> float:
        return max(0.0, self._blocked_until.get(domain, 0.0) - self._clock())

    def record_failure(self, domain: str, window: float) -> int:
        count, expires = self._failures.get(domain, (0, 0.0))
        if self._clock() >= expires:
            count = 0
        self._failures[domain] = (count + 1, self._clock() + window)
        return count + 1

    def reset_failures(self, domain: str) -> None:
        self._failures.pop(domain, None)


# Atomically: if the domain's next-allowed time is in the future, return the wait;
# otherwise claim the slot. Uses the Redis server clock so all workers agree.
_ACQUIRE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
if nxt > now then
  return tostring(nxt - now)
end
local interval = tonumber(ARGV[1])
redis.call('SET', KEYS[1], tostring(now + interval), 'EX', math.ceil(interval) + 60)
return '0'
"""


class RedisGate:
    """Gate shared by all Celery workers via Redis."""

    def __init__(
        self,
        client: Redis,
        prefix: str = "opensansad:gate",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._r = client
        self._prefix = prefix
        self._sleep = sleep
        self._acquire = client.register_script(_ACQUIRE_LUA)

    def acquire(self, domain: str, min_interval: float) -> None:
        key = f"{self._prefix}:next:{domain}"
        while (wait := float(self._acquire(keys=[key], args=[min_interval]))) > 0:
            self._sleep(wait)

    def block(self, domain: str, seconds: float) -> None:
        self._r.set(f"{self._prefix}:blocked:{domain}", "1", px=max(1, int(seconds * 1000)))

    def blocked_for(self, domain: str) -> float:
        ttl_ms = self._r.pttl(f"{self._prefix}:blocked:{domain}")
        return max(0.0, ttl_ms / 1000) if ttl_ms and ttl_ms > 0 else 0.0

    def record_failure(self, domain: str, window: float) -> int:
        key = f"{self._prefix}:failures:{domain}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.pexpire(key, max(1, int(window * 1000)))
        count, _ = pipe.execute()
        return int(count)

    def reset_failures(self, domain: str) -> None:
        self._r.delete(f"{self._prefix}:failures:{domain}")
