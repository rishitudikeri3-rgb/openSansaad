from datetime import date
from pathlib import Path

from opensansad.db.models import DocType, Source
from opensansad.ingest import discover
from opensansad.ingest.manifest import DocEntry, Manifest, load_manifest
from opensansad.ingest.sources import eparlib, sansad
from opensansad.ingest.sources.base import DocRef

FIX = Path(__file__).parent / "fixtures"
ARB = "ARBITRATION AND CONCILIATION"


def _bills():
    found, _, _ = sansad.parse_bills("u", (FIX / "sansad_getbills_arbitration.json").read_bytes())
    return discover.matching(found, ARB)


def _manifest(*urls: str) -> Manifest:
    return Manifest({}, [DocEntry(url=u, source=Source.SANSAD, doc_type=DocType.BILL,
                                  title="t", act=None, number=None) for u in urls])


def test_matching_keeps_only_this_acts_bills() -> None:
    titles = {b.title.upper() for b in _bills()}
    assert all(ARB in t for t in titles)
    assert not any("INTERNATIONAL ARBITRATION CENTRE" in t for t in titles)


def test_bill_blocks_are_valid_manifest_entries(tmp_path: Path) -> None:
    blocks = discover.bill_blocks(_bills(), _manifest(), act="26/1996")
    text = "\n\n".join(blocks)
    assert "252C_2015_LS_Eng.pdf" in text  # 2015 Bill as passed by Lok Sabha
    assert "Private Member Bill: attribute to the member" in text  # the 2022 Bill
    assert "not PDF/HTML" in text  # errata .doc
    assert "status Assented; introduced 2015-12-03; passed by Lok Sabha 2015-12-17" in text

    (tmp_path / "manifest.toml").write_text(
        '[[act]]\nid = "26/1996"\nshort_title = "The Arbitration and Conciliation Act, 1996"\n\n'
        + text)
    m = load_manifest(tmp_path / "manifest.toml")
    assert len(m.documents) == len(blocks) and all(d.act == (26, 1996) for d in m.documents)


def test_bill_blocks_skip_manifest_urls_and_hindi() -> None:
    refs = [d for b in _bills() for d in b.docs]
    known = refs[0].url
    text = "\n".join(discover.bill_blocks(_bills(), _manifest(known)))
    assert known not in text
    assert '# act = ""' in text  # no --act: a prompt, never a guess
    hindi = [DocRef("https://x/h.pdf", Source.SANSAD, DocType.BILL, "h", lang="hi")]
    assert discover.new_refs(hindi, _manifest()) == []


def test_debate_window_includes_days_before_a_stage() -> None:
    bills = _bills()
    assert discover.related(bills, date(2015, 12, 17)) == [
        "The Arbitration and Conciliation (Amendment) Bill, 2015: passed by Lok Sabha"]
    # 16 Dec 2015: the discussion that concluded on the 17th
    assert discover.related(bills, date(2015, 12, 16)) == [
        "The Arbitration and Conciliation (Amendment) Bill, 2015: during passage"]
    assert discover.related(bills, date(2017, 1, 1)) == []


def test_debate_comment_from_elibrary_item() -> None:
    items, _ = eparlib.parse_search(
        "u", (FIX / "elibrary_search_arbitration_bill.json").read_bytes())
    item = next(i for i in items if i.date == date(2015, 12, 16))
    comment = discover.debate_comment(item, _bills())
    assert comment.startswith("2015-12-16, LS 16 session VI;")
    assert "names The Arbitration and Conciliation (Amendment) Bill, 2015: during passage" \
        in comment
    unrelated = next(i for i in items if i.date and not discover.related(_bills(), i.date))
    assert "probably unrelated" in discover.debate_comment(unrelated, _bills())


def test_cli_prints_candidates_through_the_cache(tmp_path: Path, monkeypatch, capsys) -> None:
    import httpx
    import pytest

    from opensansad.ingest.cache import Cache
    from opensansad.ingest.ratelimit import MemoryGate
    from opensansad.ingest.store import RawStore

    payload = (FIX / "sansad_getbills_arbitration.json").read_bytes()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(str(request.url))
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    class _Rec:
        def record(self, *a) -> None:
            pass

    monkeypatch.setattr(Cache, "from_settings", classmethod(lambda cls: Cache(
        RawStore(tmp_path / "raw"), MemoryGate(sleep=lambda s: None), _Rec(),
        user_agent="OpenSansad/test (contact: test@example.org)",
        transport=httpx.MockTransport(handler), sleep=lambda s: None)))
    (tmp_path / "manifest.toml").write_text(
        '[[act]]\nid = "26/1996"\nshort_title = "The Arbitration and Conciliation Act, 1996"\n')
    argv = ["--manifest", str(tmp_path / "manifest.toml"), "--act", "26/1996", "bills",
            "Arbitration"]

    with pytest.raises(SystemExit):  # --act without --match: refused
        discover.main(argv)
    assert seen == []

    assert discover.main(argv + ["--match", ARB]) == 0
    out = capsys.readouterr().out
    assert seen == [sansad.bills_url("Arbitration")]
    assert "252C_2015_LS_Eng.pdf" in out and 'act = "26/1996"' in out
    assert out.count("[[document]]") == out.count('act = "26/1996"')  # every block filled
    assert "INTERNATIONAL ARBITRATION CENTRE" not in out.upper()


def test_names_bill_by_name_and_year() -> None:
    by_year = {b.title.split(",")[-1].strip()[:4]: b for b in _bills()}
    b2015, b2018, b1996 = by_year["2015"], by_year["2018"], by_year["1996"]
    t = ("Further discussion on statutory resolution regarding Disapproval of Arbitration and "
         "Conciliation (Amendment ) Ordinance and Arbitration and Conciliation (Amendment) "
         "Bill, 2015")
    assert discover.names_bill(b2015, t) and not discover.names_bill(b2018, t)  # year differs
    assert not discover.names_bill(b1996, t)  # "(Amendment) Bill" is not "Bill"
    t1996 = ("Statutory Resolution Re: Disapproval of Arbitration and Conciliation Third "
             "Ordinance; and the Arbitration and Conciliation Bill")
    assert discover.names_bill(b1996, t1996)  # no year in the title: name alone
    assert not discover.names_bill(b2015, "Need to construct underpass at Shalimar station")


def test_window_only_items_are_marked_apart() -> None:
    items, _ = eparlib.parse_search(
        "u", (FIX / "elibrary_search_arbitration_bill.json").read_bytes())
    item = next(i for i in items if i.date == date(2015, 12, 16))
    from dataclasses import replace
    other = replace(item, title="Need to construct underpass at railway crossing")
    comment = discover.debate_comment(other, _bills())
    assert "# date within The Arbitration" in comment and "does not name the bill" in comment


class _FakeEparlib:
    def __init__(self, results):
        self.results = results

    def search(self, cache, query, *, max_pages):
        return self.results[query]


def test_merged_search_dedupes_and_flags_partial() -> None:
    items, _ = eparlib.parse_search(
        "u", (FIX / "elibrary_search_arbitration_bill.json").read_bytes())
    a, b = items[0], items[1]
    fake = _FakeEparlib({"q1": ([a, b], 225), "q2": ([b, a], 2)})
    merged, warnings = discover.merged_search(fake, None, ["q1", "q2"], max_pages=4)
    assert [i.handle for i in merged] == [a.handle, b.handle]
    assert warnings == ["# PARTIAL: 'q1' returned 2 of 225 hits: raise --max-pages"]


def test_files_need_only(tmp_path: Path, capsys) -> None:
    import pytest

    with pytest.raises(SystemExit):
        discover.main(["--manifest", str(tmp_path / "m.toml"), "debates-ls", "q",
                       "--bills", "Arbitration", "--files"])
    assert "--files needs --only" in capsys.readouterr().err
