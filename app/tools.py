import re
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from httpx import HTTPError

from app.client import NotFound, _client_
from app.models import (
    GrepResult,
    Project,
    ProjectSummary,
    SearchResult,
    SourceMatch,
    UserProfile,
)
from app.scraper import parse_project, parse_search, parse_user, views_to_int

mcp = FastMCP(
    name="frida-codeshare",
    instructions=(
        "Frida instrumentation scripts published on codeshare.frida.re.\n"
        "Workflow: search_projects (cheap, paginated) -> pick an `id` -> get_project "
        "or grep_project_source with that same `id`.\n"
        "Every `id` is \"creator/slug\". From it you can build the page URL "
        "(https://codeshare.frida.re/@{id}/) and the run command "
        "(`frida --codeshare {id} -f YOUR_BINARY`) yourself; they are not returned "
        "per result to save tokens.\n"
        "search_projects defaults to 20 results with descriptions clipped to 200 "
        "chars; raise `limit`/`max_description_chars` or page with `offset` only when "
        "needed (limit=0 returns everything and is expensive).\n"
        "Scripts can be long: prefer grep_project_source to inspect a specific API, "
        "and get_project's source_offset/source_max_chars to page through the body."
    ),
)

PLATFORM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "android": ("android", "apk", "dalvik", "art ", "java.perform", "frida-server"),
    "ios": ("ios", "iphone", "ipad", "objc", "objective-c", "jailbreak", "ipa"),
    "windows": ("windows", "win32", "win64", ".exe", "dll"),
    "linux": ("linux", "elf", "glibc"),
    "macos": ("macos", "mac os", "osx", "darwin"),
}


def _tool_error(what: str, exc: Exception) -> ToolError:
    return ToolError(f"codeshare.frida.re request failed for {what}: {exc}")


def _fetch(fn, what: str, *args) -> str:
    try:
        return fn(*args)
    except NotFound as exc:
        raise ToolError(f"not found on codeshare.frida.re: {what}") from exc
    except HTTPError as exc:
        raise _tool_error(what, exc) from exc


def _parse(parser, html: str, what: str):
    try:
        return parser(html)
    except ValueError as exc:
        raise ToolError(f"unexpected page layout for {what}: {exc}") from exc


def _split_id(username: str, project_name: str = "") -> tuple[str, str]:
    """Accept ("dzonerzy", "fridantiroot") or ("dzonerzy/fridantiroot", "")."""
    handle = username.lstrip("@").strip().strip("/")
    slug = project_name.lstrip("@").strip().strip("/")
    if "/" in handle:
        handle, _, embedded = handle.partition("/")
        slug = slug or embedded.strip("/")
    return handle, slug


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _matches_platform(item: ProjectSummary, platform: str) -> bool:
    if platform == "any":
        return True
    haystack = f"{item.name} {item.description} {item.id}".lower()
    return any(word in haystack for word in PLATFORM_KEYWORDS[platform])


def _page(items: list, limit: int, offset: int) -> tuple[list, bool]:
    start = max(offset, 0)
    window = items[start:] if limit <= 0 else items[start : start + limit]
    return window, start + len(window) < len(items)


@mcp.tool
def search_projects(
    query: str,
    limit: int = 20,
    offset: int = 0,
    sort_by: Literal["relevance", "likes", "views"] = "relevance",
    min_likes: int = 0,
    platform: Literal["any", "android", "ios", "windows", "linux", "macos"] = "any",
    max_description_chars: int = 200,
) -> SearchResult:
    """Search codeshare.frida.re for Frida scripts matching a keyword.

    Args:
        query: Search terms, e.g. "ssl pinning" or "jailbreak".
        limit: Results per page. 0 returns all matches (expensive: 100+ entries).
        offset: Skip this many results; use with `total` to page.
        sort_by: "relevance" keeps the site's own order; "likes"/"views" sort desc.
        min_likes: Drop results with fewer likes than this.
        platform: Keyword heuristic over name/description; not an official tag.
        max_description_chars: Clip each description. 0 keeps it whole.

    Returns lean entries: `id` ("creator/slug"), name, description, likes, views.
    Pass `id` to get_project for the source, or grep_project_source to search it.
    """
    if not query.strip():
        raise ToolError("query is required")

    what = f"search {query!r}"
    html = _fetch(_client_.search, what, query)
    echoed, items = _parse(parse_search, html, what)

    if min_likes > 0:
        items = [i for i in items if (i.likes or 0) >= min_likes]
    if platform != "any":
        items = [i for i in items if _matches_platform(i, platform)]

    if sort_by == "likes":
        items.sort(key=lambda i: i.likes or 0, reverse=True)
    elif sort_by == "views":
        items.sort(key=lambda i: views_to_int(i.views), reverse=True)

    total = len(items)
    window, truncated = _page(items, limit, offset)
    results = [
        ProjectSummary(
            id=i.id,
            name=i.name,
            description=_clip(i.description, max_description_chars),
            likes=i.likes,
            views=i.views,
        )
        for i in window
    ]
    return SearchResult(
        query=echoed or query,
        total=total,
        offset=max(offset, 0),
        returned=len(results),
        truncated=truncated,
        results=results,
    )


@mcp.tool
def get_user_projects(
    username: str,
    limit: int = 50,
    offset: int = 0,
    max_description_chars: int = 200,
) -> UserProfile:
    """List projects published by a codeshare.frida.re user.

    Args:
        username: Author handle, with or without the leading "@" (e.g. "akabe1").
        limit: Results per page. 0 returns all of them.
        offset: Skip this many projects; use with `total` to page.
        max_description_chars: Clip each description. 0 keeps it whole.
    """
    handle, _ = _split_id(username)
    if not handle:
        raise ToolError("username is required")

    what = f"user @{handle}"
    html = _fetch(_client_.get_item, what, handle)
    parsed_name, items = _parse(parse_user, html, what)

    total = len(items)
    window, truncated = _page(items, limit, offset)
    projects = [
        ProjectSummary(
            id=i.id,
            name=i.name,
            description=_clip(i.description, max_description_chars),
            likes=i.likes,
            views=i.views,
        )
        for i in window
    ]
    return UserProfile(
        username=parsed_name or handle,
        total=total,
        offset=max(offset, 0),
        returned=len(projects),
        truncated=truncated,
        projects=projects,
    )


def _load_project(username: str, project_name: str) -> Project:
    handle, slug = _split_id(username, project_name)
    if not handle or not slug:
        raise ToolError(
            'both username and project_name are required, or pass "creator/slug" '
            "as username"
        )
    what = f"project @{handle}/{slug}"
    html = _fetch(_client_.get_item, what, handle, slug)
    return _parse(parse_project, html, what)


@mcp.tool
def get_project(
    username: str,
    project_name: str = "",
    include_source: bool = True,
    source_offset: int = 0,
    source_max_chars: int = 8000,
) -> Project:
    """Fetch one codeshare.frida.re project and (a slice of) its script source.

    Args:
        username: Author handle, or the whole "creator/slug" id from a search result.
        project_name: Project slug (omit if `username` already holds "creator/slug").
        include_source: False returns metadata only.
        source_offset: Character offset to start reading the source at.
        source_max_chars: Max source characters to return. 0 returns the rest.

    `source_total_chars` and `source_truncated` tell you whether to call again with
    a higher `source_offset`. To find a specific API instead, use grep_project_source.
    """
    project = _load_project(username, project_name)
    source = project.snippet or ""
    project.source_total_chars = len(source)

    if not include_source:
        project.snippet = None
        project.source_offset = 0
        project.source_truncated = bool(source)
        return project

    start = max(source_offset, 0)
    end = len(source) if source_max_chars <= 0 else start + source_max_chars
    project.snippet = source[start:end]
    project.source_offset = start
    project.source_truncated = end < len(source)
    return project


@mcp.tool
def grep_project_source(
    username: str,
    pattern: str,
    project_name: str = "",
    context_lines: int = 3,
    max_matches: int = 20,
    ignore_case: bool = True,
) -> GrepResult:
    """Search inside a project's script source without downloading all of it.

    Args:
        username: Author handle, or the whole "creator/slug" id from a search result.
        pattern: Regular expression, e.g. "Java\\.perform" or "SSL|X509".
        project_name: Project slug (omit if `username` already holds "creator/slug").
        context_lines: Lines of context around each match.
        max_matches: Stop after this many matches. 0 returns all of them.
        ignore_case: Case-insensitive matching.

    Returns the matching line numbers with their surrounding lines. Follow up with
    get_project(source_offset=...) when you need a larger contiguous block.
    """
    if not pattern.strip():
        raise ToolError("pattern is required")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError(f"invalid regular expression {pattern!r}: {exc}") from exc

    project = _load_project(username, project_name)
    lines = (project.snippet or "").splitlines()

    hits = [n for n, line in enumerate(lines) if regex.search(line)]
    kept = hits if max_matches <= 0 else hits[:max_matches]

    wanted: set[int] = set()
    for n in kept:
        wanted.update(range(max(n - context_lines, 0), min(n + context_lines + 1, len(lines))))

    matches = [SourceMatch(line=n + 1, text=lines[n]) for n in sorted(wanted)]
    return GrepResult(
        id=project.id,
        pattern=pattern,
        total_matches=len(hits),
        returned=len(kept),
        truncated=len(kept) < len(hits),
        matches=matches,
    )
