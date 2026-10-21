from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from opensansad.db.models import Acquisition
from opensansad.ingest.cache import (
    Cache,
    ConfigError,
    DomainBlocked,
    FetchError,
    HttpError,
    NotADocument,
    RobotsDisallowed,
    build_ssl_context,
)
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.store import RawStore

UA = "OpenSansad/0.1 (academic research project; contact: test@example.org)"
PDF = b"%PDF-1.7 test"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int | None, str | None]] = []

    def record(self, url: str, status_code: int | None, error: str | None) -> None:
        self.rows.append((url, status_code, error))


class Site:
    """Scriptable fake web server. routes: path -> list of responses served in order."""

    def __init__(self, robots: str | int = "User-agent: *\nDisallow: /private/\n") -> None:
        self.calls: list[str] = []
        self.robots = robots
        self.routes: dict[str, list[httpx.Response]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        assert request.headers["User-Agent"] == UA
        if request.url.path == "/robots.txt":
            if isinstance(self.robots, int):
                return httpx.Response(self.robots)
            return httpx.Response(200, text=self.robots)
        queue = self.routes.get(request.url.path)
        if not queue:
            return httpx.Response(404)
        return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def make_cache(tmp_path: Path, site: Site, clock: FakeClock, recorder: Recorder, **kw) -> Cache:
    return Cache(
        RawStore(tmp_path),
        MemoryGate(clock=clock, sleep=clock.sleep),
        recorder,
        user_agent=kw.pop("user_agent", UA),
        transport=httpx.MockTransport(site.handler),
        sleep=clock.sleep,
        **kw,
    )


def test_fetch_then_serve_from_cache(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a.pdf"] = [httpx.Response(200, content=PDF)]
    cache = make_cache(tmp_path, site, clock, recorder)

    first = cache.get("https://prs.test/a.pdf", source="prs")
    second = cache.get("https://prs.test/a.pdf", source="prs")

    assert not first.from_cache and second.from_cache
    assert first.path.read_bytes() == PDF
    assert first.acquisition is Acquisition.FETCHED
    assert site.calls == ["/robots.txt", "/a.pdf"]  # no second network hit


def test_force_refetch_keeps_old_version(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a.pdf"] = [
        httpx.Response(200, content=PDF),
        httpx.Response(200, content=PDF + b"v2"),
    ]
    cache = make_cache(tmp_path, site, clock, recorder)

    old = cache.get("https://prs.test/a.pdf", source="prs")
    new = cache.get("https://prs.test/a.pdf", source="prs", force=True)

    assert old.path.exists() and old.path.read_bytes() == PDF
    assert new.path.read_bytes() == PDF + b"v2"
    assert len(cache.store.versions("https://prs.test/a.pdf", "prs")) == 2


def test_robots_disallow_blocks_without_request(tmp_path, clock, recorder) -> None:
    site = Site()
    cache = make_cache(tmp_path, site, clock, recorder)
    with pytest.raises(RobotsDisallowed):
        cache.get("https://prs.test/private/x.pdf", source="prs")
    assert "/private/x.pdf" not in site.calls
    assert recorder.rows[-1][2] == "disallowed by robots.txt"


def test_robots_4xx_means_allow_all(tmp_path, clock, recorder) -> None:
    site = Site(robots=404)
    site.routes["/private/x.pdf"] = [httpx.Response(200, content=PDF)]
    cache = make_cache(tmp_path, site, clock, recorder)
    assert cache.get("https://sansad.test/private/x.pdf", source="sansad").path.exists()


def test_robots_403_blocks_domain(tmp_path, clock, recorder) -> None:
    site = Site(robots=403)
    cache = make_cache(tmp_path, site, clock, recorder)
    with pytest.raises(DomainBlocked):
        cache.get("https://indiacode.test/a.pdf", source="indiacode")
    assert site.calls == ["/robots.txt"]


def test_crawl_delay_respected(tmp_path, clock, recorder) -> None:
    site = Site(robots="User-agent: *\nCrawl-delay: 10\n")
    site.routes["/a"] = [httpx.Response(200, content=PDF)]
    site.routes["/b"] = [httpx.Response(200, content=PDF + b"b")]
    cache = make_cache(tmp_path, site, clock, recorder, default_delay=1.5)

    cache.get("https://prs.test/a", source="prs")
    cache.get("https://prs.test/b", source="prs")

    # robots fetched at default delay; /a then waits 1.5s; /b waits the 10s crawl-delay
    assert clock.sleeps == [1.5, 10.0]


def test_retry_on_503_then_success(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a.pdf"] = [
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "42"}),
        httpx.Response(200, content=PDF),
    ]
    cache = make_cache(tmp_path, site, clock, recorder, default_delay=0)

    doc = cache.get("https://prs.test/a.pdf", source="prs")

    assert doc.path.read_bytes() == PDF
    assert 2.0 <= clock.sleeps[0] <= 3.0  # exponential backoff with jitter
    assert clock.sleeps[1] == 42.0  # Retry-After honoured
    assert [r[1] for r in recorder.rows if r[0].endswith("/a.pdf")] == [503, 429, 200]


def test_gives_up_after_max_attempts(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a.pdf"] = [httpx.Response(500)]
    cache = make_cache(tmp_path, site, clock, recorder, max_attempts=3, default_delay=0)
    with pytest.raises(HttpError) as e:
        cache.get("https://prs.test/a.pdf", source="prs")
    assert e.value.status_code == 500
    assert site.calls.count("/a.pdf") == 3


def test_404_not_retried_or_stored(tmp_path, clock, recorder) -> None:
    site = Site()
    cache = make_cache(tmp_path, site, clock, recorder)
    with pytest.raises(HttpError):
        cache.get("https://prs.test/missing", source="prs")
    assert site.calls.count("/missing") == 1
    assert cache.store.latest("https://prs.test/missing", "prs") is None


def test_403_blocks_domain_for_hours(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a"] = [httpx.Response(403)]
    site.routes["/b"] = [httpx.Response(200, content=PDF)]
    cache = make_cache(tmp_path, site, clock, recorder, block_seconds=6 * 3600)

    with pytest.raises(HttpError):
        cache.get("https://indiacode.test/a", source="indiacode")
    with pytest.raises(DomainBlocked):
        cache.get("https://indiacode.test/b", source="indiacode")

    assert site.calls.count("/a") == 1  # no retries on 403
    assert "/b" not in site.calls  # blocked before any request
    clock.t += 6 * 3600 + 1
    assert cache.get("https://indiacode.test/b", source="indiacode").path.exists()


def test_transport_errors_retried_then_raised(tmp_path, clock, recorder) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectTimeout("timed out")

    cache = Cache(RawStore(tmp_path), MemoryGate(clock=clock, sleep=clock.sleep), recorder,
                  user_agent=UA, transport=httpx.MockTransport(handler), sleep=clock.sleep,
                  max_attempts=2)
    with pytest.raises(FetchError, match="ConnectTimeout after 2 attempts"):
        cache.get("https://eparlib.test/x.pdf", source="eparlib")
    assert [r[2].split(":")[0] for r in recorder.rows[-2:]] == ["ConnectTimeout"] * 2


def test_robots_refetched_after_ttl(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a"] = [httpx.Response(200, content=PDF)]
    site.routes["/b"] = [httpx.Response(200, content=PDF + b"b")]
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    cache = make_cache(tmp_path, site, clock, recorder, now=lambda: now[0],
                       robots_ttl=timedelta(days=7))
    cache.get("https://prs.test/a", source="prs")

    now[0] += timedelta(days=8)
    fresh = make_cache(tmp_path, site, clock, recorder, now=lambda: now[0],
                       robots_ttl=timedelta(days=7))
    fresh.get("https://prs.test/b", source="prs")
    assert site.calls.count("/robots.txt") == 2


@pytest.mark.parametrize("ua", [
    "OpenSansad/0.1 (academic research project; contact: <your-email>)",
    "OpenSansad/0.1 (academic research project)",
])
def test_placeholder_user_agent_refused(tmp_path, clock, recorder, ua) -> None:
    with pytest.raises(ConfigError):
        make_cache(tmp_path, Site(), clock, recorder, user_agent=ua)


def test_import_local(tmp_path, clock, recorder) -> None:
    f = tmp_path / "a199626.pdf"
    f.write_bytes(PDF)
    site = Site()
    cache = make_cache(tmp_path / "raw", site, clock, recorder)

    doc = cache.import_local("https://www.indiacode.nic.in/bitstream/a199626.pdf", f,
                             source="indiacode")
    assert doc.acquisition is Acquisition.MANUAL
    assert site.calls == []  # no network
    assert cache.get("https://www.indiacode.nic.in/bitstream/a199626.pdf",
                     source="indiacode").sha256 == doc.sha256


def test_import_local_rejects_error_page(tmp_path, clock, recorder) -> None:
    f = tmp_path / "saved.html"
    f.write_text("<html><head><title>Access Denied</title></head><body>WAF</body></html>")
    cache = make_cache(tmp_path / "raw", Site(), clock, recorder)
    with pytest.raises(NotADocument):
        cache.import_local("https://www.indiacode.nic.in/x", f, source="indiacode")


def test_ssl_context_keeps_verification_on() -> None:
    import ssl

    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    subjects = {dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()}
    assert {"YR1", "Root YR", "ISRG Root X1"} <= subjects


# ---- redirects ---------------------------------------------------------------


class Web:
    """Fake multi-host web. routes: full URL -> response (wins, even for robots.txt);
    robots: host -> text or status."""

    def __init__(self, robots: dict[str, str | int], routes: dict[str, httpx.Response]) -> None:
        self.robots = robots
        self.routes = routes
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        if request.url.path == "/robots.txt":
            r = self.robots.get(request.url.host, 404)
            return httpx.Response(r) if isinstance(r, int) else httpx.Response(200, text=r)
        return self.routes.get(url, httpx.Response(404))


def redirect(to: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"Location": to})


def web_cache(tmp_path, web: Web, clock, recorder, **kw) -> Cache:
    return Cache(RawStore(tmp_path), MemoryGate(clock=clock, sleep=clock.sleep), recorder,
                 user_agent=UA, transport=httpx.MockTransport(web.handler), sleep=clock.sleep,
                 **kw)


def test_redirect_stored_under_requested_url(tmp_path, clock, recorder) -> None:
    web = Web({}, {
        "https://old.test/doc": redirect("/v2/doc", 301),  # relative Location
        "https://old.test/v2/doc": httpx.Response(200, content=PDF),
    })
    cache = web_cache(tmp_path, web, clock, recorder)

    doc = cache.get("https://old.test/doc", source="eparlib")

    assert doc.url == "https://old.test/doc"
    assert doc.final_url == "https://old.test/v2/doc"
    assert cache.get("https://old.test/doc", source="eparlib").from_cache
    assert ("https://old.test/doc", 301, "redirect -> https://old.test/v2/doc") in recorder.rows


def test_cross_domain_redirect_checks_target_robots(tmp_path, clock, recorder) -> None:
    web = Web({"new.test": "User-agent: *\nDisallow: /private/\n"}, {
        "https://old.test/doc": redirect("https://new.test/private/doc"),
    })
    cache = web_cache(tmp_path, web, clock, recorder)

    with pytest.raises(RobotsDisallowed):
        cache.get("https://old.test/doc", source="eparlib")
    assert "https://new.test/robots.txt" in web.calls
    assert "https://new.test/private/doc" not in web.calls


def test_cross_domain_redirect_respects_target_block(tmp_path, clock, recorder) -> None:
    web = Web({}, {"https://mirror.test/doc": redirect("https://indiacode.test/doc")})
    gate = MemoryGate(clock=clock, sleep=clock.sleep)
    gate.block("indiacode.test", 3600)
    cache = Cache(RawStore(tmp_path), gate, recorder, user_agent=UA,
                  transport=httpx.MockTransport(web.handler), sleep=clock.sleep)

    with pytest.raises(DomainBlocked):
        cache.get("https://mirror.test/doc", source="indiacode")
    assert not any(c.startswith("https://indiacode.test/") for c in web.calls)


def test_cross_domain_redirect_uses_target_crawl_delay(tmp_path, clock, recorder) -> None:
    web = Web({"slow.test": "User-agent: *\nCrawl-delay: 10\n"}, {
        "https://fast.test/a": redirect("https://slow.test/a"),
        "https://fast.test/b": redirect("https://slow.test/b"),
        "https://slow.test/a": httpx.Response(200, content=PDF),
        "https://slow.test/b": httpx.Response(200, content=PDF + b"b"),
    })
    cache = web_cache(tmp_path, web, clock, recorder, default_delay=0)

    cache.get("https://fast.test/a", source="prs")
    cache.get("https://fast.test/b", source="prs")

    assert clock.sleeps == [10.0]  # second hit on slow.test waits its crawl-delay


def test_403_behind_redirect_blocks_redirecting_domain_too(tmp_path, clock, recorder) -> None:
    """legislative.gov.in -> www.legislative.gov.in, whose robots.txt answers 403."""
    web = Web({"www.leg.test": 403}, {"https://leg.test/robots.txt": redirect(
        "https://www.leg.test/robots.txt")})
    gate = MemoryGate(clock=clock, sleep=clock.sleep)
    cache = Cache(RawStore(tmp_path), gate, recorder, user_agent=UA,
                  transport=httpx.MockTransport(web.handler), sleep=clock.sleep)

    with pytest.raises(DomainBlocked):
        cache.get("https://leg.test/", source="other")
    assert gate.blocked_for("www.leg.test") > 0
    assert gate.blocked_for("leg.test") > 0

    sent = len(web.calls)
    with pytest.raises(DomainBlocked):
        cache.get("https://leg.test/sites/default/files/A1996-26.pdf", source="other")
    assert len(web.calls) == sent  # nothing more sent to either host


def test_page_403_behind_redirect_blocks_chain(tmp_path, clock, recorder) -> None:
    web = Web({}, {"https://a.test/doc": redirect("https://b.test/doc"),
                   "https://b.test/doc": httpx.Response(403)})
    gate = MemoryGate(clock=clock, sleep=clock.sleep)
    cache = Cache(RawStore(tmp_path), gate, recorder, user_agent=UA,
                  transport=httpx.MockTransport(web.handler), sleep=clock.sleep)

    with pytest.raises(HttpError):
        cache.get("https://a.test/doc", source="other")
    assert gate.blocked_for("a.test") > 0 and gate.blocked_for("b.test") > 0


def test_redirect_to_404_does_not_block(tmp_path, clock, recorder) -> None:
    web = Web({}, {"https://a.test/doc": redirect("https://b.test/gone")})
    gate = MemoryGate(clock=clock, sleep=clock.sleep)
    cache = Cache(RawStore(tmp_path), gate, recorder, user_agent=UA,
                  transport=httpx.MockTransport(web.handler), sleep=clock.sleep)

    with pytest.raises(HttpError):
        cache.get("https://a.test/doc", source="other")
    assert gate.blocked_for("a.test") == 0 and gate.blocked_for("b.test") == 0


def test_redirect_loop_gives_up(tmp_path, clock, recorder) -> None:
    web = Web({}, {
        "https://loop.test/a": redirect("https://loop.test/b"),
        "https://loop.test/b": redirect("https://loop.test/a"),
    })
    cache = web_cache(tmp_path, web, clock, recorder, default_delay=0)

    with pytest.raises(FetchError, match="redirects"):
        cache.get("https://loop.test/a", source="prs")
    assert len([c for c in web.calls if not c.endswith("robots.txt")]) == 6  # 1 + 5 hops


# ---- hardening: silent drops, robots freshness, Retry-After dates -------------


def _dropping_handler(fail_paths: set[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path in fail_paths:
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, content=PDF + request.url.path.encode())
    return handler


def test_consecutive_connection_failures_block_domain(tmp_path, clock, recorder) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _dropping_handler({"/a", "/b"})(request)

    cache = Cache(RawStore(tmp_path), MemoryGate(clock=clock, sleep=clock.sleep), recorder,
                  user_agent=UA, transport=httpx.MockTransport(handler), sleep=clock.sleep,
                  max_attempts=4, failure_threshold=3, failure_block_seconds=3600,
                  default_delay=0)

    with pytest.raises(DomainBlocked, match="3 consecutive connection failures"):
        cache.get("https://indiacode.test/a", source="indiacode")
    assert calls.count("/a") == 3  # stopped before the 4th attempt
    with pytest.raises(DomainBlocked):
        cache.get("https://indiacode.test/b", source="indiacode")
    assert "/b" not in calls


def test_successful_response_resets_failure_count(tmp_path, clock, recorder) -> None:
    gate = MemoryGate(clock=clock, sleep=clock.sleep)
    cache = Cache(RawStore(tmp_path), gate, recorder, user_agent=UA,
                  transport=httpx.MockTransport(_dropping_handler({"/bad"})), sleep=clock.sleep,
                  max_attempts=2, failure_threshold=3, default_delay=0)

    with pytest.raises(FetchError):
        cache.get("https://flaky.test/bad", source="prs")  # 2 failures
    cache.get("https://flaky.test/ok", source="prs")  # success resets
    with pytest.raises(FetchError) as e:
        cache.get("https://flaky.test/bad", source="prs")  # 2 more, still under 3
    assert not isinstance(e.value, DomainBlocked)


def test_retry_after_http_date(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a.pdf"] = [
        httpx.Response(503, headers={"Retry-After": "Tue, 20 Oct 2026 12:01:30 GMT"}),
        httpx.Response(200, content=PDF),
    ]
    cache = make_cache(tmp_path, site, clock, recorder, default_delay=0,
                       now=lambda: datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC))
    cache.get("https://prs.test/a.pdf", source="prs")
    assert clock.sleeps == [90.0]


def test_long_running_cache_refreshes_robots(tmp_path, clock, recorder) -> None:
    site = Site()
    site.routes["/a"] = [httpx.Response(200, content=PDF)]
    site.routes["/b"] = [httpx.Response(200, content=PDF + b"b")]
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    cache = make_cache(tmp_path, site, clock, recorder, now=lambda: now[0])

    cache.get("https://prs.test/a", source="prs")
    now[0] += timedelta(days=8)
    cache.get("https://prs.test/b", source="prs")  # same instance, weeks later
    assert site.calls.count("/robots.txt") == 2


def test_unchanged_robots_recheck_counts_as_fresh(tmp_path, clock, recorder) -> None:
    """Re-fetching identical robots.txt must reset its age, or it would be re-fetched forever."""
    site = Site()
    for p in ("/a", "/b", "/c"):
        site.routes[p] = [httpx.Response(200, content=PDF + p.encode())]
    now = [datetime(2026, 10, 20, tzinfo=UTC)]

    def fresh_cache() -> Cache:
        return make_cache(tmp_path, site, clock, recorder, now=lambda: now[0])

    fresh_cache().get("https://prs.test/a", source="prs")
    now[0] += timedelta(days=8)
    fresh_cache().get("https://prs.test/b", source="prs")  # stale: re-check (identical bytes)
    now[0] += timedelta(days=1)
    fresh_cache().get("https://prs.test/c", source="prs")  # confirmed a day ago: no re-check
    assert site.calls.count("/robots.txt") == 2
