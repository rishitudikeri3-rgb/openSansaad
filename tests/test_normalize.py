from opensansad.extract.normalize import (
    document_vocab,
    join_hyphenated_lines,
    looks_legacy_hindi,
    normalize_text,
    script_of,
    strip_running_lines,
    wordlike_ratio,
)
from opensansad.extract.pdf import margin_note

LEGAL = (
    "In section 11 of the principal Act, after sub-section (3), the following sub-section "
    "shall be inserted, namely the Supreme Court and the High Court shall have the power"
)
KRUTI_DEV = "bl Hkkx esa fHkUu i`\"B la[;k nh tkrh gS ftlls fd ;g vyx ladyu ds :i esa j[kk tk ldsA"


def test_superscript_digits_become_footnote_markers_not_digits() -> None:
    assert normalize_text("29A¹[Time limit]") == "29A[^1][Time limit]"
    assert normalize_text("section 12²³") == "section 12[^23]"


def test_ligatures_and_soft_hyphens() -> None:
    assert normalize_text("ﬁnal ﬂow") == "final flow"
    assert normalize_text("arbi­tral") == "arbitral"


def test_dash_runs_become_one_em_dash_but_ranges_keep_en_dash() -> None:
    assert normalize_text("namely: ––") == "namely:—"
    assert normalize_text("namely:—") == "namely:—"
    assert normalize_text("————") == "—"
    assert normalize_text("sections 43A–43M") == "sections 43A–43M"
    assert normalize_text("sub-section") == "sub-section"


def test_typographic_quotes_preserved() -> None:
    s = "‘(i) “prescribed” means prescribed by rules made under this Act;’"
    assert normalize_text(s) == s
    assert normalize_text("„low quote‟") == "“low quote“"


def test_hyphen_at_line_end_kept_when_joining() -> None:
    assert join_hyphenated_lines("in sub-\nsection (2)") == "in sub-section (2)"
    assert join_hyphenated_lines("clause (a)-\n(b)") == "clause (a)-\n(b)"  # not a word break
    assert normalize_text("the sub-\n  section") == "the sub-section"


def test_whitespace() -> None:
    assert normalize_text("a  b  \n\n\n\nc ") == "a b\n\nc"


def test_running_lines_alternating_gazette_headers() -> None:
    words = ["arbitral", "tribunal", "award", "seat", "venue", "appeal", "court", "council"]
    bodies = [[f"In section {n}, the word “{w}” shall be omitted;", f"({w}) is defined;",
               f"the {w} clause stands."] for n, w in enumerate(words, start=1)]
    pages = []
    for n, body in enumerate(bodies, start=1):
        header = (["SEC. 1]", "THE GAZETTE OF INDIA EXTRAORDINARY", str(n)] if n % 2
                  else [str(n), "THE GAZETTE OF INDIA EXTRAORDINARY", "[PART II—"])
        pages.append(header + body)
    assert strip_running_lines(pages) == bodies


def test_running_lines_short_gazette_heads_seen_once() -> None:
    pages = [["REGISTERED NO. DL—(N)04/0007/2003—19", "PART II — Section 1", "1. Short title."],
             ["2", "THE GAZETTE OF INDIA EXTRAORDINARY", "[PART II—", "2. In s. 11, for “may”,"],
             ["SEC. 1]", "THE GAZETTE OF INDIA EXTRAORDINARY", "3", "3. Section 12 is omitted."]]
    assert strip_running_lines(pages) == [
        ["REGISTERED NO. DL—(N)04/0007/2003—19", "PART II — Section 1", "1. Short title."],
        ["2. In s. 11, for “may”,"], ["3. Section 12 is omitted."]]


def test_running_lines_keep_unique_body_lines_in_zone() -> None:
    pages = [["1", "16. In the Fourth Schedule,"], ["2", "(ix) for sub-sections (11)"]]
    assert strip_running_lines(pages) == [["16. In the Fourth Schedule,"],
                                          ["(ix) for sub-sections (11)"]]


def test_script_of() -> None:
    assert script_of("मध्यस्थम् और सुलह अधिनियम") == "hi"
    assert script_of("Switch to Hindi (हिंदी)") == "en"
    assert script_of("26 of 1996.") == "en"


def test_wordlike_ratio_separates_english_from_legacy_font() -> None:
    assert wordlike_ratio(LEGAL) > 0.85
    assert wordlike_ratio(KRUTI_DEV * 2) < 0.6
    assert wordlike_ratio("too short") == 1.0


def test_legacy_line_detection_real_masthead_lines() -> None:
    for line in ["jftLVªh lañ Mhñ ,yñ—(,u)04@0007@2003—19", "vlk/kkj.k",
                 "Hkkx  II — [k.M 1", "izkf/kdkj ls izdkf'kr", "lañ 52]", KRUTI_DEV]:
        assert looks_legacy_hindi(line), line


def test_legacy_line_detection_no_false_positives() -> None:
    for line in ["Rs. 45,000", "Above Rs. 20,00,00,000", "[9th August, 2019.]", "(xxx) clause",
                 "Smt. Nirmala Sitharaman", "PART II — Section 1", "26 of 1996.",
                 "REGISTERED NO. DL—(N)04/0007/2003—19", "section 29A and 43-I", "MGIP", LEGAL]:
        assert not looks_legacy_hindi(line), line


def test_line_end_hyphen_resolved_by_document_vocab() -> None:
    vocab = document_vocab(["the subject-matter of the dispute", "any arbitration"])
    assert join_hyphenated_lines("the subject-\nmatter", vocab) == "the subject-matter"
    assert join_hyphenated_lines("the arbi-\ntration", vocab) == "the arbitration"


def test_line_end_hyphen_fallback_keeps_only_legal_prefixes() -> None:
    assert join_hyphenated_lines("in sub-\nclause (a)") == "in sub-clause (a)"
    assert join_hyphenated_lines("the non-\nobstante clause") == "the non-obstante clause"
    assert join_hyphenated_lines("such interfer-\nence") == "such interference"


def test_broken_word_does_not_confirm_itself() -> None:
    vocab = document_vocab(["the subject-\nmatter"])  # only ever seen broken
    assert "subject-matter" not in vocab


def test_wrapped_margin_note_word_break() -> None:
    note = margin_note(["Short title and commence-", "ment."])
    assert normalize_text(note) == "[[margin: Short title and commencement.]]"
    assert normalize_text(margin_note(["Amendment of sub-", "section (2)."])) == (
        "[[margin: Amendment of sub-section (2).]]"
    )
    assert "⁣" not in normalize_text(margin_note(["clause (a)-", "(b)."]))
