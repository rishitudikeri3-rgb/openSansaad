"""Generic HTML -> block-structured text. Site-specific content selection lives in the
source adapters (step 3d), which pass a CSS selector for the main content."""

from bs4 import BeautifulSoup, NavigableString

from opensansad.extract.document import Extraction, Page
from opensansad.extract.normalize import (
    footnote_marker,
    is_marker_text,
    normalize_text,
    script_of,
)

_DROP = ["script", "style", "noscript", "nav", "header", "footer", "form", "iframe", "svg",
         "button", "select"]
_BLOCK = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "dt", "dd", "pre", "blockquote",
          "div", "section", "article", "br", "table", "ul", "ol"]


def extract_html(data: bytes, selector: str | None = None) -> Extraction:
    soup = BeautifulSoup(data, "lxml")
    for tag in soup(_DROP):
        tag.decompose()
    root = (soup.select_one(selector) if selector else None) or soup.body or soup

    # India Code marks amendment footnotes as <sup>1</sup>[...]; as plain text the "1" would
    # fuse with the section number ("29A1"). Other superscripts ("9<sup>th</sup>") stay text.
    for sup in root.find_all("sup"):
        text = sup.get_text(strip=True)
        if is_marker_text(text):
            sup.replace_with(NavigableString(footnote_marker(text)))
    for row in root.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        row.replace_with(NavigableString("\n" + " | ".join(c for c in cells if c) + "\n"))
    for tag in root.find_all(_BLOCK):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))

    lines = [ln for ln in root.get_text("").split("\n") if ln.strip()]
    en = [ln for ln in lines if script_of(ln) == "en"]
    hi = [ln for ln in lines if script_of(ln) == "hi"]
    return Extraction(pages=[
        Page(number=1, method="html", en=normalize_text("\n".join(en)),
             hi=normalize_text("\n".join(hi)))
    ])
