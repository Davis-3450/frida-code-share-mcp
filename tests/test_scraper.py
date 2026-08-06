import pytest

from app.scraper import (
    parse_browse,
    parse_pagination,
    parse_project,
    parse_search,
    parse_user,
    views_to_int,
)


def test_parse_pagination_reads_the_last_page(browse_html):
    assert parse_pagination(browse_html) == (1, 45)


def test_parse_pagination_on_the_last_page(browse_last_html):
    # Out-of-range pages are served as this same page, so the widget is the
    # only way to know which page the answer really is.
    assert parse_pagination(browse_last_html) == (45, 45)


def test_parse_pagination_without_a_widget(user_html):
    assert parse_pagination(user_html) == (1, 1)


def test_parse_browse(browse_html):
    page, total_pages, items = parse_browse(browse_html)
    assert (page, total_pages) == (1, 45)
    assert len(items) == 16
    assert all(item.id and item.name for item in items)


def test_parse_search_echoes_query_and_finds_every_article(search_html):
    query, items = parse_search(search_html)
    assert query == "root"
    assert len(items) == 144
    assert all(item.id and item.name for item in items)

    top = items[0]
    assert top.id == "dzonerzy/fridantiroot"
    assert top.name == "fridantiroot"
    assert top.description == "Android antiroot checks bypass"
    assert top.likes == 53
    assert top.views == "192K"


def test_parse_user(user_html):
    username, items = parse_user(user_html)
    assert username == "akabe1"
    assert [i.id for i in items] == [
        "akabe1/frida-universal-pinning-bypasser",
        "akabe1/frida-multiple-unpinning",
    ]
    assert items[1].likes == 64


def test_parse_project(project_html):
    project = parse_project(project_html)
    assert project.id == "dzonerzy/fridantiroot"
    assert project.creator == "dzonerzy"
    assert project.slug == "fridantiroot"
    assert project.name == "fridantiroot"
    assert project.description == "Android antiroot checks bypass"
    assert project.command == "frida --codeshare dzonerzy/fridantiroot -f YOUR_BINARY"
    assert project.fingerprint == (
        "94f95c0a18df49d62867b2f51e25f0cf1699a30ace91e502c5bea2a1900469df"
    )
    assert project.source_total_chars == len(project.snippet) == 14700
    assert "Java.perform" in project.snippet


def test_parse_project_rejects_a_listing_page(user_html):
    with pytest.raises(ValueError):
        parse_project(user_html)


def test_parse_search_rejects_a_project_page(project_html):
    with pytest.raises(ValueError):
        parse_search(project_html)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192K", 192_000),
        ("1.2M", 1_200_000),
        ("834", 834),
        ("1,234", 1234),
        ("2B", 2_000_000_000),
        ("", 0),
        (None, 0),
        ("garbage", 0),
    ],
)
def test_views_to_int(raw, expected):
    assert views_to_int(raw) == expected
