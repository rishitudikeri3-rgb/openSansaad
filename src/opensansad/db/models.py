"""Phase 1 tables: acts, documents, fetch_log, clauses.

amendment_ops is added in step 4c alongside the amendment-op extractor.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opensansad.db.base import Base


class DocType(StrEnum):
    PRINCIPAL = "principal"
    AMENDING = "amending"
    GAZETTE = "gazette"
    DEBATE = "debate"
    COMMITTEE_REPORT = "committee_report"  # parliamentary committees
    BULLETIN = "bulletin"
    LIST_OF_BUSINESS = "list_of_business"
    BILL = "bill"
    ORDINANCE = "ordinance"
    NOTIFICATION = "notification"  # commencement / enforcement notifications
    JUDGMENT = "judgment"  # court rulings that change operative law
    EXTERNAL_REPORT = "external_report"  # Law Commission, expert / high-level committees


class Source(StrEnum):
    INDIACODE = "indiacode"
    EGAZETTE = "egazette"
    EPARLIB = "eparlib"
    SANSAD = "sansad"
    PRS = "prs"
    OTHER = "other"  # e.g. court or Law Commission websites; the url records where


class DocFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"
    OTHER = "other"


class Acquisition(StrEnum):
    FETCHED = "fetched"  # downloaded by cache.py
    MANUAL = "manual"  # downloaded by hand in a browser, imported via cache.import_local


class Authority(StrEnum):
    PRIMARY = "primary"  # issued by Parliament / Government / a court, or a faithful copy
    SECONDARY = "secondary"  # third-party compilation: usable as text, never cited for a claim


class NodeType(StrEnum):
    LONG_TITLE = "long_title"
    PREAMBLE = "preamble"
    ENACTING_FORMULA = "enacting_formula"
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    CLAUSE = "clause"
    SUBCLAUSE = "subclause"
    PROVISO = "proviso"
    EXPLANATION = "explanation"
    SCHEDULE = "schedule"
    ARTICLE = "article"  # of a treaty set out in a Schedule
    PARAGRAPH = "paragraph"  # numbered paragraph of a Schedule ("1." or "(1)")


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # Non-native enum (VARCHAR + CHECK): adding values later is a plain constraint change.
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Act(Base):
    __tablename__ = "acts"
    __table_args__ = (UniqueConstraint("act_number", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    short_title: Mapped[str] = mapped_column(String(512))
    act_number: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    india_code_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="act")


class Document(Base):
    __tablename__ = "documents"
    # Same URL may be re-fetched (force=True) with different bytes: each version is its own row.
    __table_args__ = (UniqueConstraint("url", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    act_id: Mapped[int | None] = mapped_column(ForeignKey("acts.id"), index=True)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"), index=True)
    source: Mapped[Source] = mapped_column(_enum(Source, "source"))
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acquisition: Mapped[Acquisition] = mapped_column(_enum(Acquisition, "acquisition"))
    authority: Mapped[Authority] = mapped_column(
        _enum(Authority, "authority"), default=Authority.PRIMARY, server_default="primary"
    )
    format: Mapped[DocFormat] = mapped_column(_enum(DocFormat, "format"))
    raw_path: Mapped[str] = mapped_column(Text)
    # Unknown (NULL) until extraction has run.
    is_scanned: Mapped[bool | None] = mapped_column(Boolean)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    # Image regions left unread (another script, or OCR below MIN_REGION_CONFIDENCE).
    ocr_regions_dropped: Mapped[int | None] = mapped_column(Integer)
    text_path: Mapped[str | None] = mapped_column(Text)
    # From the extraction sidecar: spans into text_path are valid only while this matches.
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    extractor_version: Mapped[str | None] = mapped_column(String(16))
    # Set when doc_type is amending / ordinance / gazette copy of an amending act.
    amending_act_number: Mapped[int | None] = mapped_column(Integer)
    amending_year: Mapped[int | None] = mapped_column(Integer)
    # What the document says about itself (e.g. a Gazette masthead), as read by the extractor.
    meta: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))

    act: Mapped[Act | None] = relationship(back_populates="documents")


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Clause(Base):
    """One node of an act's clause tree, in one version of the text.

    `path` is the stable ID every stage uses (`s40A.ss3.cl(b).sc(ii).p1`, `s12.expl1`,
    `sch2.art1.para3`); sections and schedules are top-level paths whatever Part or Chapter
    holds them (`parent_path` records that). `text` is the node's own text, without its
    children's; [span_start, span_end) covers the whole node, children included, in the file
    `text_path` (relative to DATA_DIR), valid only while that file hashes to `text_sha256`.
    """

    __tablename__ = "clauses"
    __table_args__ = (
        UniqueConstraint("act_id", "version_label", "path"),
        Index("ix_clauses_act_id_version_label", "act_id", "version_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("acts.id"))
    version_label: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(255))
    parent_path: Mapped[str | None] = mapped_column(String(255))
    node_type: Mapped[NodeType] = mapped_column(_enum(NodeType, "node_type"))
    number: Mapped[str | None] = mapped_column(String(16))
    heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    footnote_refs: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list)
    source_doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    ord: Mapped[int] = mapped_column(Integer)  # document order within the version
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    text_path: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    span_start: Mapped[int] = mapped_column(Integer)
    span_end: Mapped[int] = mapped_column(Integer)
    # Parse decisions worth a second look (e.g. how a proviso was attached), margin citations.
    meta: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
