# Bill XXX-C of 1995: transcription notes

Page = PDF page of the elibrary item 79403 scan (the cited source; see the `.toml`).

## Rule

The text is transcribed **as printed**: wording, spelling, grammar, capitalization and printed
errors are kept, marked "(sic)" here and never corrected in the text. The only departures are
**editorial changes**, each listed in the last section:

1. Format rules shared with the extractor: printed page numbers, running heads and margin line
   numbers dropped; a run of 2+ dashes or hyphens written as `—`.
2. Specks: a mark that is not type (a dot of ink or dirt beside a word, judged by size and
   position, not by shape) is dropped.
3. Damaged punctuation: a printed mark changed to another only where the glyph is visibly
   damaged or misplaced and the sentence runs on through it. Two cases (s.44(b), s.65).

## The print

- p10, s.9: printed in a different typeface from the rest of the page (a correction slip
  pasted over the original print). Its clause markers are upright, not italic.
- p10, s.9(ii)(c): the mark after "arbitration" is damaged (";" or ","); read as ",".
- p12, s.11(12): a correction slip in a different typeface, like s.9. Transcribed as printed,
  including "those sub-section" (singular) in (a), "(5) (6)" without a comma and "to, the
  Chief Justice" in (b), "Chief justice" (lower-case j) at the end, and curly quotes.
- p17: s.28(1)(b)(iii) reads "under clause (a)" as printed (not "sub-clause (i)"). In s.28(3) a
  stray pen stroke runs from the comma after "In all cases" into "the"; read as ", the".
- p21: **s.36 is incomplete as printed, in both scans.** The section is a pasted slip in a
  different typeface (like s.9), set over the original lines: after "award under" the rest of
  that clause is missing, and the next line starts "award shall be endorced" (sic: "enforced");
  "decree" is followed by a blank where a character was removed. Transcribed exactly as
  printed, with no conjectural text. The missing words ("section 34 has expired, or such
  application having been made, it has been refused, the" in the typed WIPO copy) are a
  question for the Phase 2 ground truth: they must come from a primary source (the 1996
  Gazette, if one ever becomes available), not from the typed copy. s.37(1)(a)–(2)(b) is also
  printed in the slip typeface.
- p23: s.43(4) "shall be exclued" (sic, for "excluded"), checked at 300 dpi; s.43(2) "the date
  referred in section 21" (sic, no "to").
- p32: s.85(2) and s.86 are pasted slips in a different typeface (s.86 is new in the passed
  text: it repeals the Third Ordinance of 1996, which the Bill as introduced could not have
  named). The s.86 margin note reads "Repeal of Ordinance 27 of 1996 and saving."

## Schedules (pp33–41): method

The First, Second and Third Schedules (New York Convention, Geneva Protocol 1923, Geneva
Convention 1927) were **not typed by hand**: long verbatim output of the treaty text was cut off
by the model API's output filter. A script took the cleaned-OCR draft of the second scan (item
159905, correct line order), swapped each out-of-vocabulary token for the aligned token of the
first scan's draft when that one was a known word, and dropped page- and line-number lines.
Then:

- every page (33–41) was read in full against the images of item 79403, line by line;
- every line no witness contains, every out-of-vocabulary token, every stray capital and every
  odd mark was corrected on the image;
- a word-level diff against the typed WIPO copy found no dropped or added wording; its
  remaining differences are typos in the WIPO copy ("arbitrat", "reefed", "cessation",
  "failing with in", "under take") or its US spelling ("specialized", "favorable").

Misreads both OCRs shared, corrected from the images: the Article headings (II, III, IV, VII,
XI, XII, XIII) and every article reference in Article XV and XVI(2) (VIII, IX, I, X and XI, XII,
XIII); one line both lost ("tribunal.", end of p39).

## Editorial changes

| Page | Place | Printed | Transcribed | Kind |
|---|---|---|---|---|
| p23 | s.44(b), after "Official Gazette" | `.` | `,` | damaged punctuation (sentence continues "declare to be") |
| p26 | s.57(1) | `that --` | `that—` | format rule |
| p29 | s.65 Explanation | `conciliators.as` | `conciliators, as` | damaged punctuation ("conciliators" patched in a lighter face, dot touching "as") |
| p34 | First Sch., Art. IV(2) | `or' sworn` | `or sworn` | speck |
| p34 | First Sch., Art. VI | `suitable. security` | `suitable security` | speck |
| p38 | Second Sch., para 3 | `its' authorities` | `its authorities` | speck |
| p38 | Second Sch., para 6 | `soon,as` | `soon as` | speck |
| p41 | Third Sch., Art. 10 | `protectorates :; or` | `protectorates or` | speck |
| p41 | Third Sch., Art. 11 | `of.the` | `of the` | speck |
