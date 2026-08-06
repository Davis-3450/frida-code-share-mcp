import re
import time
from datetime import datetime, timezone
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from httpx import HTTPError

from app import index as index_module
from app.client import NotFound, _client_
from app.models import (
    BrowseResult,
    GrepResult,
    IndexStatus,
    Project,
    ProjectSummary,
    SearchResult,
    SourceMatch,
    UserProfile,
)
from app.scraper import (
    parse_browse,
    parse_project,
    parse_search,
    parse_user,
    views_to_int,
)

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
        "and get_project's source_offset/source_max_chars to page through the body.\n"
        "browse_projects walks the site's catalogue page by page. For repeated "
        "exploration, build_index downloads that whole catalogue once and "
        "search_index then queries it offline (no requests, matches descriptions "
        "too, unlike search_projects)."
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
    results = _summaries(window, max_description_chars)
    return SearchResult(
        query=echoed or query,
        total=total,
        offset=max(offset, 0),
        returned=len(results),
        truncated=truncated,
        results=results,
    )


def _summaries(items: list[ProjectSummary], max_description_chars: int):
    return [
        ProjectSummary(
            id=i.id,
            name=i.name,
            description=_clip(i.description, max_description_chars),
            likes=i.likes,
            views=i.views,
        )
        for i in items
    ]


@mcp.tool
def browse_projects(
    page: int = 1,
    limit: int = 20,
    offset: int = 0,
    max_description_chars: int = 200,
) -> BrowseResult:
    """List the codeshare.frida.re catalogue one page at a time.

    Args:
        page: 1-based page number. The site clamps anything past the end to the
            last page (it answers 200, never 404), so check the returned `page`.
        limit: Entries returned from that page. 0 returns the whole page.
        offset: Skip this many entries within the page.
        max_description_chars: Clip each description. 0 keeps it whole.

    `total_pages` is read from the site's pagination widget. To search the whole
    catalogue instead of paging through it, use build_index + search_index.
    """
    wanted = max(page, 1)
    what = f"browse page {wanted}"
    html = _fetch(_client_.browse, what, wanted)
    served, total_pages, items = _parse(parse_browse, html, what)

    total = len(items)
    window, truncated = _page(items, limit, offset)
    return BrowseResult(
        page=served,
        total_pages=total_pages,
        total=total,
        offset=max(offset, 0),
        returned=len(window),
        truncated=truncated,
        projects=_summaries(window, max_description_chars),
    )


def _index_status(payload: dict | None) -> IndexStatus:
    path = str(index_module.index_path())
    if not payload:
        return IndexStatus(exists=False, projects=0, pages=0, path=path)
    built_at = payload.get("built_at") or 0
    return IndexStatus(
        exists=True,
        projects=len(payload.get("projects", [])),
        pages=int(payload.get("pages", 0)),
        built_at=datetime.fromtimestamp(built_at, timezone.utc).isoformat(
            timespec="seconds"
        )
        if built_at
        else None,
        age_seconds=int(time.time() - built_at) if built_at else None,
        path=path,
        failed_pages=list(payload.get("failed_pages", [])),
    )


@mcp.tool
def build_index(refresh: bool = False, max_pages: int = 0) -> IndexStatus:
    """Download the whole browse catalogue once so searches can run offline.

    Args:
        refresh: Re-download even if an index already exists.
        max_pages: Stop after this many browse pages (0 = all of them).

    Walks every page of https://codeshare.frida.re/browse/ (~45 pages, one
    request each) and stores the result on disk. Call it once; afterwards
    search_index answers with no network traffic. Returns the index status —
    call with refresh=False to just check what is already cached. A few browse
    pages answer 500 permanently; they are skipped and reported in
    `failed_pages`.
    """
    existing = index_module.load_index()
    if existing and not refresh:
        return _index_status(existing)

    what = "browse catalogue"
    try:
        payload = index_module.build_index(max_pages=max_pages)
    except NotFound as exc:
        raise ToolError(f"not found on codeshare.frida.re: {what}") from exc
    except HTTPError as exc:
        raise _tool_error(what, exc) from exc
    except ValueError as exc:
        raise ToolError(f"unexpected page layout for {what}: {exc}") from exc
    except OSError as exc:
        raise ToolError(f"could not write the index to disk: {exc}") from exc
    return _index_status(payload)


@mcp.tool
def search_index(
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    sort_by: Literal["relevance", "likes", "views"] = "relevance",
    min_likes: int = 0,
    platform: Literal["any", "android", "ios", "windows", "linux", "macos"] = "any",
    max_description_chars: int = 200,
) -> SearchResult:
    """Search the locally indexed catalogue. No network requests.

    Args:
        query: Terms matched against name, id and description (all must appear).
            Empty returns the whole catalogue, ranked only by the sort.
        limit: Results per page. 0 returns everything (expensive).
        offset: Skip this many results; use with `total` to page.
        sort_by: "relevance" ranks by where the terms matched, then views.
        min_likes: Drop results with fewer likes than this.
        platform: Keyword heuristic over name/description; not an official tag.
        max_description_chars: Clip each description. 0 keeps it whole.

    Requires build_index first. Unlike search_projects this also matches
    descriptions and covers every published project, not just the site's own hits.
    """
    payload = index_module.load_index()
    if payload is None:
        raise ToolError("no local index yet: call build_index first")

    items = [item for _, item in index_module.search(payload, query)]

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
    return SearchResult(
        query=query,
        total=total,
        offset=max(offset, 0),
        returned=len(window),
        truncated=truncated,
        results=_summaries(window, max_description_chars),
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
    projects = _summaries(window, max_description_chars)
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
        # nothing was returned, so anything non-empty is still unread
        project.source_truncated = bool(source)
        return project

    start = min(max(source_offset, 0), len(source))
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
