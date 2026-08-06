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
def offline(monkeypatch, search_html, user_html, project_html):
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
        if endpoint not in pages:
            raise client_module.NotFound(endpoint)
        return pages[endpoint]

    monkeypatch.setattr(client_module._client_, "query", fake_query)
    return calls
