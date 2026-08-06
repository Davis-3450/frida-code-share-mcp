"""The offline browse index: building it, reloading it and querying it."""

import json

import pytest

from app import index as index_module
from app.models import ProjectSummary

pytestmark = pytest.mark.usefixtures("index_dir")


def synthetic(monkeypatch, pages: int, per_page: int = 3, last_page: int | None = None):
    """Serve `pages` fake browse pages; returns the log of requested pages."""
    seen: list[int] = []
    total = pages if last_page is None else last_page

    def fake_browse(page: int = 1):
        seen.append(page)
        served = min(page, pages)  # the real site clamps past the end
        items = [
            ProjectSummary(
                id=f"user{served}/project{n}",
                name=f"Project {served}-{n}",
                description=f"page {served} entry {n}",
                likes=n,
                views=f"{n}K",
            )
            for n in range(per_page)
        ]
        return served, total, items

    monkeypatch.setattr(index_module._client_, "browse", lambda page=1: page)
    monkeypatch.setattr(index_module, "parse_browse", lambda page: fake_browse(page))
    return seen


def test_build_walks_every_page(monkeypatch):
    seen = synthetic(monkeypatch, pages=4)
    payload = index_module.build_index()
    assert seen == [1, 2, 3, 4]
    assert payload["pages"] == 4
    assert len(payload["projects"]) == 12


def test_build_stops_when_the_site_clamps(monkeypatch):
    # 3 real pages but the widget claims 10: page 4 comes back as page 3.
    seen = synthetic(monkeypatch, pages=3, last_page=10)
    payload = index_module.build_index()
    assert seen == [1, 2, 3, 4]
    assert payload["pages"] == 3
    assert len(payload["projects"]) == 9


def test_build_skips_pages_that_error(monkeypatch):
    import httpx

    real = synthetic(monkeypatch, pages=4)
    parse = index_module.parse_browse

    def flaky(page):
        if page == 2:
            request = httpx.Request("GET", "https://codeshare.frida.re/browse/")
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(500, request=request)
            )
        return parse(page)

    monkeypatch.setattr(index_module, "parse_browse", flaky)
    payload = index_module.build_index()
    assert real == [1, 3, 4]  # page 2 never reached the parser
    assert payload["failed_pages"] == [2]
    assert payload["pages"] == 3
    assert len(payload["projects"]) == 9


def test_build_respects_max_pages(monkeypatch):
    synthetic(monkeypatch, pages=10)
    payload = index_module.build_index(max_pages=2)
    assert payload["pages"] == 2
    assert len(payload["projects"]) == 6


def test_build_deduplicates(monkeypatch):
    def fake_browse(page):
        item = ProjectSummary(id="same/one", name="One", description="dup")
        return page, 3, [item]

    monkeypatch.setattr(index_module._client_, "browse", lambda page=1: page)
    monkeypatch.setattr(index_module, "parse_browse", fake_browse)
    payload = index_module.build_index()
    assert len(payload["projects"]) == 1


def test_index_round_trips_to_disk(monkeypatch):
    synthetic(monkeypatch, pages=2)
    index_module.build_index()
    reloaded = index_module.load_index()
    assert reloaded is not None
    assert len(index_module.summaries(reloaded)) == 6


def test_index_path_follows_the_cache_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("FRIDA_CS_INDEX_PATH", raising=False)
    monkeypatch.setenv("FRIDA_CS_CACHE_DIR", str(tmp_path))
    assert index_module.index_path() == tmp_path / "browse-index.json"


def test_index_path_falls_back_when_the_page_cache_is_off(monkeypatch):
    monkeypatch.delenv("FRIDA_CS_INDEX_PATH", raising=False)
    monkeypatch.setenv("FRIDA_CS_CACHE_DIR", "")
    assert index_module.index_path().name == "browse-index.json"


def test_load_index_missing_returns_none():
    assert index_module.load_index() is None


def test_load_index_corrupt_returns_none():
    path = index_module.index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert index_module.load_index() is None


def _payload(*items: ProjectSummary) -> dict:
    return json.loads(
        json.dumps({"projects": [item.__dict__ for item in items], "pages": 1})
    )


def test_search_matches_the_description_not_only_the_title():
    hidden = ProjectSummary(id="a/one", name="Nothing", description="bypasses ssl pinning")
    other = ProjectSummary(id="b/two", name="Other", description="unrelated")
    hits = index_module.search(_payload(hidden, other), "ssl")
    assert [item.id for _, item in hits] == ["a/one"]


def test_search_requires_every_term():
    item = ProjectSummary(id="a/one", name="SSL bypass", description="android")
    assert index_module.search(_payload(item), "ssl android")
    assert index_module.search(_payload(item), "ssl windows") == []


def test_search_ranks_the_name_above_the_description():
    named = ProjectSummary(id="a/one", name="ssl unpinning", description="x")
    described = ProjectSummary(id="b/two", name="Other", description="about ssl")
    hits = index_module.search(_payload(described, named), "ssl")
    assert [item.id for _, item in hits] == ["a/one", "b/two"]


def test_search_breaks_ties_by_views():
    quiet = ProjectSummary(id="a/one", name="ssl", description="x", views="100")
    loud = ProjectSummary(id="b/two", name="ssl", description="x", views="1M")
    hits = index_module.search(_payload(quiet, loud), "ssl")
    assert [item.id for _, item in hits] == ["b/two", "a/one"]


def test_empty_query_returns_everything():
    items = [ProjectSummary(id=f"a/{n}", name=str(n), description="") for n in range(3)]
    assert len(index_module.search(_payload(*items), "  ")) == 3
