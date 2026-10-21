"""The ONLY network entry point in OpenSansad.

Every outbound HTTP request goes through Cache.get(). It:
- returns the stored copy if one exists (never re-fetches unless force=True),
- respects robots.txt (rules and Crawl-delay), re-checking it every robots_ttl_days,
- spaces requests per domain via a shared DomainGate,
- retries timeouts / 429 / 5xx with exponential backoff and Retry-After,
- treats a 403 as a domain-wide block for hours (no retries), and blocks a domain
  after repeated consecutive connection failures (silent drops),
- follows redirects itself, applying all of the above to every hop's domain,
- sends an honest User-Agent with a contact address,
- records every attempt via a FetchRecorder (fetch_log table in production).
"""

import random
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import certifi
import httpx
from sqlalchemy.orm import Session, sessionmaker

from opensansad.db.models import Acquisition, FetchLog
from opensansad.ingest.ratelimit import DomainGate
from opensansad.ingest.store import RawStore, StoredDoc, looks_like_error_page, normalize_url

ROBOTS_SOURCE = "_robots"
_RETRY_STATUS = {429, 500, 502, 503, 504}
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 120.0


class FetchError(Exception):
    pass


class ConfigError(FetchError):
    pass


class DomainBlocked(FetchError):
    pass


class RobotsForbidden(DomainBlocked):
    """robots.txt itself answered 403: the site refuses us outright."""


class RobotsDisallowed(FetchError):
    pass


class HttpError(FetchError):
    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code


class NotADocument(FetchError):
    pass


class FetchRecorder(Protocol):
    def record(self, url: str, status_code: int | None, error: str | None) -> None: ...


class SqlFetchRecorder:
    def __init__(self, make_session: sessionmaker[Session]) -> None:
        self._make_session = make_session

    def record(self, url: str, status_code: int | None, error: str | None) -> None:
        with self._make_session() as s:
            s.add(FetchLog(url=url, status_code=status_code, error=error))
            s.commit()


def build_ssl_context() -> ssl.SSLContext:
    """certifi roots plus bundled intermediates for servers that send incomplete chains."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    extra = files("opensansad.ingest").joinpath("certs/letsencrypt-gen-y.pem")
    ctx.load_verify_locations(cadata=extra.read_text())
    return ctx


def _check_user_agent(ua: str) -> None:
    if "<your-email>" in ua or "contact:" not in ua:
        raise ConfigError(
            "USER_AGENT must include a real contact address (set it in .env) before fetching"
        )


def _retry_after(response: httpx.Response, now: datetime) -> float | None:
    """Retry-After as delta-seconds or HTTP-date (RFC 9110)."""
    value = (response.headers.get("Retry-After") or "").strip()
    if value.isdigit():
        return float(value)
    if value:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - now).total_seconds())
    return None


class Cache:
    def __init__(
        self,
        store: RawStore,
        gate: DomainGate,
        recorder: FetchRecorder,
        *,
        user_agent: str,
        default_delay: float = 1.5,
        timeout: float = 30.0,
        max_attempts: int = 4,
        block_seconds: float = 6 * 3600,
        failure_threshold: int = 4,
        failure_block_seconds: float = 3600,
        robots_ttl: timedelta = timedelta(days=7),
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        _check_user_agent(user_agent)
        self.store = store
        self._gate = gate
        self._recorder = recorder
        self._ua = user_agent
        self._ua_token = user_agent.split("/", 1)[0]
        self._default_delay = default_delay
        self._max_attempts = max_attempts
        self._block_seconds = block_seconds
        self._failure_threshold = failure_threshold
        self._failure_block_seconds = failure_block_seconds
        self._robots_ttl = robots_ttl
        self._sleep = sleep
        self._now = now
        # root -> (parser, when it was last confirmed against the server)
        self._robots: dict[str, tuple[RobotFileParser, datetime]] = {}
        client_kwargs: dict = dict(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=False,  # followed by hand in _fetch, one checked hop at a time
        )
        if transport is not None:
            client_kwargs["transport"] = transport
        else:
            client_kwargs["verify"] = build_ssl_context()
        self._client = httpx.Client(**client_kwargs)

    @classmethod
    def from_settings(cls) -> "Cache":
        from redis import Redis

        from opensansad.config import get_settings
        from opensansad.db.session import get_sessionmaker
        from opensansad.ingest.ratelimit import RedisGate

        s = get_settings()
        return cls(
            RawStore(s.raw_dir),
            RedisGate(Redis.from_url(s.redis_url)),
            SqlFetchRecorder(get_sessionmaker()),
            user_agent=s.user_agent,
            default_delay=s.request_delay_seconds,
            timeout=s.fetch_timeout_seconds,
            max_attempts=s.fetch_max_attempts,
            block_seconds=s.domain_block_hours * 3600,
            failure_threshold=s.domain_failure_threshold,
            failure_block_seconds=s.domain_failure_block_hours * 3600,
            robots_ttl=timedelta(days=s.robots_ttl_days),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- public API -------------------------------------------------------

    def get(self, url: str, *, source: str, force: bool = False) -> StoredDoc:
        url = normalize_url(url)
        if not force and (cached := self.store.latest(url, source)):
            return cached
        return self._fetch(url, source)

    def import_local(self, url: str, path: str | Path, *, source: str) -> StoredDoc:
        """Record a file downloaded by hand in a browser, with the URL it came from."""
        path = Path(path).expanduser()
        with path.open("rb") as f:
            head = f.read(20_001)
        if looks_like_error_page(head):
            raise NotADocument(f"{path} looks like an error / access-denied page, not a document")
        return self.store.import_file(normalize_url(url), source, path)

    # ---- internals --------------------------------------------------------

    def _domain(self, url: str) -> str:
        return urlsplit(url).netloc

    def _fetch(self, url: str, source: str, *, is_robots: bool = False) -> StoredDoc:
        """Follow redirects by hand so every hop gets its own robots / delay / block checks.

        Content is stored under the requested `url`; the URL it finally came from is
        recorded as `final_url`. robots.txt requests skip the robots check (RFC 9309).
        """
        current = url
        via: list[str] = []  # domains that redirected us here, on this request
        for _ in range(MAX_REDIRECTS + 1):
            delay = self._default_delay
            try:
                if not is_robots:
                    robots = self._robots_for(current)
                    if not robots.can_fetch(self._ua_token, current):
                        self._recorder.record(current, None, "disallowed by robots.txt")
                        raise RobotsDisallowed(current)
                    delay = max(delay, float(robots.crawl_delay(self._ua_token) or 0))
                result = self._request(url, current, source, delay, store_errors=is_robots)
            except (HttpError, RobotsForbidden) as e:
                # A 403 behind a redirect (legislative.gov.in -> www.legislative.gov.in) comes
                # from the same operator: block the domains that sent us there as well.
                if isinstance(e, RobotsForbidden) or e.status_code == 403:
                    for domain in via:
                        self._gate.block(domain, self._block_seconds)
                raise
            if isinstance(result, StoredDoc):
                return result
            if (domain := self._domain(current)) not in via:
                via.append(domain)
            current = result
        self._recorder.record(url, None, f"more than {MAX_REDIRECTS} redirects")
        raise FetchError(f"{url}: more than {MAX_REDIRECTS} redirects")

    def _request(
        self, url: str, current: str, source: str, delay: float, *, store_errors: bool
    ) -> StoredDoc | str:
        """One hop, with retries. Returns the stored doc, or the next URL on a redirect."""
        domain = self._domain(current)
        for attempt in range(1, self._max_attempts + 1):
            if (remaining := self._gate.blocked_for(domain)) > 0:
                self._recorder.record(current, None, f"domain blocked for {remaining:.0f}s")
                raise DomainBlocked(f"{domain} is blocked for another {remaining:.0f}s")

            self._gate.acquire(domain, delay)
            try:
                with self._client.stream("GET", current) as resp:
                    status = resp.status_code
                    ctype = resp.headers.get("content-type")
                    keep_error = store_errors and 400 <= status < 500 and status != 403
                    if 200 <= status < 300 or keep_error:
                        doc = self.store.write(
                            url,
                            source,
                            resp.iter_bytes(),
                            status_code=status,
                            content_type=ctype,
                            acquisition=Acquisition.FETCHED,
                            final_url=current if current != url else None,
                            now=self._now(),
                        )
                        self._recorder.record(current, status, None)
                        self._gate.reset_failures(domain)
                        return doc
                    location = resp.headers.get("location")
                    retry_after = _retry_after(resp, self._now())
            except httpx.TransportError as e:
                self._recorder.record(current, None, f"{type(e).__name__}: {e}")
                # A server that silently drops us (as India Code did) is as clear a signal as
                # a 403: after enough consecutive failures, stop knocking on the whole domain.
                failures = self._gate.record_failure(domain, self._failure_block_seconds)
                if failures >= self._failure_threshold:
                    self._gate.block(domain, self._failure_block_seconds)
                    self._gate.reset_failures(domain)
                    raise DomainBlocked(
                        f"{domain}: {failures} consecutive connection failures; "
                        f"blocked for {self._failure_block_seconds:.0f}s"
                    ) from e
                if attempt == self._max_attempts:
                    raise FetchError(
                        f"{current}: {type(e).__name__} after {attempt} attempts"
                    ) from e
                self._sleep(self._backoff(attempt))
                continue

            self._gate.reset_failures(domain)  # the server answered, even if not with content
            if status in _REDIRECT_STATUS and location:
                target = normalize_url(urljoin(current, location))
                self._recorder.record(current, status, f"redirect -> {target}")
                return target
            self._recorder.record(current, status, None)
            if status == 403:
                self._gate.block(domain, self._block_seconds)
                raise HttpError(current, status)
            if status not in _RETRY_STATUS or attempt == self._max_attempts:
                raise HttpError(current, status)
            self._sleep(retry_after if retry_after is not None else self._backoff(attempt))
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return min(_BACKOFF_CAP, _BACKOFF_BASE * 2 ** (attempt - 1)) + random.uniform(0, 1)

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if (hit := self._robots.get(root)) and self._now() - hit[1] <= self._robots_ttl:
            return hit[0]

        robots_url = f"{root}/robots.txt"
        doc = self.store.latest(robots_url, ROBOTS_SOURCE)
        if doc is None or self._now() - doc.last_confirmed > self._robots_ttl:
            try:
                doc = self._fetch(robots_url, ROBOTS_SOURCE, is_robots=True)
            except HttpError as e:
                if e.status_code == 403:
                    raise RobotsForbidden(f"{parts.netloc} returned 403 for robots.txt") from e
                # 5xx after retries: RFC 9309 says assume complete disallow.
                raise RobotsDisallowed(f"robots.txt unavailable ({e.status_code})") from e

        parser = RobotFileParser()
        if doc.status_code is not None and 400 <= doc.status_code < 500:
            parser.allow_all = True  # RFC 9309: 4xx means no restrictions
        else:
            parser.parse(doc.path.read_text(errors="replace").splitlines())
        self._robots[root] = (parser, doc.last_confirmed)
        return parser
