"""Run the amendment-op extractor over an act's amending instruments; write `amendment_ops`.

Instruments are the act's `amending` and `ordinance` documents, one copy per sha256
(e-Gazette > sansad.in > PRS, as for clauses), in order of year and number. The base is a
clause-tree version from `clauses`; each Act's ops are carried into the next instrument's
known paths, an Ordinance's are not (see `amending_ops`). Every instrument's rows are
replaced in one transaction.
"""

import hashlib
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from opensansad.config import get_settings
from opensansad.db.models import AmendmentOp, Clause, Document, OpStatus, OpType
from opensansad.parse.amending_ops import Extraction, PathState, extract, report

SOURCE_PREFERENCE = {"egazette": 0, "sansad": 1, "prs": 2}


def instruments(session: Session, act_id: int) -> list[Document]:
    docs = session.scalars(select(Document).where(
        Document.act_id == act_id, Document.doc_type.in_(["amending", "ordinance"]))).all()
    best: dict[str, Document] = {}
    for d in docs:
        if d.sha256 not in best or SOURCE_PREFERENCE.get(d.source, 9) < SOURCE_PREFERENCE.get(
                best[d.sha256].source, 9):
            best[d.sha256] = d
    return sorted(best.values(), key=lambda d: (d.amending_year or 0,
                                                d.amending_act_number or 0))


def base_state(session: Session, act_id: int, version: str) -> PathState:
    rows = session.scalars(select(Clause).where(
        Clause.act_id == act_id, Clause.version_label == version).order_by(Clause.ord)).all()
    if not rows:
        raise SystemExit(f"no clauses for act {act_id} version {version}")
    return PathState([(r.path, r.text) for r in rows], version)


def rows(doc: Document, act_id: int, ext: Extraction) -> list[AmendmentOp]:
    return [AmendmentOp(
        act_id=act_id, amending_doc_id=doc.id, ord=i, provision=op.provision,
        op_type=OpType(op.op_type), unit=op.unit, target_path=op.target_path,
        anchor_path=op.anchor_path, old_text=op.old_text, new_text=op.new_text,
        text_path=doc.text_path, text_sha256=doc.text_sha256, span_start=op.span[0],
        span_end=op.span[1], status=OpStatus(op.status),
        confidence=None if op.reason else 1.0, reason=op.reason,
        meta={**op.meta, **({"new_paths": op.new_paths} if op.new_paths else {})} or None)
        for i, op in enumerate(ext.ops)]


def run(act_id: int, version: str, *, write: bool, verbose: bool = False,
        session: Session | None = None) -> int:
    from opensansad.db.session import get_sessionmaker

    own = session is None
    session = session or get_sessionmaker()()
    data_dir = get_settings().data_dir
    totals: Counter[str] = Counter()
    try:
        state = base_state(session, act_id, version)
        for doc in instruments(session, act_id):
            raw = (data_dir / doc.text_path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != doc.text_sha256:
                print(f"document {doc.id}: text changed since extraction; run persist first")
                return 1
            kind = "Act" if doc.doc_type == "amending" else "Ordinance"
            name = f"doc {doc.id} {kind} {doc.amending_act_number}/{doc.amending_year}"
            ext = extract(raw.decode(), state, name, carry=doc.doc_type == "amending")
            print(report(name, ext))
            if verbose:
                for op in ext.ops:
                    print(f"    {op.status[:3]} {op.provision} {op.op_type}/{op.unit} "
                          f"{op.target_path} anchor={op.anchor_path} "
                          f"new={(op.new_text or '')[:50]!r} old={(op.old_text or '')[:40]!r}")
            for op in ext.ops:
                totals[f"{kind}:{op.status}"] += 1
            if write:
                session.execute(delete(AmendmentOp).where(AmendmentOp.amending_doc_id == doc.id))
                session.add_all(rows(doc, act_id, ext))
        for kind in ("Act", "Ordinance"):
            r, u = totals[f"{kind}:resolved"], totals[f"{kind}:unresolved"]
            if r + u:
                print(f"{kind}s: {r} resolved / {u} unresolved ({r / (r + u):.0%} resolved)")
        if write:
            session.commit()
            print("written")
        return 0
    except BaseException:
        session.rollback()
        raise
    finally:
        if own:
            session.close()
