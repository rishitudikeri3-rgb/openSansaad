from pathlib import Path

import pytest

from opensansad.parse import transcription as tr

TEXT = """[[page: 6]]
[[margin: Short title, extent and commencement.]]
1. (1) This Act may be called the Arbitration and Conciliation Act, 1996.
(2) It extends to the whole of India:
[[page: 7]]
(e) “Court” means the principal Civil Court of original jurisdiction in a district,
"""


def test_clean_level_whitens_show_through_and_keeps_ink() -> None:
    assert tr.clean_level(40) == 0 and tr.clean_level(tr.INK) == 0
    assert tr.clean_level(200) == 255 and tr.clean_level(tr.PAPER) == 255
    assert 0 < tr.clean_level(120) < 255


def test_line_numbers_dropped_but_not_text_numbers() -> None:
    assert tr.strip_line_number("5 arbitration if the same had been") == \
        "arbitration if the same had been"
    assert tr.strip_line_number("where at least one of the parties is— 10") == \
        "where at least one of the parties is—"
    assert tr.strip_line_number("sections 41 and 43") == "sections 41 and 43"
    assert tr.strip_line_number("41 and 43 shall apply") == "41 and 43 shall apply"  # not a 5n
    assert tr.strip_line_number("(3) It shall come") == "(3) It shall come"


def test_body_lines_skip_markers_and_margins() -> None:
    lines = tr.body_lines(TEXT)
    assert [(p, t[:6]) for p, _, t in lines] == [(6, "1. (1)"), (6, "(2) It"), (7, "(e) “C")]
    assert lines[0][1] == 3  # line number in the file


def test_check_flags_only_lines_no_witness_contains() -> None:
    witnesses = {
        "lib": "1. (7) This Act may be called the Arbitration and Conciliation Act, 996,\n"
               "(2) It extends to the whole of India:",
        "typed": "(e ) \"Court\" means the principal Civil Court of original jurisdiction "
                 "in a district, and includes",
    }
    flags = tr.check(TEXT, witnesses)
    assert [f.page for f in flags] == [6]  # s.1(1): every witness misreads it
    assert flags[0].closest["lib"][0] > 0.9


def test_normalize_ignores_quotes_case_and_spacing() -> None:
    assert tr.normalize("(e) “Court”  means") == tr.normalize('(E ) "court" means')
    assert tr.normalize("[[margin: x]] a—b") == tr.normalize("A-B")


def test_meta_loads(tmp_path: Path) -> None:
    (tmp_path / "x.toml").write_text(
        'doc_url = "u"\nraw_sha256 = "s"\npages = "6-85"\nmethod = "m"\nverified = "model"\n')
    assert tr.load_meta(tmp_path / "x.toml").name == "x"
    (tmp_path / "y.toml").write_text('doc_url = "u"\n')
    with pytest.raises(TypeError):
        tr.load_meta(tmp_path / "y.toml")


def test_accepted_lines_are_not_flagged(tmp_path: Path) -> None:
    line = "1. (1) This Act may be called the Arbitration and Conciliation Act, 1996."
    (tmp_path / "x.accepted.txt").write_text(f"# re-read on the image\n{line}\n")
    accepted = tr.load_accepted(tmp_path / "x.accepted.txt")
    flagged = [f.text for f in tr.check(TEXT, {"lib": "nothing alike"}, accepted=accepted)]
    assert line not in flagged and len(flagged) == 2
    assert tr.load_accepted(tmp_path / "missing.txt") == set()


CLAUSES = "".join(f"({c}) item\n" for c in "abcdefgh")
ACT = f"""PART I
CHAPTER I
1. (1) This Act may be called.
(2) It extends to India.
2. (1) In this Part—
{CLAUSES}(i) "representative" means.
3. (1) Where the matters referred to in clause (a) of sub-section
(2) of section 32 arise.
(2) The tribunal shall—
(a) first
(i) one
(ii) two
(b) second, under sub-sections (4) and
(5) of section 11.
THE FIRST SCHEDULE
ARTICLE I
1. This Convention.
2. The term.
ARTICLE II
1. Each.
"""


def test_numbering_accepts_a_consistent_text() -> None:
    assert tr.numbering(ACT) == []
    assert tr.numbering(ACT.replace("(2) The tribunal", "(2) (a) The tribunal")) == []


def test_numbering_reports_breaks_and_misread_numerals() -> None:
    assert tr.numbering(ACT.replace("(2) The tribunal", "(2) (b) The")) == \
        ["p0 L17: clause (b) after ()"]
    assert tr.numbering(ACT.replace("(d) item", "(f) item"))[0] == "p0 L9: clause (f) after (c)"
    assert tr.numbering(ACT.replace("3. (1) Where", "8. (1) Where")) == \
        ["p0 L15: section 8 after 2"]
    assert tr.numbering(ACT.replace("CHAPTER I\n", "CHAPTER l\n")) == \
        ["p0 L2: CHAPTER l after 0"]
    assert tr.numbering(ACT.replace("ARTICLE II", "ARTICLE XI")) == \
        ["p0 L27: ARTICLE XI after 1"]
    assert tr.numbering(ACT.replace("2. The term", "3. The term")) == \
        ["p0 L26: paragraph 3 after 1"]
