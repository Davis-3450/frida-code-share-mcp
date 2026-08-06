from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from httpx import HTTPError, Response

from app.client import _client_
from app.models import Project, SearchResult, UserProfile
from app.scraper import parse_project, parse_search, parse_user

mcp = FastMCP(
    name="frida-codeshare",
    instructions=(
        "Search and read Frida instrumentation scripts published on codeshare.frida.re. "
        "Use search_projects to find scripts by keyword, get_user_projects to list everything "
        "published by an author, and get_project to fetch a script's source and its "
        "`frida --codeshare` command."
    ),
)


def _parse(parser, html: str, what: str):
    try:
        return parser(html)
    except ValueError as exc:
        raise ToolError(f"unexpected page layout for {what}: {exc}") from exc


def _fetch(response: Response, what: str) -> str:
    if response.status_code == 404:
        raise ToolError(f"not found on codeshare.frida.re: {what}")
    try:
        response.raise_for_status()
    except HTTPError as exc:
        raise ToolError(f"codeshare.frida.re request failed for {what}: {exc}") from exc
    return response.text


@mcp.tool
def search_projects(query: str, limit: int = 200) -> SearchResult:
    """Search codeshare.frida.re for Frida scripts matching a keyword.

    Args:
        query: Search terms, e.g. "ssl pinning" or "jailbreak".
        limit: Maximum number of results to return. Use 0 for all of them.

    Returns the matching projects with author, likes, views and the ready-to-run
    `frida --codeshare` command. Source code is not included: call get_project for that.
    """
    try:
        response = _client_.query("search/", params={"query": query})
    except HTTPError as exc:
        raise ToolError(
            f"codeshare.frida.re request failed for search {query!r}: {exc}"
        ) from exc

    result = _parse(
        parse_search, _fetch(response, f"search {query!r}"), f"search {query!r}"
    )
    if limit > 0:
        result.results = result.results[:limit]
    return result


@mcp.tool
def get_user_projects(username: str) -> UserProfile:
    """List every project published by a codeshare.frida.re user.

    Args:
        username: Author handle, with or without the leading "@" (e.g. "akabe1").
    """
    handle = username.lstrip("@").strip("/")
    if not handle:
        raise ToolError("username is required")
    try:
        response = _client_.get_item(handle)
    except HTTPError as exc:
        raise ToolError(
            f"codeshare.frida.re request failed for @{handle}: {exc}"
        ) from exc

    return _parse(parse_user, _fetch(response, f"user @{handle}"), f"user @{handle}")


@mcp.tool
def get_project(
    username: str, project_name: str, include_source: bool = True
) -> Project:
    """Fetch a single codeshare.frida.re project, including its script source.

    Args:
        username: Author handle, with or without the leading "@" (e.g. "dzonerzy").
        project_name: Project slug as it appears in the URL (e.g. "fridantiroot").
        include_source: Set to False to skip the script body when only metadata is needed.
    """
    handle = username.lstrip("@").strip("/")
    slug = project_name.strip("/")
    target = f"@{handle}/{slug}"
    if not handle or not slug:
        raise ToolError("both username and project_name are required")
    try:
        response = _client_.get_item(handle, slug)
    except HTTPError as exc:
        raise ToolError(
            f"codeshare.frida.re request failed for {target}: {exc}"
        ) from exc

    project = _parse(
        parse_project, _fetch(response, f"project {target}"), f"project {target}"
    )
    if not include_source:
        project.snippet = None
    return project
