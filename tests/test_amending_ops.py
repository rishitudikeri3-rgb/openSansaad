from pathlib import Path

import pytest

from opensansad.extract.pdf import extract_pdf
from opensansad.parse import amending_ops as ao
from opensansad.parse.clause_tree import parse

FIX = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parents[1]

BASE = """BE it enacted by Parliament as follows:—
1. (1) This Act may be called the Test Act.
(2) It extends to India.
2. (1) In this Act,—
(a) "arbitration" means any arbitration;
(b) "award" means an award;
(c) "Court" means the Court;
(d) "party" means a party;
(e) "place" means a place;
(f) "record" means a record;
(g) "rule" means a rule;
(h) "seat" means a seat.
(2) This Act shall apply:
Provided that one.
3. (1) An appeal shall lie from orders.
(2) The tribunal shall—
(a) first thing;
(b) second thing.
4. A party may object.
"""

AMENDING = """An Act to amend the Test Act.
BE it enacted by Parliament as follows:—
[[margin: Short title.]]
1. This Act may be called the Test (Amendment) Act, 2020.
[[margin: Amendment of section 2.]]
2. In the Test Act (hereinafter referred to as the principal Act), in section 2,—
(i) in sub-section (1),—
(A) after clause (c), the following clause shall be inserted, namely:—
‘(ca) “council” means the Council;’;
(B) after clause (h), the following clauses shall be inserted, namely:—
‘(i) “prescribed” means prescribed by rules;
(j) “rules” means rules.’;
(ii) in sub-section (2), in the proviso, for the word “one”, the word “two” shall be
substituted.
[[margin: Amendment of section 3.]]
3. In section 3 of the principal Act,—
(a) in sub-section (1), for the words “An appeal”, the words “Notwithstanding anything,
an appeal” shall be substituted;
(b) in sub-section (2), for clauses (a) and (b), the following clauses shall be
substituted, namely:—
“(a) new first;
(b) first thing;
(c) second thing.”;
(c) after sub-section (2), the following sub-section shall be inserted, namely:—
“(3) A new rule:
Provided that it applies.
[[margin: Amendment of section 4.]]
4. In section 4 of the principal Act, for the words “not there”, the words “here” shall
be substituted.
[[margin: Insertion of new section 4A.]]
5. After section 4 of the principal Act, the following section shall be inserted,
namely:—
“4A. New section.”.
[[margin: Amendment to Act 1 of 2019.]]
6. Section 9 of the Test (Amendment) Act, 2019 shall be omitted.
7. Section 4 of the principal Act shall be renumbered as sub-section (1) thereof, and after
sub-section (1) as so renumbered, the following sub-section shall be inserted, namely:—
“(2) Second.”.
—
"""


def state(text: str = BASE) -> ao.PathState:
    tree = parse(text)
    assert tree.warnings == []
    return ao.PathState([(n.path, n.text(tree.source)) for n in tree.nodes], "base")


@pytest.fixture(scope="module")
def ops() -> dict[tuple[str, str | None], ao.Op]:
    ext = ao.extract(AMENDING, state(), "test")
    return {(op.provision, op.target_path): op for op in ext.ops}


def test_quoted_items_are_not_instruction_items() -> None:
    s2 = ao.read(AMENDING).sections[1]
    assert [c.path for c in s2.children] == ["s2.cl(i)", "s2.cl(ii)"]
    assert [c.path for c in s2.children[0].children] == ["s2.cl(i).cl(A)", "s2.cl(i).cl(B)"]


def test_inserted_clauses_take_their_printed_labels(ops) -> None:
    ca = ops[("s2.cl(i).cl(A)", "s2.ss1.cl(ca)")]
    assert ca.status == "resolved" and ca.anchor_path == "s2.ss1.cl(c)"
    assert ops[("s2.cl(i).cl(B)", "s2.ss1.cl(i)")].op_type == "insert"  # a clause after (h)
    assert ("s2.cl(i).cl(B)", "s2.ss1.cl(j)") in ops


def test_words_are_checked_against_the_base_text(ops) -> None:
    proviso = ops[("s2.cl(ii)", "s2.ss2.p1")]
    assert (proviso.old_text, proviso.new_text, proviso.status) == ("one", "two", "resolved")
    assert proviso.meta["text_check"] == "found 1x in the base text"
    appeal = ops[("s3.cl(a)", "s3.ss1")]
    assert appeal.new_text == "Notwithstanding anything, an appeal"
    missing = ops[("s4", "s4")]
    assert missing.status == "unresolved" and "not in the base text" in missing.reason


def test_unequal_substitution_is_left_unresolved(ops) -> None:
    subs = [op for (prov, _), op in ops.items() if prov == "s3.cl(b)"]
    assert sorted(op.target_path for op in subs) == [
        "s3.ss2.cl(a)", "s3.ss2.cl(b)", "s3.ss2.cl(c)"]
    assert all(op.status == "unresolved" for op in subs)
    assert "pairing by label" in ops[("s3.cl(b)", "s3.ss2.cl(a)")].reason


def test_unclosed_quote_ends_at_the_next_provision(ops) -> None:
    ss3 = ops[("s3.cl(c)", "s3.ss3")]
    assert ss3.status == "resolved" and ss3.meta["quote_unclosed"]
    assert ss3.new_paths == ["s3.ss3", "s3.ss3.p1"]
    assert "Amendment of section 4" not in ss3.new_text


def test_other_instruments_and_non_amending_provisions() -> None:
    ext = ao.extract(AMENDING, state(), "test")
    other = next(op for op in ext.ops if op.provision == "s6")
    assert other.status == "unresolved" and "another instrument" in other.reason
    assert [p.split(" ")[0] for p in ext.non_amending] == ["s1"]


def test_renumber_then_insert_under_the_new_number(ops) -> None:
    renumber = ops[("s7", "s4")]
    assert (renumber.op_type, renumber.new_text) == ("renumber", "s4.ss1")
    ss2 = ops[("s7", "s4.ss2")]
    assert ss2.status == "resolved" and ss2.anchor_path == "s4.ss1"


def test_known_paths_carry_only_when_asked() -> None:
    st = state()
    ao.extract(AMENDING, st, "ordinance", carry=False)
    assert not st.has("s4A") and st.has("s2.ss1.cl(c)")
    ao.extract(AMENDING, st, "act")
    assert st.has("s4A") and st.has("s2.ss1.cl(ca)") and st.has("s4.ss1")
    assert st.order.index("s2.ss1.cl(ca)") == st.order.index("s2.ss1.cl(c)") + 1


def test_ambiguous_proviso_is_unresolved() -> None:
    two = BASE.replace("Provided that one.", "Provided that one.\nProvided further that two.")
    ext = ao.extract(AMENDING, state(two), "test")
    op = next(op for op in ext.ops if op.provision == "s2.cl(ii)")
    assert op.status == "unresolved" and "'the proviso' of s2.ss2: 2 known" in op.reason


def test_margin_note_naming_another_section_is_flagged() -> None:
    text = AMENDING.replace("[[margin: Amendment of section 4.]]",
                            "[[margin: Amendment of section 5.]]")
    ext = ao.extract(text.replace("“not there”", "“A party”"), state(), "test")
    op = next(op for op in ext.ops if op.provision == "s4")
    assert "margin note names ['s5']" in op.reason


def test_2019_act_against_the_1996_text_alone() -> None:
    """Real fixture (pp. 1–3 of Act 33 of 2019) against the base transcription, without the
    2015 Act: what 2015 inserted or rewrote must come out unresolved, never guessed."""
    text = extract_pdf(FIX / "gazette_arbitration_act_2019_p1-3.pdf", ocr=None).joined("en")[0]
    base = parse((REPO / "data/transcriptions/bill-xxx-c-1995.txt").read_text())
    st = ao.PathState([(n.path, n.text(base.source)) for n in base.nodes], "1996-enacted")
    ext = ao.extract(text, st, "33/2019", carry=False)
    by = {(op.provision, op.target_path): op for op in ext.ops}
    for key in (("s2.cl(i).cl(A)", "s2.ss1.cl(ca)"), ("s2.cl(i).cl(B)", "s2.ss1.cl(i)"),
                ("s2.cl(i).cl(B)", "s2.ss1.cl(j)"), ("s3.cl(i)", "s11.ss3A"),
                ("s3.cl(ii)", "s11.ss4"), ("s5", "s23.ss4")):
        assert by[key].status == "resolved", key
    assert by[("s3.cl(ii)", "s11.ss4")].unit == "portion"
    assert "not in the known paths" in by[("s3.cl(v)", "s11.ss6A")].reason  # 2015 inserted it
    assert "'the proviso' of s2.ss2: 0 known" in by[("s2.cl(ii)", None)].reason
    assert "not in the base text" in by[("s4", "s17.ss1")].reason  # 2015 substituted s.17


def test_ops_round_trip_in_sqlite() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from opensansad.db.base import Base
    from opensansad.db.models import Act, AmendmentOp, Document
    from opensansad.parse.load_ops import rows

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    ext = ao.extract(AMENDING, state(), "test")
    with Session(engine) as session:
        act = Act(short_title="Test Act", act_number=1, year=2000)
        doc = Document(act=act, doc_type="amending", source="other", url="u", sha256="c" * 64,
                       acquisition="fetched", format="pdf", raw_path="raw/x.pdf",
                       fetched_at=datetime(2026, 10, 28, tzinfo=UTC), text_path="text/x.en.txt",
                       text_sha256="d" * 64)
        session.add(doc)
        session.flush()
        session.add_all(rows(doc, act.id, ext))
        session.commit()
        stored = session.query(AmendmentOp).order_by(AmendmentOp.ord).all()
        assert len(stored) == len(ext.ops)
        resolved = [op for op in stored if op.status == "resolved"]
        assert all(op.confidence == 1.0 and op.reason is None for op in resolved)
        assert all(op.confidence is None and op.reason for op in stored if op not in resolved)
        ss3 = next(op for op in stored if op.target_path == "s3.ss3")
        assert ss3.meta["new_paths"] == ["s3.ss3", "s3.ss3.p1"]
