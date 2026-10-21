from dataclasses import dataclass, field
from typing import Literal

EXTRACTOR_VERSION = "2"

Method = Literal["native", "ocr", "html"]


@dataclass
class Page:
    number: int  # 1-based
    method: Method
    en: str
    hi: str = ""
    ocr_confidence: float | None = None  # 0..1, OCR pages only
    wordlike_ratio: float | None = None  # native text-layer quality, PDF pages only
    legacy_lines_dropped: int = 0  # legacy-font Hindi lines removed from the English text


@dataclass
class Extraction:
    pages: list[Page]
    stats: dict = field(default_factory=dict)

    @property
    def is_scanned(self) -> bool:
        return sum(p.method == "ocr" for p in self.pages) > len(self.pages) / 2

    @property
    def ocr_confidence(self) -> float | None:
        confs = [p.ocr_confidence for p in self.pages if p.ocr_confidence is not None]
        return sum(confs) / len(confs) if confs else None

    def joined(self, lang: Literal["en", "hi"]) -> tuple[str, list[tuple[int, int]]]:
        """Pages joined by a blank line, plus each page's (start, end) offsets in the result."""
        parts, offsets, pos = [], [], 0
        for i, page in enumerate(self.pages):
            if i:
                parts.append("\n\n")
                pos += 2
            text = getattr(page, lang)
            offsets.append((pos, pos + len(text)))
            parts.append(text)
            pos += len(text)
        return "".join(parts), offsets
