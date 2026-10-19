from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensansad.db.base import Base
from opensansad.db.models import Acquisition, Act, DocFormat, DocType, Document, FetchLog, Source


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _doc(act: Act | None = None, **overrides) -> Document:
    fields = dict(
        act=act,
        doc_type=DocType.PRINCIPAL,
        source=Source.INDIACODE,
        url="https://www.indiacode.nic.in/handle/123456789/1978",
        sha256="a" * 64,
        fetched_at=datetime(2026, 10, 19, tzinfo=UTC),
        acquisition=Acquisition.MANUAL,
        format=DocFormat.PDF,
        raw_path="data/raw/indiacode/abc.pdf",
    )
    fields.update(overrides)
    return Document(**fields)


def test_act_document_round_trip(session: Session) -> None:
    act = Act(short_title="The Arbitration and Conciliation Act, 1996", act_number=26, year=1996)
    session.add(_doc(act))
    session.commit()

    doc = session.query(Document).one()
    assert doc.act.short_title.startswith("The Arbitration")
    assert doc.doc_type is DocType.PRINCIPAL
    assert doc.is_scanned is None  # unknown until extraction


def test_enums_stored_as_values(session: Session) -> None:
    session.add(_doc(doc_type=DocType.EXTERNAL_REPORT, source=Source.OTHER))
    session.commit()
    row = session.execute(text("SELECT doc_type, source, acquisition FROM documents")).one()
    assert tuple(row) == ("external_report", "other", "manual")


def test_invalid_doc_type_rejected_by_check_constraint(session: Session) -> None:
    session.add(_doc())
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE documents SET doc_type = 'opinion_piece'"))


def test_same_url_new_bytes_is_new_row_but_exact_duplicate_rejected(session: Session) -> None:
    session.add_all([_doc(sha256="a" * 64), _doc(sha256="b" * 64)])
    session.commit()
    assert session.query(Document).count() == 2

    session.add(_doc(sha256="a" * 64))
    with pytest.raises(IntegrityError):
        session.commit()


def test_act_number_year_unique(session: Session) -> None:
    session.add(Act(short_title="A", act_number=26, year=1996))
    session.add(Act(short_title="B", act_number=26, year=1996))
    with pytest.raises(IntegrityError):
        session.commit()


def test_fetch_log_defaults(session: Session) -> None:
    session.add(FetchLog(url="https://prsindia.org/x", status_code=200))
    session.commit()
    assert session.query(FetchLog).one().attempted_at is not None


def test_constraint_names_follow_convention() -> None:
    names = {c.name for c in Base.metadata.tables["documents"].constraints}
    assert {"pk_documents", "uq_documents_url", "ck_documents_doc_type",
            "fk_documents_act_id_acts"} <= names
