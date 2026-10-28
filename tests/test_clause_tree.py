from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from opensansad.db.base import Base
from opensansad.db.models import Act, Clause, Document
from opensansad.parse import clause_tree as ct
from opensansad.parse import load as ld
from opensansad.parse.transcription import Meta

ACT = """[[page: 1]]
BILL No. XX
THE TEST BILL
to consolidate the law relating to tests.
WHEREAS tests are needed;
BE it enacted by Parliament as follows:—
PRELIMINARY
[[margin: Short title.]]
1. (1) This Act may be called the Test Act.
(2) It extends to India:
Provided that it shall not extend to Mars.
PART I
TESTING
CHAPTER I
GENERAL
[[margin: Definitions.]]
2. (1) In this Part, unless the context otherwise requires,—
(a) "court" means a court;
(b) "court" means a court;
(c) "court" means a court;
(d) "court" means a court;
(e) "court" means a court;
(f) "court" means a court;
(g) "court" means a court;
(h) "party" means a party;
(i) "tribunal" means a tribunal.
[[margin: Scope.]]
(2) This Part shall apply where the place is in India.
[[margin: Waiver.]]
3. A party who knows that—
(a) any provision, or
(b) any requirement,
[[flush]]
has not been complied with shall be deemed to have waived.
[[page: 2]]
4. (1) The tribunal shall set aside the award if—
(a) the party proves that—
(i) he was under incapacity, or
(ii) the award deals with a dispute not contemplated:
Provided that, if the decisions can be separated, only that part may be set aside; or
(b) the Court finds that the award is against policy.
Explanation.—Without prejudice to the generality of sub-clause (ii), it is declared.
(2) The application under sub-section
(1) of section 3 shall be made in time.
[[margin: 5 of 1908.]]
5. (1) The Code of Civil Procedure applies.
(2) A copy shall be filed.
Explanation.—In this section "Court" means the High Court.
THE FIRST SCHEDULE
(See section 5)
CONVENTION ON TESTS
ARTICLE I
1. This Convention shall apply.
2. The term shall include—
(a) awards;
ARTICLE II
1. Each State shall recognize.
THE SECOND SCHEDULE
(See section 5)
PROTOCOL ON TESTS
1. Each State recognises.
"""


@pytest.fixture(scope="module")
def tree() -> ct.Tree:
    return ct.parse(ACT)


def text(tree: ct.Tree, path: str) -> str:
    return tree.by_path()[path].text(tree.source)


def test_paths_and_containers(tree: ct.Tree) -> None:
    assert tree.warnings == []
    nodes = tree.by_path()
    assert [n.path for n in tree.nodes[:3]] == ["long_title", "preamble", "enacting_formula"]
    assert nodes["s1"].parent_path is None and nodes["s1"].meta["group_heading"] == "PRELIMINARY"
    assert nodes["pt1"].heading == "TESTING" and nodes["pt1.ch1"].heading == "GENERAL"
    assert nodes["s2"].parent_path == "pt1.ch1" and nodes["s2"].heading == "Definitions."
    assert nodes["s2.ss2"].heading == "Scope."
    assert text(tree, "long_title") == "to consolidate the law relating to tests."


def test_roman_after_letter_is_a_clause(tree: ct.Tree) -> None:
    assert "s2.ss1.cl(i)" in tree.by_path()
    assert "s4.ss1.cl(a).sc(i)" in tree.by_path()


def test_provisos_and_explanations(tree: ct.Tree) -> None:
    nodes = tree.by_path()
    assert nodes["s1.ss2.p1"].meta == {}
    proviso = nodes["s4.ss1.cl(a).sc(ii).p1"]
    assert proviso.meta["attached"] == "deepest open provision"
    assert "s4.ss1.cl(b)" in nodes  # the clause list continues after the proviso
    assert text(tree, "s4.ss1.expl1").startswith("Without prejudice")  # label stripped
    assert nodes["s5.expl1"].meta["attached"] == "says 'In this section'"


def test_flush_text_returns_to_list_opener(tree: ct.Tree) -> None:
    assert text(tree, "s3") == "A party who knows that—\nhas not been complied with shall " \
        "be deemed to have waived."
    assert text(tree, "s3.cl(b)") == "any requirement,"


def test_cross_reference_continuation_is_not_a_subsection(tree: ct.Tree) -> None:
    assert "s4.ss1" in tree.by_path() and text(tree, "s4.ss2").endswith("made in time.")
    assert [n.path for n in tree.nodes if n.node_type == "subsection"
            and n.parent_path == "s4"] == ["s4.ss1", "s4.ss2"]


def test_margin_citations_and_spans(tree: ct.Tree) -> None:
    s5 = tree.by_path()["s5"]
    assert tree.by_path()["s5.ss1"].meta["margin_citations"] == ["5 of 1908"]
    assert ACT[s5.span[0]:s5.span[1]].startswith("5. (1)")
    assert ACT[s5.span[0]:s5.span[1]].endswith("the High Court.")
    assert tree.by_path()["s4"].pages == [2] and tree.by_path()["s1"].pages == [1]


def test_schedules(tree: ct.Tree) -> None:
    nodes = tree.by_path()
    assert nodes["sch1"].heading == "CONVENTION ON TESTS" and nodes["sch1"].meta["see"] == \
        "section 5"
    assert "sch1.art1.para2.cl(a)" in nodes and "sch1.art2.para1" in nodes
    assert "sch2.para1" in nodes and nodes["sch2.para1"].parent_path == "sch2"


def test_out_of_sequence_item_is_warned() -> None:
    tree = ct.parse(ACT.replace("(b) any requirement,", "(d) any requirement,"))
    assert any("clause (d) does not continue" in w for w in tree.warnings)


def test_base_text_parses_cleanly() -> None:
    path = Path(__file__).parents[1] / "data/transcriptions/bill-xxx-c-1995.txt"
    tree = ct.parse(path.read_text())
    assert tree.warnings == []
    sections = [n.number for n in tree.nodes if n.node_type == "section"]
    assert sections == [str(i) for i in range(1, 87)]
    assert sum(n.node_type == "proviso" for n in tree.nodes) == 12
    assert sum(n.node_type == "explanation" for n in tree.nodes) == 8


def test_load_into_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    path = tmp_path / "transcriptions" / "test.txt"
    path.parent.mkdir()
    path.write_text(ACT)
    monkeypatch.setattr(ld, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    meta = Meta(name="test", doc_url="u", raw_sha256="b" * 64, pages="1-2", method="m",
                verified="model", version_label="test-enacted")
    with Session(engine) as session:
        act = Act(short_title="Test Act", act_number=1, year=2000)
        session.add(Document(act=act, doc_type="principal", source="other", url="u",
                             sha256="b" * 64, acquisition="fetched", format="pdf",
                             fetched_at=datetime(2026, 10, 28, tzinfo=UTC), raw_path="raw/x.pdf"))
        session.commit()
        tree = ct.parse(ACT)
        first = ld.load(tree, path, meta, session=session, graph=False)
        again = ld.load(tree, path, meta, session=session, graph=False)
        assert first.rows == again.rows == again.replaced == len(tree.nodes)
        row = session.query(Clause).filter_by(path="s3").one()
        assert row.text_path == "transcriptions/test.txt" and row.heading == "Waiver."
        assert ACT[row.span_start:row.span_end].startswith("3. A party")
        with pytest.raises(ld.LoadRefused, match="sha256"):
            ld.load(tree, path, Meta(**{**vars(meta), "raw_sha256": "c" * 64}),
                    session=session, graph=False)
        bad = ct.parse(ACT.replace("(b) any requirement,", "(d) any requirement,"))
        with pytest.raises(ld.LoadRefused, match="warnings"):
            ld.load(bad, path, meta, session=session, graph=False)


def test_explanation_naming_a_sub_clause_belongs_to_its_clause() -> None:
    # printed like s.34(2): the Explanation follows clause (b)'s sub-clauses and explains (ii)
    tree = ct.parse("BE it enacted by Parliament as follows:—\n"
                    "1. (1) An award may be set aside only if—\n"
                    "(a) a party proves incapacity; or\n"
                    "(b) the Court finds that—\n"
                    "(i) the dispute is not arbitrable, or\n"
                    "(ii) the award conflicts with policy.\n"
                    "Explanation.—Without prejudice to the generality of sub-clause (ii), "
                    "it is declared.\n"
                    "(2) An application shall be made in time.\n")
    assert tree.warnings == []
    expl = tree.by_path()["s1.ss1.cl(b).expl1"]
    assert expl.meta["attached"] == "names sub-clause (ii)"
    assert "s1.ss2" in tree.by_path()


def test_fragment_numbers_after_existing_units() -> None:
    source = "“Provided further that it applies.\nExplanation.—Here.”."
    lines = [(0, "Provided further that it applies."), (35, "Explanation.—Here.")]
    tree = ct.parse_fragment(source, lines, parent="s36.ss3", parent_type="subsection",
                             siblings={"proviso": 1, "explanation": 0})
    assert [n.path for n in tree.nodes] == ["s36.ss3.p2", "s36.ss3.expl1"]
    after = ct.parse_fragment("(i) one", [(0, "(i) one")], parent="s2.ss1",
                              parent_type="subsection", after=("clause", "h"))
    assert [n.path for n in after.nodes] == ["s2.ss1.cl(i)"]


def test_fragment_items_under_a_clause_are_siblings() -> None:
    # 2015 s.18: Explanation 1 quoted under s.34(2)(b) lists (i)–(iii); the fragment root has
    # no number and must not count as an open clause before "(ii)"
    lines = ["Explanation 1.—It is clarified that—", "(i) one; or", "(ii) two; or", "(iii) three."]
    source, offsets, at = "", [], 0
    for line in lines:
        offsets.append((at, line))
        source += line + "\n"
        at += len(line) + 1
    tree = ct.parse_fragment(source, offsets, parent="s34.ss2.cl(b)", parent_type="clause")
    assert [n.path for n in tree.nodes] == [
        "s34.ss2.cl(b).expl1", "s34.ss2.cl(b).expl1.sc(i)", "s34.ss2.cl(b).expl1.sc(ii)",
        "s34.ss2.cl(b).expl1.sc(iii)"]


def test_printed_spacing_quirks() -> None:
    # 2015 Act: "(f )" in s.31A(4), "11.The" in the Fifth Schedule
    tree = ct.parse(ACT.replace("(f) \"court\"", "(f ) \"court\"").replace(
        "2. Each State shall recognize.", "2.Each State shall recognize.") +
        "THE THIRD SCHEDULE\n1. One.\n2.Two.\n")
    assert "s2.ss1.cl(f)" in tree.by_path() and "sch3.para2" in tree.by_path()
