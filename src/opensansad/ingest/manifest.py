"""The ingestion manifest, `data/manifest.toml` (committed).

Corpus decisions are made here, by hand, and nowhere else: which act a document belongs to,
what kind of document it is, and an instrument's own number ("(Amendment) Act, 2015" is
Act 3 of 2016). Adapters never decide these (sources/base.py); the document's own text
is used only to check them (a Gazette masthead, see persist.py).

    [[act]]
    id = "26/1996"
    short_title = "The Arbitration and Conciliation Act, 1996"

    [[document]]
    url = "https://egazette.gov.in/WriteReadData/2021/225832.pdf"
    source = "egazette"
    doc_type = "amending"
    title = "The Arbitration and Conciliation (Amendment) Act, 2021"
    act = "26/1996"        # the act this document is about
    number = "3/2021"      # the instrument's own number: amending act / ordinance
                           # (a corrigendum states instead: corrects = "14/2020")

    [[document]]           # a hand-downloaded file: source, doc_type, title and authority
    url = "https://..."    # come from data/manual_imports.jsonl (must agree if repeated)
    manual = true
    act = "26/1996"

Optional keys: authority ("primary" | "secondary"), html_selector, note, and
masthead_check = false (with a note saying why) for a Gazette whose masthead is misread.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from opensansad.db.models import Authority, DocType, Source
from opensansad.ingest.manual import ManualImport, read_log
from opensansad.ingest.store import normalize_url

MANIFEST_NAME = "manifest.toml"

_NUMBER = re.compile(r"(\d+)/(\d{4})")
_ACT_KEYS = {"id", "short_title"}
_DOC_KEYS = {"url", "source", "doc_type", "title", "act", "number", "corrects", "authority",
             "manual", "html_selector", "note", "masthead_check"}
_FROM_LOG = ("source", "doc_type", "title", "authority")


class ManifestError(ValueError):
    """The manifest is invalid. Lists every problem found, not just the first."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{path}:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class ActEntry:
    number: int
    year: int
    short_title: str


@dataclass(frozen=True)
class DocEntry:
    url: str  # normalized: the cache key
    source: Source
    doc_type: DocType
    title: str
    act: tuple[int, int] | None  # (number, year) of an [[act]] entry
    number: tuple[int, int] | None  # the instrument's own (number, year)
    corrects: tuple[int, int] | None = None  # corrigendum: (number, year) it corrects
    authority: Authority = Authority.PRIMARY
    manual: bool = False
    html_selector: str | None = None
    note: str | None = None
    masthead_check: bool = True


@dataclass(frozen=True)
class Manifest:
    acts: dict[tuple[int, int], ActEntry]
    documents: list[DocEntry]


def parse_number(value: object) -> tuple[int, int] | None:
    """'3/2016' -> (3, 2016); None for anything else."""
    m = _NUMBER.fullmatch(value) if isinstance(value, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def load_manifest(path: Path, manual_log: Path | None = None) -> Manifest:
    """Read and check the manifest. `manual_log` defaults to manual_imports.jsonl beside it."""
    from opensansad.ingest.manual import LOG_NAME

    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(path, [f"not valid TOML: {e}"]) from e
    log = read_log(manual_log or path.parent / LOG_NAME)
    problems: list[str] = []

    if unknown := set(raw) - {"act", "document"}:
        problems.append(f"unknown top-level keys {sorted(unknown)}")

    acts: dict[tuple[int, int], ActEntry] = {}
    for i, a in enumerate(raw.get("act", [])):
        where = f"act #{i + 1}"
        if unknown := set(a) - _ACT_KEYS:
            problems.append(f"{where}: unknown keys {sorted(unknown)}")
        key = parse_number(a.get("id"))
        title = a.get("short_title")
        if not key:
            problems.append(f"{where}: id must look like '26/1996', got {a.get('id')!r}")
        elif key in acts:
            problems.append(f"{where}: duplicate act {a['id']}")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{where}: short_title is required")
        if key and isinstance(title, str) and title.strip():
            acts[key] = ActEntry(key[0], key[1], " ".join(title.split()))

    documents: list[DocEntry] = []
    seen: set[str] = set()
    for i, d in enumerate(raw.get("document", [])):
        where = f"document #{i + 1} ({d.get('url', 'no url')})"
        entry, errors = _document(d, acts, log)
        problems += [f"{where}: {e}" for e in errors]
        if isinstance(url := d.get("url"), str):
            if normalize_url(url) in seen:
                problems.append(f"{where}: duplicate url")
            seen.add(normalize_url(url))
        if entry:
            documents.append(entry)

    if problems:
        raise ManifestError(path, problems)
    return Manifest(acts, documents)


def _document(d: dict, acts: dict, log: list[ManualImport]) -> tuple[DocEntry | None, list[str]]:
    errors: list[str] = []
    if unknown := set(d) - _DOC_KEYS:
        errors.append(f"unknown keys {sorted(unknown)}")
    url = d.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None, errors + ["url must be an absolute http(s) URL"]
    url = normalize_url(url)

    fields = dict(d)
    if d.get("manual"):
        records = [r for r in log if r.url == url]
        if not records:
            return None, errors + ["manual = true but the url is not in the manual import log"]
        rec = records[-1]
        for key in _FROM_LOG:
            if key in d and d[key] != getattr(rec, key):
                errors.append(f"{key} {d[key]!r} disagrees with the manual import log "
                              f"({getattr(rec, key)!r})")
            fields[key] = getattr(rec, key)

    def enum(cls, key: str, default=None):
        value = fields.get(key, default)
        try:
            return cls(value)
        except ValueError:
            errors.append(f"{key} must be one of {[m.value for m in cls]}, got {value!r}")
            return None

    source = enum(Source, "source")
    doc_type = enum(DocType, "doc_type")
    authority = enum(Authority, "authority", Authority.PRIMARY.value)
    title = fields.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title is required")

    act = None
    if "act" in d:
        act = parse_number(d["act"])
        if act not in acts:
            errors.append(f"act {d['act']!r} is not an [[act]] entry")
    number = corrects = None
    if "number" in d and not (number := parse_number(d["number"])):
        errors.append(f"number must look like '3/2016', got {d['number']!r}")
    if "corrects" in d and not (corrects := parse_number(d["corrects"])):
        errors.append(f"corrects must look like '14/2020', got {d['corrects']!r}")
    if d.get("masthead_check") is False and not d.get("note"):
        errors.append("masthead_check = false needs a note saying why")
    for key in ("html_selector", "note"):
        if key in d and not isinstance(d[key], str):
            errors.append(f"{key} must be a string")

    if errors:
        return None, errors
    return DocEntry(
        url=url,
        source=source,
        doc_type=doc_type,
        title=" ".join(title.split()),
        act=act,
        number=number,
        corrects=corrects,
        authority=authority,
        manual=bool(d.get("manual")),
        html_selector=d.get("html_selector"),
        note=" ".join(d["note"].split()) if d.get("note") else None,
        masthead_check=d.get("masthead_check", True),
    ), []
