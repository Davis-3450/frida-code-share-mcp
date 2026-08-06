"""Offline index of every project listed on codeshare.frida.re/browse/.

The site's own search only matches titles, and every query costs a request.
Walking all ~45 browse pages once gives the full catalogue; it is stored as a
single JSON file so later searches run locally with zero network traffic.

``FRIDA_CS_INDEX_PATH`` overrides where that file lives.
"""

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

from httpx import HTTPStatusError

from app.client import DEFAULT_CACHE_DIR, _cache_dir, _client_
from app.models import ProjectSummary
from app.scraper import parse_browse, views_to_int

MAX_PAGES = 500  # hard stop; the site clamps out-of-range pages instead of 404ing
_WORD_RE = re.compile(r"[\w.+#-]+")


def index_path() -> Path:
    raw = os.environ.get("FRIDA_CS_INDEX_PATH", "").strip()
    if raw:
        return Path(raw)
    # Sits next to the page cache, but is written even when that cache is off:
    # the index is built on explicit request, not as a side effect.
    return (_cache_dir() or DEFAULT_CACHE_DIR) / "browse-index.json"


def build_index(max_pages: int = 0) -> dict:
    """Walk every browse page and write the index. Returns the index payload.

    Args:
        max_pages: Stop after this many pages (0 = all of them).

    Some browse pages answer 500 permanently (broken rows on the site). Those
    are skipped and listed in ``failed_pages`` rather than aborting the walk.
    """
    projects: dict[str, ProjectSummary] = {}
    failed: list[int] = []
    page = 1
    total_pages = 1
    seen_pages = 0

    while page <= total_pages and page <= MAX_PAGES:
        try:
            html = _client_.browse(page)
            served, total_pages, items = parse_browse(html)
        except (HTTPStatusError, ValueError):
            failed.append(page)
            page += 1
            continue
        # Out-of-range pages are clamped to the last one; stop instead of looping.
        if served != page and seen_pages:
            break
        for item in items:
            projects.setdefault(item.id, item)
        seen_pages += 1
        if max_pages and seen_pages >= max_pages:
            break
        page += 1

    payload = {
        "built_at": time.time(),
        "pages": seen_pages,
        "total_pages": total_pages,
        "failed_pages": failed,
        "projects": [asdict(p) for p in projects.values()],
    }
    _write(payload)
    return payload


def _write(payload: dict) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written index


def load_index() -> dict | None:
    try:
        return json.loads(index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summaries(payload: dict) -> list[ProjectSummary]:
    return [ProjectSummary(**row) for row in payload.get("projects", [])]


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def score(item: ProjectSummary, terms: list[str]) -> int:
    """Sum of per-term weights; 0 means at least one term is missing (AND match)."""
    name = item.name.lower()
    ident = item.id.lower()
    description = item.description.lower()
    total = 0
    for term in terms:
        weight = 0
        if term in name:
            weight = 8
        elif term in ident:
            weight = 5
        elif term in description:
            weight = 2
        if not weight:
            return 0
        total += weight
    return total


def search(payload: dict, query: str) -> list[tuple[int, ProjectSummary]]:
    """Rank the indexed projects against a query. Empty query returns everything."""
    items = summaries(payload)
    terms = _tokens(query)
    if not terms:
        return [(0, item) for item in items]
    scored = [(score(item, terms), item) for item in items]
    hits = [pair for pair in scored if pair[0] > 0]
    # Ties broken by popularity so the useful scripts float to the top.
    hits.sort(key=lambda pair: (pair[0], views_to_int(pair[1].views)), reverse=True)
    return hits
