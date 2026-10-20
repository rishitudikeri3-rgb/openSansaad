"""Rule 1: only ingest/cache.py may import HTTP client libraries."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "opensansad"
ALLOWED = {SRC / "ingest" / "cache.py"}
HTTP_MODULES = {"httpx", "requests", "aiohttp", "urllib3", "urllib.request", "http.client"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return found


def test_only_cache_imports_http_clients() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED:
            continue
        hits = {m for m in _imports(path) if m in HTTP_MODULES or m.split(".")[0] in
                {"httpx", "requests", "aiohttp", "urllib3"}}
        if hits:
            offenders.append(f"{path.relative_to(SRC)}: {sorted(hits)}")
    assert not offenders, "network access outside cache.py:\n" + "\n".join(offenders)
