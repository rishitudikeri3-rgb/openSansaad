# Bill XXX-C of 1995: transcription notes

Points where the print itself is unusual or the reading is uncertain. Page = PDF page of
the elibrary item 79403 scan.

- p10, s.9: printed in a different typeface from the rest of the page (a correction slip
  pasted over the original print). Its clause markers are upright, not italic.
- p10, s.9(ii)(c): mark after "arbitration" is damaged (";" or ","); transcribed ",".
- p12, s.11(12): a correction slip in a different typeface, like s.9. Transcribed as printed,
  including "those sub-section" (singular) in (a), "(5) (6)" without a comma and "to, the
  Chief Justice" in (b), "Chief justice" (lower-case j) at the end, and curly quotes.
- p17: s.28(1)(b)(iii) reads "under clause (a)" as printed (not "sub-clause (i)"). In s.28(3) a stray pen stroke runs from the comma after "In all cases" into "the"; transcribed as ", the".
- p21: **s.36 is incomplete as printed, in both scans.** The section is a pasted slip in a different typeface (like s.9), set over the original lines: after "award under" the rest of that clause is missing, and the next line starts "award shall be endorced" (sic: "enforced"); "decree" is followed by a blank where a character was removed. Transcribed exactly as printed, with no conjectural text. The missing words ("section 34 has expired, or such application having been made, it has been refused, the" in the typed WIPO copy) are a question for the Phase 2 ground truth: they must come from a primary source (the 1996 Gazette, if one ever becomes available), not from the typed copy. s.37 (1)(a)–(2)(b) is also printed in the slip typeface.
- p23: s.43(4) "shall be exclued" (sic, for "excluded"), checked at 300 dpi; s.43(2) "the date referred in section 21" (no "to") as printed. s.44(b): the mark after "Official Gazette" is a full stop in the scan, transcribed as "," (the sentence continues "declare to be"): the only normalized punctuation on the page.
- p26: s.57(1) "that --" (typed double hyphen) transcribed as "that—", per the extractor rule that 2+ dash runs are an em dash.
- p29: s.65 Explanation: "conciliators.as the case may be" in the scan (the word "conciliators" is patched in a lighter face, followed by a dot touching "as"); transcribed "conciliators, as".
- p32: s.85(2) and s.86 are pasted slips in a different typeface (s.86 is new in the passed text: it repeals the Third Ordinance of 1996, which the Bill as introduced could not have named). The s.86 margin note reads "Repeal of Ordinance 27 of 1996 and saving."
- pp33–41, the First, Second and Third Schedules (New York Convention, Geneva Protocol 1923,
  Geneva Convention 1927): **method differs from pages 6–32**. The text was not typed by hand:
  a script took the cleaned-OCR draft of the second scan (item 159905, correct line order),
  swapped each out-of-vocabulary token for the aligned token of the first scan's draft when
  that one was a known word, and dropped page/line-number lines. Every line no witness
  contains, every out-of-vocabulary token and every odd mark was then corrected by hand
  against the page images (pp33, 37, 38, 39 read in full). A word-level diff against the typed
  WIPO copy found no dropped or added wording; its remaining differences are typos in the WIPO
  copy ("arbitrat", "reefed", "cessation", "failing with in") or its US spelling. One line the
  OCR lost was restored from the image ("tribunal.", end of p39). Punctuation on pp34–36 and
  pp40–41 is checked only where flagged. Printed specks read as marks were dropped: "its'"
  (p38, Second Schedule para 3) and "soon,as" (p38, para 6).
- pp33–41: the First Schedule's article references were misread by both OCRs; corrected from
  p37 (Article XV: VIII, IX, I, X and XI, XII, XIII; Article XVI(2): XIII).
