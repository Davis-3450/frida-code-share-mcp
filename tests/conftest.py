import os
from pathlib import Path

import pytest

# Every test runs offline against the fixtures; make sure no cache layer leaks
# between tests or writes into the developer's temp dir.
os.environ.setdefault("FRIDA_CS_CACHE_TTL", "0")
os.environ.setdefault("FRIDA_CS_CACHE_DIR", "")

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def search_html() -> str:
    """search/?query=root — 144 results."""
    return _read("query.html")


@pytest.fixture
def user_html() -> str:
    """@akabe1/ — 2 projects."""
    return _read("user.html")


@pytest.fixture
def project_html() -> str:
    """@dzonerzy/fridantiroot/ — 14700 chars of source."""
    return _read("project.html")


@pytest.fixture
def browse_html() -> str:
    """browse/?page=1 — 45 pages of catalogue."""
    return _read("browse.html")


@pytest.fixture
def browse_last_html() -> str:
    """browse/?page=45 — the last page; the site clamps anything past it here."""
    return _read("browse_last.html")


@pytest.fixture
def index_dir(monkeypatch, tmp_path) -> Path:
    """Keep the offline index out of the developer's temp dir."""
    monkeypatch.setenv("FRIDA_CS_INDEX_PATH", str(tmp_path / "browse-index.json"))
    return tmp_path


@pytest.fixture
def offline(
    monkeypatch, search_html, user_html, project_html, browse_html, browse_last_html
):
    """Serve the fixtures instead of hitting codeshare.frida.re."""
    from app import client as client_module

    pages = {
        "search/": search_html,
        "@akabe1/": user_html,
        "@dzonerzy/fridantiroot/": project_html,
    }
    calls: list[tuple[str, dict | None]] = []

    def fake_query(endpoint, params=None):
        calls.append((endpoint, params))
        if endpoint == "browse/":
            page = int((params or {}).get("page", 1))
            # The real site clamps out-of-range pages to the last one.
            return browse_html if page == 1 else browse_last_html
        if endpoint not in pages:
            raise client_module.NotFound(endpoint)
        return pages[endpoint]

    monkeypatch.setattr(client_module._client_, "query", fake_query)
    return calls
