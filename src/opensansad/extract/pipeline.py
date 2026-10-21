"""Raw file -> text files under data/text/, mirroring the raw store layout.

data/raw/{source}/{url_hash}/{version}.pdf
  -> data/text/{source}/{url_hash}/{version}.en.txt   (always)
     data/text/{source}/{url_hash}/{version}.hi.txt   (only if there is Hindi text)
     data/text/{source}/{url_hash}/{version}.json     (page offsets, methods, confidence)

Text is derived data: it may be regenerated (e.g. after an extractor fix) at any time.
Raw files are only ever read. Anything that stores character offsets into a text file
(clause spans, from step 4) must also store that file's sha256 from the sidecar, and
check it before trusting the offsets: re-extraction can shift every position.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from opensansad.db.models import DocFormat
from opensansad.extract.document import EXTRACTOR_VERSION, Extraction
from opensansad.extract.html import extract_html
from opensansad.extract.pdf import OcrFn, default_ocr, extract_pdf


def extract_file(
    raw_path: Path,
    fmt: DocFormat,
    *,
    html_selector: str | None = None,
    ocr: OcrFn | None = default_ocr,
) -> Extraction:
    if fmt is DocFormat.PDF:
        return extract_pdf(raw_path, ocr=ocr)
    if fmt is DocFormat.HTML:
        return extract_html(raw_path.read_bytes(), html_selector)
    raise ValueError(f"no extractor for format {fmt.value!r} ({raw_path})")


def text_base(raw_path: Path, raw_root: Path, text_root: Path) -> Path:
    rel = Path(raw_path).resolve().relative_to(Path(raw_root).resolve())
    return Path(text_root) / rel.with_suffix("")


def _write_atomic(path: Path, text: str) -> str:
    """Write via temp file + rename so readers never see a half-written file. Returns sha256."""
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


def write_extraction(
    extraction: Extraction, raw_path: Path, raw_root: Path, text_root: Path
) -> Path:
    """Write text + sidecar JSON. Returns the English text path (documents.text_path)."""
    base = text_base(raw_path, raw_root, text_root)
    base.parent.mkdir(parents=True, exist_ok=True)

    en, en_offsets = extraction.joined("en")
    hi, hi_offsets = extraction.joined("hi")
    en_path = base.with_name(base.name + ".en.txt")
    en_sha = _write_atomic(en_path, en)
    hi_path = base.with_name(base.name + ".hi.txt")
    hi_sha = None
    if hi.strip():
        hi_sha = _write_atomic(hi_path, hi)
    else:
        hi_path.unlink(missing_ok=True)

    sidecar = {
        "extractor_version": EXTRACTOR_VERSION,
        "raw_path": str(Path(raw_path).resolve().relative_to(Path(raw_root).resolve())),
        "raw_sha256": hashlib.sha256(Path(raw_path).read_bytes()).hexdigest(),
        "en_sha256": en_sha,
        "hi_sha256": hi_sha,
        "is_scanned": extraction.is_scanned,
        "ocr_confidence": extraction.ocr_confidence,
        "pages": [
            {
                "page": p.number,
                "method": p.method,
                "en": list(en_offsets[i]),
                "hi": list(hi_offsets[i]),
                "ocr_confidence": p.ocr_confidence,
                "wordlike_ratio": p.wordlike_ratio,
                "legacy_lines_dropped": p.legacy_lines_dropped,
                "ocr_regions_dropped": p.ocr_regions_dropped,
            }
            for i, p in enumerate(extraction.pages)
        ],
    }
    # Sidecar last: if a crash leaves text newer than the sidecar, the sha mismatch shows it.
    _write_atomic(base.with_name(base.name + ".json"), json.dumps(sidecar, indent=2))
    return en_path
