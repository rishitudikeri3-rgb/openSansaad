"""OCR for page images via Tesseract (a local binary; no network)."""

import shutil

import pymupdf
import pytesseract
from PIL import Image

OCR_DPI = 300


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_page(page: pymupdf.Page, lang: str = "eng", dpi: int = OCR_DPI) -> tuple[str, float]:
    """Render a PDF page and OCR it. Returns (text, confidence 0..1).

    Confidence is Tesseract's per-word confidence averaged with word length as weight,
    so a few misread punctuation marks don't dominate.
    """
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)

    lines: dict[tuple[int, int, int], list[str]] = {}
    weighted, weight = 0.0, 0
    for word, conf, block, par, line in zip(
        data["text"], data["conf"], data["block_num"], data["par_num"], data["line_num"],
        strict=True,
    ):
        conf = float(conf)
        if conf < 0 or not word.strip():
            continue
        lines.setdefault((block, par, line), []).append(word)
        weighted += conf * len(word)
        weight += len(word)

    text, prev_par = [], None
    for (block, par, _), words in lines.items():
        if prev_par is not None and (block, par) != prev_par:
            text.append("")  # blank line between paragraphs
        text.append(" ".join(words))
        prev_par = (block, par)
    return "\n".join(text), (weighted / weight / 100) if weight else 0.0
