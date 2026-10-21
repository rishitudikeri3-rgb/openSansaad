import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from opensansad.db.base import Base
from opensansad.db.models import Acquisition, Act, Authority, DocType, Document, Source
from opensansad.extract.document import EXTRACTOR_VERSION
from opensansad.ingest.manifest import ManifestError, load_manifest
from opensansad.ingest.persist import ingest, stale
from opensansad.ingest.store import RawStore

FIX = Path(__file__).parent / "fixtures"
GAZETTE_URL = "https://egazette.gov.in/WriteReadData/2021/225832.pdf"
PRS_URL = "https://prsindia.org/files/bills_acts/bills_parliament/2019/Bill%202019.pdf"
MANUAL_URL = "https://www.legislative.gov.in/static/uploads/2025/07/note.pdf"

ACT = '''
[[act]]
id = "26/1996"
short_title = "The Arbitration and Conciliation Act, 1996"
'''

GAZETTE = f'''
[[document]]
url = "{GAZETTE_URL}"
source = "egazette"
doc_type = "amending"
title = "The Arbitration and Conciliation (Amendment) Act, 2021"
act = "26/1996"
number = "3/2021"
'''


@pytest.fixture
def data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "raw").mkdir(parents=True)
    return d


@pytest.fixture
def store(data: Path) -> RawStore:
    s = RawStore(data / "raw")
    _put(s, GAZETTE_URL, "egazette", FIX / "egazette_act3_2021_p1.pdf")
    return s


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _put(store: RawStore, url: str, source: str, file: Path,
         acquisition: Acquisition = Acquisition.FETCHED) -> None:
    store.write(url, source, [file.read_bytes()], status_code=200,
                content_type="application/pdf", acquisition=acquisition)


def _manifest(data: Path, body: str, log: list[dict] | None = None):
    (data / "manifest.toml").write_text(ACT + body)
    if log is not None:
        (data / "manual_imports.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in log))
    return load_manifest(data / "manifest.toml")


def _log_record(sha: str, **overrides) -> dict:
    return dict(url=MANUAL_URL, sha256=sha, imported_at="2026-10-21T12:00:00+00:00",
                source="other", doc_type="notification", title="Explanatory note",
                authority="primary", original_filename="note.pdf", note=None) | overrides


# ---- manifest ------------------------------------------------------------------------

def test_manifest_reports_every_problem(data: Path) -> None:
    body = f'''
[[document]]
url = "{GAZETTE_URL}"
source = "egazette"
doc_type = "statute"
title = "x"
act = "27/1996"
colour = "red"

[[document]]
url = "{PRS_URL}"
source = "prs"
doc_type = "bill"
title = "y"
number = "Bill 12"
masthead_check = false

[[document]]
url = "{PRS_URL.replace('%20', ' ')}"
source = "prs"
doc_type = "bill"
title = "z"
'''
    with pytest.raises(ManifestError) as err:
        _manifest(data, body)
    text = "\n".join(err.value.problems)
    for expected in ("unknown keys ['colour']", "doc_type must be one of",
                     "act '27/1996' is not an [[act]] entry", "number must look like",
                     "masthead_check = false needs a note", "duplicate url"):
        assert expected in text, expected
    assert len(err.value.problems) == 6


def test_manifest_manual_entry_takes_metadata_from_log(data: Path) -> None:
    body = f'''
[[document]]
url = "{MANUAL_URL}"
manual = true
act = "26/1996"
'''
    m = _manifest(data, body, [_log_record("a" * 64)])
    (entry,) = m.documents
    assert (entry.source, entry.doc_type, entry.title) == (
        Source.OTHER, DocType.NOTIFICATION, "Explanatory note")

    with pytest.raises(ManifestError, match="disagrees with the manual import log"):
        _manifest(data, body.replace("manual = true", 'manual = true\ndoc_type = "judgment"'),
                  [_log_record("a" * 64)])


# ---- ingest --------------------------------------------------------------------------

def test_ingest_adds_extracts_and_reads_masthead(data: Path, store: RawStore,
                                                 session: Session) -> None:
    report = ingest(_manifest(data, GAZETTE), session, store, ocr=None)
    assert report.ok and report.counts() == {"added": 1, "extracted": 1}

    doc = session.scalars(select(Document)).one()
    assert doc.act.act_number == 26 and (doc.amending_act_number, doc.amending_year) == (3, 2021)
    assert doc.raw_path.startswith("raw/egazette/") and doc.text_path.startswith("text/egazette/")
    assert doc.extractor_version == EXTRACTOR_VERSION and len(doc.text_sha256) == 64
    text = (data / doc.text_path).read_text()
    assert "ARBITRATION AND CONCILIATION (AMENDMENT) ACT, 2021" in text
    assert doc.meta["kind"] == "act" and (doc.meta["number"], doc.meta["year"]) == (3, 2021)
    assert doc.meta["doc_type"] == "amending" and doc.meta["assent_date"] == "2021-03-11"
    assert doc.acquisition is Acquisition.FETCHED and doc.authority is Authority.PRIMARY


def test_ingest_is_idempotent(data: Path, store: RawStore, session: Session) -> None:
    manifest = _manifest(data, GAZETTE)
    ingest(manifest, session, store, ocr=None)
    again = ingest(manifest, session, store, ocr=None)
    assert again.counts() == {"unchanged": 1, "extracted": 0}
    assert session.scalar(select(func.count()).select_from(Document)) == 1
    assert session.scalar(select(func.count()).select_from(Act)) == 1


def test_manifest_edit_updates_row(data: Path, store: RawStore, session: Session) -> None:
    ingest(_manifest(data, GAZETTE), session, store, ocr=None)
    edited = GAZETTE.replace('Act, 2021"', 'Act, 2021 (Gazette copy)"')
    report = ingest(_manifest(data, edited), session, store, ocr=None)
    assert report.counts() == {"updated": 1, "extracted": 0}
    assert session.scalars(select(Document)).one().title.endswith("(Gazette copy)")


def test_changed_or_stale_text_is_re_extracted(data: Path, store: RawStore,
                                               session: Session) -> None:
    manifest = _manifest(data, GAZETTE)
    ingest(manifest, session, store, ocr=None)
    doc = session.scalars(select(Document)).one()

    (data / doc.text_path).write_text("edited by hand")
    assert ingest(manifest, session, store, ocr=None).results[0].extracted

    doc.extractor_version = "1"
    session.commit()
    assert [d.id for d in stale(session)] == [doc.id]
    report = ingest(manifest, session, store, ocr=None)
    assert report.results[0].extracted and report.stale == []


def test_masthead_disagreeing_with_manifest_is_refused(data: Path, store: RawStore,
                                                       session: Session) -> None:
    wrong = GAZETTE.replace('number = "3/2021"', 'number = "3/2020"')
    report = ingest(_manifest(data, wrong), session, store, ocr=None)
    (result,) = report.results
    assert result.status == "error" and not report.ok
    assert "masthead says No. 3 of 2021, manifest says number 3/2020" in result.detail
    assert session.scalar(select(func.count()).select_from(Document)) == 0

    wrong_type = GAZETTE.replace('doc_type = "amending"', 'doc_type = "principal"')
    (result,) = ingest(_manifest(data, wrong_type), session, store, ocr=None).results
    assert "masthead says amending (act), manifest says principal" in result.detail


def test_masthead_check_can_be_waived_with_a_note(data: Path, store: RawStore,
                                                  session: Session) -> None:
    waived = GAZETTE.replace('number = "3/2021"', 'number = "3/2020"\nmasthead_check = false\n'
                             'note = "test: masthead deliberately ignored"')
    assert ingest(_manifest(data, waived), session, store, ocr=None).ok


def test_gazette_copy_from_other_source_is_checked(data: Path, store: RawStore,
                                                   session: Session) -> None:
    _put(store, PRS_URL, "prs", FIX / "egazette_act3_2021_p1.pdf")
    body = GAZETTE.replace(GAZETTE_URL, PRS_URL).replace('"egazette"', '"prs"')
    wrong = body.replace('number = "3/2021"', 'number = "3/2022"')
    (result,) = ingest(_manifest(data, wrong), session, store, ocr=None).results
    assert result.status == "error" and "No. 3 of 2021" in result.detail


def test_non_gazette_copy_has_no_masthead(data: Path, store: RawStore,
                                          session: Session) -> None:
    _put(store, PRS_URL, "prs", FIX / "prs_arbitration_bill_2019.pdf")
    body = GAZETTE.replace(GAZETTE_URL, PRS_URL).replace('"egazette"', '"prs"')
    report = ingest(_manifest(data, body), session, store, ocr=None)
    assert report.ok and session.scalars(select(Document)).one().meta is None


def test_missing_raw_file_is_reported(data: Path, session: Session) -> None:
    report = ingest(_manifest(data, GAZETTE), session, RawStore(data / "raw"), ocr=None)
    assert [r.status for r in report.results] == ["missing"] and not report.ok


def test_manual_entry_needs_its_bytes_in_the_log(data: Path, store: RawStore,
                                                 session: Session) -> None:
    pdf = FIX / "egazette_so3154e_2019.pdf"
    _put(store, MANUAL_URL, "other", pdf, Acquisition.MANUAL)
    sha = store.latest(MANUAL_URL, "other").sha256
    body = f'''
[[document]]
url = "{MANUAL_URL}"
manual = true
act = "26/1996"
'''
    report = ingest(_manifest(data, body, [_log_record(sha)]), session, store, ocr=None)
    assert report.ok
    doc = session.scalars(select(Document)).one()
    assert doc.acquisition is Acquisition.MANUAL and doc.doc_type is DocType.NOTIFICATION

    (result,) = ingest(_manifest(data, body, [_log_record("b" * 64)]), session, store,
                       ocr=None).results
    assert result.status == "error" and "not in the manual import log" in result.detail


def test_raw_store_must_be_under_data_dir(data: Path, session: Session) -> None:
    with pytest.raises(ValueError, match="DATA_DIR/raw"):
        ingest(_manifest(data, GAZETTE), session, RawStore(data / "elsewhere"), ocr=None)


def test_committed_manifest_is_valid() -> None:
    m = load_manifest(Path(__file__).parents[1] / "data" / "manifest.toml")
    assert (26, 1996) in m.acts
    numbers = {d.number for d in m.documents if d.doc_type is DocType.AMENDING}
    assert numbers == {(3, 2016), (33, 2019), (3, 2021)}


CORRIGENDUM_URL = "https://egazette.gov.in/WriteReadData/2020/222961.pdf"
CORRIGENDUM = f'''
[[document]]
url = "{CORRIGENDUM_URL}"
source = "egazette"
doc_type = "notification"
title = "Corrigendum to Ordinance 14 of 2020"
act = "26/1996"
corrects = "14/2020"
'''


def _corrigendum_ocr(page, clip):
    # The corrigendum's body (with its heading and target) is an image: stand-in OCR text.
    return ("MINISTRY OF LAW AND JUSTICE\n(Legislative Department)\nCORRIGENDUM\n"
            "In the Arbitration and Conciliation (Amendment) Ordinance, 2020 (No. 14 of 2020),"
            " in section 3, for \"Subsitution\" read \"Substitution\".", 0.9)


def test_corrigendum_target_is_checked(data: Path, store: RawStore, session: Session) -> None:
    _put(store, CORRIGENDUM_URL, "egazette", FIX / "egazette_corrigendum_ord14_2020.pdf")
    assert ingest(_manifest(data, CORRIGENDUM), session, store, ocr=_corrigendum_ocr).ok

    wrong = CORRIGENDUM.replace('corrects = "14/2020"', 'corrects = "9/2015"')
    (result,) = ingest(_manifest(data, wrong), session, store, ocr=_corrigendum_ocr).results
    assert result.status == "error"
    assert "masthead says corrects No. 14 of 2020, manifest says corrects 9/2015" in result.detail

    missing = CORRIGENDUM.replace('corrects = "14/2020"\n', "")
    (result,) = ingest(_manifest(data, missing), session, store, ocr=_corrigendum_ocr).results
    assert "manifest says corrects nothing" in result.detail


def test_report_lists_rows_needing_a_decision(data: Path, store: RawStore,
                                              session: Session) -> None:
    _put(store, PRS_URL, "prs", FIX / "egazette_act3_2021_p1.pdf")
    copy = GAZETTE.replace(GAZETTE_URL, PRS_URL).replace('"egazette"', '"prs"')
    report = ingest(_manifest(data, GAZETTE + copy), session, store, ocr=None)
    assert report.ok and list(report.identical.values()) == [[GAZETTE_URL, PRS_URL]]

    # The copy leaves the manifest; the Gazette gets new bytes at the same URL.
    _put(store, GAZETTE_URL, "egazette", FIX / "egazette_ordinance14_2020_p1.pdf")
    renumbered = GAZETTE.replace('doc_type = "amending"', 'doc_type = "ordinance"').replace(
        'number = "3/2021"', 'number = "14/2020"')
    report = ingest(_manifest(data, renumbered), session, store, ocr=None)
    assert report.ok, report.results
    rows = session.scalar(select(func.count()).select_from(Document))
    assert [url for _, url in report.not_in_manifest] == [PRS_URL]
    assert [url for _, url in report.superseded] == [GAZETTE_URL]
    assert rows == 3 and report.identical  # the copy and the old Gazette bytes still match


def test_unexpected_error_stays_with_its_entry(data: Path, store: RawStore,
                                               session: Session) -> None:
    _put(store, PRS_URL, "prs", FIX / "prs_arbitration_bill_2019.pdf")
    bill = GAZETTE.replace(GAZETTE_URL, PRS_URL).replace('"egazette"', '"prs"').replace(
        'doc_type = "amending"', 'doc_type = "bill"').replace('number = "3/2021"\n', "")

    def extract(raw, fmt, **kw):
        if "prs" in str(raw):
            raise RuntimeError("corrupt PDF")
        from opensansad.extract.pipeline import extract_file
        return extract_file(raw, fmt, **kw)

    report = ingest(_manifest(data, bill + GAZETTE), session, store, ocr=None, extract=extract)
    assert [r.status for r in report.results] == ["error", "added"]
    assert report.results[0].detail == "RuntimeError: corrupt PDF"
    assert session.scalars(select(Document)).one().url == GAZETTE_URL
