"""PRS Legislative Research (prsindia.org) bill-tracker pages.

A bill page has a title, the ministry, a status timeline (stage | house | date) and
"Relevant Links" grouped under headings ("Original Text", "External Links", "PRS
Products"). PRS's own summaries are secondary analysis (doc_type external_report):
never a source for what the government or Parliament said (CLAUDE.md rule 5).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag

from opensansad.db.models import DocType, Source
from opensansad.extract.normalize import script_of
from opensansad.ingest.cache import Cache
from opensansad.ingest.sources.base import DocRef, LayoutChanged, SourceAdapter, absolute

BASE_URL = "https://prsindia.org"


@dataclass(frozen=True)
class StatusEvent:
    stage: str  # "Introduced", "Passed", "Referred to Standing Committee", ...
    house: str  # "Lok Sabha" / "Rajya Sabha" / ...
    date: date


@dataclass(frozen=True)
class PrsBill:
    url: str  # canonical bill-tracker URL
    title: str
    ministry: str | None
    events: tuple[StatusEvent, ...]
    docs: tuple[DocRef, ...]


def bill_url(title: str) -> str:
    """'The Arbitration and Conciliation (Amendment) Bill, 2019' -> its bill-tracker URL."""
    slug = re.sub(r"[^a-z0-9 -]", "", title.lower())
    return f"{BASE_URL}/billtrack/{re.sub(r'[ -]+', '-', slug).strip('-')}"


# Order matters: first match wins.
_RULES: list[tuple[re.Pattern, DocType]] = [
    (re.compile(r"\bordinance\b", re.I), DocType.ORDINANCE),
    (re.compile(r"committee\b.*\breport|\breport\b.*\bcommittee", re.I), DocType.COMMITTEE_REPORT),
    (re.compile(r"\bbill\b|बिल|विधेयक", re.I), DocType.BILL),
    (re.compile(r"\bact\b,?\s*\d{4}", re.I), DocType.AMENDING),  # refined below
]


def classify(label: str, heading: str) -> DocType | None:
    if heading.lower().startswith("prs") or label.lower().startswith("prs "):
        return DocType.EXTERNAL_REPORT
    for pattern, doc_type in _RULES:
        if pattern.search(label):
            if doc_type is DocType.AMENDING and "(amendment)" not in label.lower():
                return DocType.PRINCIPAL
            return doc_type
    return None


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


class PrsAdapter(SourceAdapter):
    source = Source.PRS
    html_selector = "article"

    def parse(self, url: str, html: bytes) -> list[DocRef]:
        return list(self.parse_bill(url, html).docs)

    def parse_bill(self, url: str, html: bytes) -> PrsBill:
        soup = BeautifulSoup(html, "lxml")
        article = soup.select_one("article")
        title = _text(article.select_one(".field-name-title-field h2")) if article else ""
        if not title:
            raise LayoutChanged(f"{url}: no PRS bill title (article .field-name-title-field h2)")
        canonical = soup.select_one('link[rel="canonical"][href]')
        page_url = absolute(url, canonical["href"]) if canonical else absolute(url, url)

        events = []
        for li in article.select("ul.bp-link-slides > li"):
            stage = _text(li.select_one(".field-name-field-own-status .field-item"))
            house = _text(li.select_one(".field-name-field-own-status-title .field-item"))
            when = _text(li.select_one(".field-name-field-own-status-date .field-item"))
            if stage and when:
                day = datetime.strptime(when, "%b %d, %Y").date()
                events.append(StatusEvent(stage, house, day))

        docs, seen = [], set()
        for block in article.select(".relevant_links_block > div"):
            heading = _text(block.find("h4"))
            for a in block.select("a[href]"):
                label = _text(a)
                doc_url = absolute(page_url, a["href"])
                if not label or doc_url in seen:
                    continue
                seen.add(doc_url)
                docs.append(DocRef(
                    url=doc_url,
                    source=self.source,
                    doc_type=classify(label, heading),
                    title=f"{title}: {label}",
                    lang="hi" if script_of(label) == "hi" else "en",
                    found_on=page_url,
                ))

        return PrsBill(
            url=page_url,
            title=title,
            ministry=_text(article.select_one(".field-name-field-ministry .field-item")) or None,
            events=tuple(events),
            docs=tuple(docs),
        )

    def discover(self, cache: Cache, title_or_url: str) -> PrsBill:
        """Fetch a bill-tracker page (by URL, or by the bill's exact title) and parse it."""
        url = title_or_url if title_or_url.startswith("http") else bill_url(title_or_url)
        doc = self.fetch(cache, url)
        return self.parse_bill(url, doc.path.read_bytes())
