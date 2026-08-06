"""End-to-end tool calls through an in-memory FastMCP client, served from fixtures."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.tools import mcp

pytestmark = pytest.mark.usefixtures("offline")


def call(name: str, **arguments):
    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(name, arguments)
            return result.structured_content

    return asyncio.run(run())


def test_tools_are_registered():
    async def run():
        async with Client(mcp) as client:
            return sorted(t.name for t in await client.list_tools())

    assert asyncio.run(run()) == [
        "browse_projects",
        "build_index",
        "get_project",
        "get_user_projects",
        "grep_project_source",
        "search_index",
        "search_projects",
    ]


# ---- browse_projects / index -----------------------------------------


def test_browse_reports_the_pagination():
    result = call("browse_projects", page=1, limit=5)
    assert result["page"] == 1
    assert result["total_pages"] == 45
    assert result["returned"] == 5
    assert result["truncated"] is True


def test_browse_past_the_end_reports_the_page_it_was_served():
    # The site answers 200 with the last page instead of 404ing.
    result = call("browse_projects", page=4999, limit=0)
    assert result["page"] == 45
    assert result["total_pages"] == 45


def test_build_index_then_search_offline(index_dir):
    status = call("build_index")
    assert status["exists"] is True
    assert status["projects"] > 0

    result = call("search_index", query="jailbreak", limit=5)
    assert result["total"] >= 1
    assert result["returned"] <= 5


def test_search_index_without_an_index(index_dir):
    with pytest.raises(ToolError, match="call build_index first"):
        call("search_index", query="ssl")


def test_build_index_is_idempotent_without_refresh(index_dir):
    first = call("build_index")
    again = call("build_index")
    assert again["projects"] == first["projects"]
    assert again["built_at"] == first["built_at"]


# ---- search_projects -------------------------------------------------


def test_search_paginates_and_clips():
    first = call("search_projects", query="root", limit=3)
    assert first["total"] == 144
    assert first["returned"] == 3
    assert first["truncated"] is True
    assert all(len(r["description"]) <= 201 for r in first["results"])

    second = call("search_projects", query="root", limit=3, offset=3)
    assert second["offset"] == 3
    assert [r["id"] for r in second["results"]] != [
        r["id"] for r in first["results"]
    ]


def test_search_limit_zero_returns_everything():
    result = call("search_projects", query="root", limit=0)
    assert result["returned"] == result["total"] == 144
    assert result["truncated"] is False


def test_search_sort_by_likes_and_views():
    likes = [r["likes"] or 0 for r in call("search_projects", query="root", limit=10, sort_by="likes")["results"]]
    assert likes == sorted(likes, reverse=True)

    from app.scraper import views_to_int

    views = [views_to_int(r["views"]) for r in call("search_projects", query="root", limit=10, sort_by="views")["results"]]
    assert views == sorted(views, reverse=True)


def test_search_min_likes_filters():
    result = call("search_projects", query="root", limit=0, min_likes=20)
    assert result["total"] < 144
    assert all((r["likes"] or 0) >= 20 for r in result["results"])


def test_search_platform_filter_is_a_subset():
    everything = call("search_projects", query="root", limit=0)["total"]
    android = call("search_projects", query="root", limit=0, platform="android")
    assert 0 < android["total"] < everything


def test_search_untruncated_descriptions():
    clipped = call("search_projects", query="root", limit=0)["results"]
    whole = call("search_projects", query="root", limit=0, max_description_chars=0)["results"]
    assert any(len(w["description"]) > len(c["description"]) for c, w in zip(clipped, whole))
    assert not any(w["description"].endswith("…") for w in whole)


def test_search_offset_past_the_end_is_empty_not_an_error():
    result = call("search_projects", query="root", offset=9999)
    assert result["returned"] == 0
    assert result["truncated"] is False


def test_search_requires_a_query():
    with pytest.raises(ToolError, match="query is required"):
        call("search_projects", query="   ")


# ---- get_user_projects -----------------------------------------------


def test_get_user_projects_accepts_an_at_prefix():
    result = call("get_user_projects", username="@akabe1")
    assert result["username"] == "akabe1"
    assert result["total"] == result["returned"] == 2
    assert result["truncated"] is False


def test_get_user_projects_unknown_user():
    with pytest.raises(ToolError, match="not found"):
        call("get_user_projects", username="nobody-here")


def test_get_user_projects_requires_a_username():
    with pytest.raises(ToolError, match="username is required"):
        call("get_user_projects", username="@")


# ---- get_project -----------------------------------------------------


def test_get_project_accepts_a_combined_id():
    project = call("get_project", username="dzonerzy/fridantiroot")
    assert project["id"] == "dzonerzy/fridantiroot"
    assert project["source_total_chars"] == 14700
    assert len(project["snippet"]) == 8000
    assert project["source_truncated"] is True


def test_get_project_accepts_split_arguments():
    assert (
        call("get_project", username="dzonerzy", project_name="fridantiroot")["id"]
        == "dzonerzy/fridantiroot"
    )


def test_get_project_source_slicing_is_contiguous():
    head = call("get_project", username="dzonerzy/fridantiroot", source_max_chars=100)
    tail = call(
        "get_project",
        username="dzonerzy/fridantiroot",
        source_offset=100,
        source_max_chars=100,
    )
    whole = call("get_project", username="dzonerzy/fridantiroot", source_max_chars=0)
    assert head["snippet"] + tail["snippet"] == whole["snippet"][:200]
    assert whole["source_truncated"] is False


def test_get_project_offset_past_the_end():
    project = call("get_project", username="dzonerzy/fridantiroot", source_offset=10**6)
    assert project["snippet"] == ""
    assert project["source_truncated"] is False


def test_get_project_without_source():
    project = call(
        "get_project", username="dzonerzy/fridantiroot", include_source=False
    )
    assert project["snippet"] is None
    assert project["source_total_chars"] == 14700
    assert project["source_truncated"] is True


def test_get_project_needs_both_parts():
    with pytest.raises(ToolError, match="both username and project_name"):
        call("get_project", username="dzonerzy")


def test_get_project_unknown():
    with pytest.raises(ToolError, match="not found"):
        call("get_project", username="dzonerzy/nope")


# ---- grep_project_source ---------------------------------------------


def test_grep_returns_matches_with_context():
    result = call(
        "grep_project_source", username="dzonerzy/fridantiroot", pattern=r"Java\.perform"
    )
    assert result["id"] == "dzonerzy/fridantiroot"
    assert result["total_matches"] >= 1
    assert result["truncated"] is False
    assert any("Java.perform" in m["text"] for m in result["matches"])
    lines = [m["line"] for m in result["matches"]]
    assert lines == sorted(set(lines))


def test_grep_respects_max_matches():
    everything = call(
        "grep_project_source",
        username="dzonerzy/fridantiroot",
        pattern="var",
        max_matches=0,
    )
    capped = call(
        "grep_project_source",
        username="dzonerzy/fridantiroot",
        pattern="var",
        max_matches=2,
    )
    assert everything["total_matches"] > 2
    assert capped["returned"] == 2
    assert capped["truncated"] is True


def test_grep_case_sensitivity():
    sensitive = call(
        "grep_project_source",
        username="dzonerzy/fridantiroot",
        pattern="JAVA",
        max_matches=0,
        ignore_case=False,
    )
    insensitive = call(
        "grep_project_source",
        username="dzonerzy/fridantiroot",
        pattern="JAVA",
        max_matches=0,
    )
    assert sensitive["total_matches"] == 0
    assert insensitive["total_matches"] > 0


def test_grep_zero_context_returns_only_matching_lines():
    result = call(
        "grep_project_source",
        username="dzonerzy/fridantiroot",
        pattern=r"Java\.perform",
        context_lines=0,
    )
    assert len(result["matches"]) == result["total_matches"]


def test_grep_rejects_a_bad_regex():
    with pytest.raises(ToolError, match="invalid regular expression"):
        call("grep_project_source", username="dzonerzy/fridantiroot", pattern="[")


def test_grep_requires_a_pattern():
    with pytest.raises(ToolError, match="pattern is required"):
        call("grep_project_source", username="dzonerzy/fridantiroot", pattern=" ")
