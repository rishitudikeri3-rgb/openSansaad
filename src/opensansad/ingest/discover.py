"""Discovery: candidate manifest entries from the sources' listings, for review by hand.

Prints `[[document]]` TOML blocks for documents a listing shows that the manifest does not
have yet. Nothing is written: whether a document belongs to the corpus, which act it is
about and an instrument's number are decided by whoever pastes the block into
data/manifest.toml (see manifest.py). Values a listing cannot be trusted for are left as
commented prompts.

    uv run python -m opensansad.ingest.discover bills "Arbitration" \\
        --match "ARBITRATION AND CONCILIATION" --act 26/1996
    uv run python -m opensansad.ingest.discover debates-ls "Arbitration and Conciliation" \\
        "Arbitration and Conciliation (Amendment) Bill" \\
        --bills "Arbitration" --match "ARBITRATION AND CONCILIATION"
    uv run python -m opensansad.ingest.discover --act 26/1996 debates-ls "..." --bills ... \\
        --match ... --files --only 123456789/454809 123456789/469904
    uv run python -m opensansad.ingest.discover debates-rs 249 2019-07-18

Debate candidates are annotated with the bills whose passage they fall within (from
introduction to the last stage reached), naming the stage when the date is one (introduced,
passed by a House, assent). A discussion can span days before a stage (16 Dec 2015 began
the debate the Bill passed on 17 Dec), so a window, not only exact stage dates, is the rule.
A long passage (the 1995 Bill: 15 months) puts a lot of unrelated business in its window, so
items whose title names the bill are marked apart from those that only fall within it.

One search query is not enough (the 2015 Bill's introduction is found by "... (Amendment)
Bill" but not within the first 200 hits of "Arbitration and Conciliation"): several queries
are merged, and a result capped by --max-pages is flagged PARTIAL in the output itself.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from opensansad.ingest.manifest import Manifest
from opensansad.ingest.sources.base import DocRef
from opensansad.ingest.sources.eparlib import DebateItem
from opensansad.ingest.sources.sansad import SansadBill
from opensansad.ingest.store import normalize_url

_STAGES = (("introduced", "introduced"), ("passed_ls", "passed by Lok Sabha"),
           ("passed_rs", "passed by Rajya Sabha"), ("assented", "assent"))


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a valid TOML basic string


def block(ref: DocRef, *, act: str | None = None, note: str | None = None,
          comments: Iterable[str] = ()) -> str:
    """One candidate [[document]] block. Unknown values stay commented prompts."""
    lines = [f"# {c}" for c in comments]
    lines += ["[[document]]", f"url = {_q(ref.url)}", f"source = {_q(ref.source.value)}"]
    lines.append(f"doc_type = {_q(ref.doc_type.value)}" if ref.doc_type
                 else '# doc_type = ""  # not classified by the listing: decide by hand')
    lines.append(f"title = {_q(ref.title)}")
    lines.append(f"act = {_q(act)}" if act else '# act = ""  # which act: decide by hand')
    if note:
        lines.append(f"note = {_q(note)}")
    return "\n".join(lines)


def new_refs(refs: Iterable[DocRef], manifest: Manifest, *,
             langs: tuple[str, ...] = ("en", "mixed")) -> list[DocRef]:
    """Refs not yet in the manifest, in the wanted languages, each URL once."""
    known = {e.url for e in manifest.documents}
    out, seen = [], set()
    for r in refs:
        url = normalize_url(r.url)
        if url in known or url in seen or r.lang not in langs:
            continue
        seen.add(url)
        out.append(r)
    return out


def matching(bills: Iterable[SansadBill], pattern: str | None) -> list[SansadBill]:
    rx = re.compile(pattern, re.I) if pattern else None
    return [b for b in bills if rx is None or rx.search(b.title)]


def bill_note(b: SansadBill) -> str:
    parts = [f"{b.house} Bill No. {b.number} of {b.year}", f"{b.bill_type or '?'} Bill",
             f"status {b.status}"]
    parts += [f"{label} {getattr(b, attr)}" for attr, label in _STAGES if getattr(b, attr)]
    return "; ".join(parts)


def bill_blocks(bills: Iterable[SansadBill], manifest: Manifest, *,
                act: str | None = None) -> list[str]:
    out = []
    for b in bills:
        for ref in new_refs(b.docs, manifest):
            comments = []
            if b.bill_type and b.bill_type != "Government":
                comments.append(f"{b.bill_type} Bill: attribute to the member, not the "
                                "government")
            if ref.doc_type is None or ref.url.lower().split("?")[0].endswith(".doc"):
                comments.append("not PDF/HTML: cached as format 'other', no text extracted")
            out.append(block(ref, act=act, note=bill_note(b), comments=comments))
    return out


def bill_stages(b: SansadBill, day: date) -> str | None:
    """How `day` relates to the bill's passage: a stage name, "during passage", or None."""
    dates = [(getattr(b, attr), label) for attr, label in _STAGES if getattr(b, attr)]
    if not dates or not min(d for d, _ in dates) <= day <= max(d for d, _ in dates):
        return None
    title = " ".join(b.title.split())
    on = [label for d, label in dates if d == day]
    return f"{title}: {', '.join(on)}" if on else f"{title}: during passage"


def related(bills: Iterable[SansadBill], day: date | None) -> list[str]:
    return [m for b in bills if day and (m := bill_stages(b, day))]


_YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("&", "and"))


def names_bill(b: SansadBill, title: str) -> bool:
    """Whether an item title names the bill: its name up to the year (leading "The" and
    spacing / punctuation ignored), and its year unless the title gives none ("... and the
    Arbitration and Conciliation Bill", 1996)."""
    name = re.sub(r"^\s*the\s+", "", b.title, flags=re.I)
    year = _YEAR.search(name)
    core = _letters(name[:year.start()] if year else name)
    years = set(_YEAR.findall(title))
    return bool(core) and core in _letters(title) and (
        not years or not year or year.group() in years)


def debate_comment(item: DebateItem, bills: list[SansadBill]) -> str:
    when = f"{item.date}" if item.date else "undated"
    head = (f"{when}, LS {item.lok_sabha or '?'} session {item.session or '?'}; "
            f"{item.debate_type or '?'}; members: {', '.join(item.members) or '?'}")
    found = [(names_bill(b, item.title), m) for b in bills
             if item.date and (m := bill_stages(b, item.date))]
    named = [m for n, m in found if n]
    if named:
        match = "names " + " | ".join(named)
    elif found:
        match = ("date within " + " | ".join(m for _, m in found)
                 + " (title does not name the bill)")
    else:
        match = "outside every bill's passage: probably unrelated (search results are noisy)"
    return f"{head}\n# {match}"


def merged_search(eparlib, cache, queries: Iterable[str], *,
                  max_pages: int) -> tuple[list[DebateItem], list[str]]:
    """Items of several queries, each once (first query's order), plus a PARTIAL warning
    for every query whose results were capped."""
    items: dict[str, DebateItem] = {}
    warnings = []
    for q in queries:
        found, total = eparlib.search(cache, q, max_pages=max_pages)
        if len(found) < total:
            warnings.append(f"# PARTIAL: {q!r} returned {len(found)} of {total} hits: "
                            "raise --max-pages")
        for i in found:
            items.setdefault(i.handle, i)
    return list(items.values()), warnings


def _cli_manifest(path: Path | None) -> Manifest:
    from opensansad.config import get_settings
    from opensansad.ingest.manifest import MANIFEST_NAME, load_manifest

    return load_manifest(path or get_settings().data_dir / MANIFEST_NAME)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.ingest.discover",
                                     description="Print candidate manifest entries.")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--act", help="act id to fill in, e.g. 26/1996 (else a prompt)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("bills", help="bill stage texts from sansad.in getBills")
    p.add_argument("name")
    p.add_argument("--match", help="regex the bill title must match")
    p = sub.add_parser("debates-ls", help="Lok Sabha debates from elibrary.sansad.in")
    p.add_argument("query", nargs="+", help="one or more search queries (results merged)")
    p.add_argument("--bills", required=True, help="getBills name, for passage dates")
    p.add_argument("--match", help="regex for the bills whose passage counts")
    p.add_argument("--only", nargs="+", metavar="HANDLE",
                   help="only these items (e.g. 123456789/454809), as chosen by hand")
    p.add_argument("--files", action="store_true",
                   help="print [[document]] blocks for the --only items (one request each)")
    p.add_argument("--max-pages", type=int, default=10, help="per query, 50 hits a page")
    p = sub.add_parser("debates-rs", help="Rajya Sabha debates for one sitting day")
    p.add_argument("session", type=int)
    p.add_argument("day", type=date.fromisoformat)
    args = parser.parse_args(argv)

    from opensansad.ingest.cache import Cache
    from opensansad.ingest.sources.eparlib import EparlibAdapter
    from opensansad.ingest.sources.sansad import SansadAdapter

    if args.act and args.cmd == "bills" and not args.match:
        # A name search also returns other acts' bills (NDIAC for "Arbitration"): filling
        # in the act for all of them would be the listing deciding linkage.
        parser.error("--act with bills needs --match, so only this act's bills get it")
    if args.cmd == "debates-ls" and args.files and not args.only:
        # Every item in a passage window would be fetched: for the 1995 Bill that is
        # 15 months of unrelated business. Files are for items chosen by hand.
        parser.error("--files needs --only: choose the items from a listing first")
    manifest = _cli_manifest(args.manifest)
    sansad = SansadAdapter.from_settings()
    out: list[str] = []
    with Cache.from_settings() as cache:
        if args.cmd == "bills":
            out = bill_blocks(matching(sansad.bills(cache, args.name), args.match), manifest,
                              act=args.act)
        elif args.cmd == "debates-ls":
            bills = matching(sansad.bills(cache, args.bills), args.match)
            eparlib = EparlibAdapter.from_settings()
            items, out = merged_search(eparlib, cache, args.query, max_pages=args.max_pages)
            print(f"# {len(items)} distinct items", file=sys.stderr)
            if args.only:
                by_handle = {i.handle: i for i in items}
                out += [f"# NOT FOUND in these results: {h}" for h in args.only
                        if h not in by_handle]
                items = [by_handle[h] for h in args.only if h in by_handle]
            for item in items:
                comment = debate_comment(item, bills)
                if not args.files:
                    out.append(f"# {item.handle}: {item.title}\n# {comment}")
                    continue
                for ref in new_refs(eparlib.files(cache, item), manifest):
                    out.append(block(ref, act=args.act, comments=[comment]))
        else:
            out = [block(r, act=args.act) for r in
                   new_refs(sansad.rs_debates(cache, args.session, args.day), manifest)]
    print("\n\n".join(out) if out else "# nothing new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
