"""Load a parsed clause tree into Postgres (`clauses`) and Neo4j, replacing that version.

The act and `source_doc_id` come from the documents row whose bytes the transcription is of
(its `raw_sha256`), never from the file name. Postgres rows and the graph are replaced
together: the graph is written before the Postgres transaction commits, so a graph failure
leaves Postgres as it was (re-running is idempotent).
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opensansad.config import get_settings
from opensansad.db.models import Clause, Document, NodeType
from opensansad.parse.clause_tree import Tree
from opensansad.parse.transcription import Meta


class LoadRefused(ValueError):
    pass


@dataclass
class LoadResult:
    act_id: int
    version_label: str
    source_doc_id: int
    rows: int
    replaced: int
    edges: int | None  # None: graph not written

    def __str__(self) -> str:
        graph = "graph not written" if self.edges is None else f"{self.edges} CHILD_OF edges"
        return (f"act {self.act_id} version {self.version_label}: {self.rows} clauses from "
                f"document {self.source_doc_id} ({self.replaced} replaced), {graph}")


def source_document(session: Session, raw_sha256: str) -> Document:
    docs = session.scalars(select(Document).where(Document.sha256 == raw_sha256)).all()
    if not docs:
        raise LoadRefused(f"no documents row has sha256 {raw_sha256[:12]}… (run persist first)")
    with_act = {d.act_id for d in docs}
    if len(with_act) != 1 or None in with_act:
        raise LoadRefused(f"documents with sha256 {raw_sha256[:12]}… give act {with_act}")
    return min(docs, key=lambda d: d.id)


def clause_rows(tree: Tree, *, act_id: int, version_label: str, source_doc_id: int,
                text_path: str, text_sha256: str) -> list[Clause]:
    rows = []
    for i, n in enumerate(tree.nodes):
        if n.span[0] < 0:
            raise LoadRefused(f"{n.path}: no span")
        rows.append(Clause(
            act_id=act_id, version_label=version_label, path=n.path,
            parent_path=n.parent_path, node_type=NodeType(n.node_type), number=n.number,
            heading=n.heading, text=n.text(tree.source), footnote_refs=[],
            source_doc_id=source_doc_id, ord=i,
            page_start=min(n.pages) if n.pages else None,
            page_end=max(n.pages) if n.pages else None,
            text_path=text_path, text_sha256=text_sha256,
            span_start=n.span[0], span_end=n.span[1], meta=n.meta or None))
    return rows


def load(tree: Tree, transcription: Path, meta: Meta, *, session: Session | None = None,
         version_label: str | None = None, graph: bool = True,
         force: bool = False) -> LoadResult:
    version = version_label or meta.version_label
    if not version:
        raise LoadRefused("no version_label (give --version-label or set it in the .toml)")
    if tree.warnings:
        raise LoadRefused(f"{len(tree.warnings)} parse warnings; fix the text first")
    raw = transcription.read_bytes()
    if raw.decode() != tree.source:
        raise LoadRefused("parsed text differs from the file on disk (CRLF?)")
    data_dir = get_settings().data_dir.resolve()
    try:
        text_path = transcription.resolve().relative_to(data_dir).as_posix()
    except ValueError:
        raise LoadRefused(f"{transcription} is not under DATA_DIR {data_dir}") from None

    from opensansad.db.session import get_sessionmaker

    own_session = session is None
    session = session or get_sessionmaker()()
    try:
        doc = source_document(session, meta.raw_sha256)
        assert doc.act_id is not None
        rows = clause_rows(tree, act_id=doc.act_id, version_label=version,
                           source_doc_id=doc.id, text_path=text_path,
                           text_sha256=hashlib.sha256(raw).hexdigest())
        replaced = session.execute(delete(Clause).where(
            Clause.act_id == doc.act_id, Clause.version_label == version)).rowcount
        session.add_all(rows)
        session.flush()
        edges = None
        if graph:
            from opensansad.graph.clauses import GraphNode, replace_tree
            from opensansad.graph.client import GraphClient

            nodes = [GraphNode(r.path, r.parent_path, r.node_type.value, r.number, r.heading,
                               r.ord) for r in rows]
            with GraphClient.from_settings() as client:
                edges = replace_tree(client, doc.act_id, version, nodes, force=force)
        session.commit()
        return LoadResult(doc.act_id, version, doc.id, len(rows), replaced, edges)
    except BaseException:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()
