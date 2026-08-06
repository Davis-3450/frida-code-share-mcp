from dataclasses import dataclass, field


@dataclass
class ProjectSummary:
    """Lean listing entry.

    `id` is the canonical "creator/slug" key: pass it straight to get_project.
    The page URL is https://codeshare.frida.re/@{id}/ and the run command is
    `frida --codeshare {id} -f YOUR_BINARY`, so neither is repeated here.
    """

    id: str
    name: str
    description: str
    likes: int | None = None
    views: str | None = None


@dataclass
class SearchResult:
    query: str
    total: int
    offset: int
    returned: int
    truncated: bool
    results: list[ProjectSummary] = field(default_factory=list)


@dataclass
class UserProfile:
    username: str
    total: int
    offset: int
    returned: int
    truncated: bool
    projects: list[ProjectSummary] = field(default_factory=list)


@dataclass
class BrowseResult:
    """One page of https://codeshare.frida.re/browse/.

    `page` is the page the site actually served: it clamps out-of-range numbers
    to the last page instead of returning 404, so `page` may differ from what
    was requested. `total_pages` comes from the pagination widget.
    """

    page: int
    total_pages: int
    total: int
    offset: int
    returned: int
    truncated: bool
    projects: list[ProjectSummary] = field(default_factory=list)


@dataclass
class IndexStatus:
    """State of the local offline index of every browse page."""

    exists: bool
    projects: int
    pages: int
    built_at: str | None = None
    age_seconds: int | None = None
    path: str | None = None
    # Pages the site answered 500 for; their projects are missing from the index.
    failed_pages: list[int] = field(default_factory=list)


@dataclass
class Project:
    id: str
    name: str
    description: str
    creator: str | None = None
    slug: str | None = None
    command: str | None = None
    fingerprint: str | None = None
    snippet: str | None = None
    source_total_chars: int = 0
    source_offset: int = 0
    source_truncated: bool = False


@dataclass
class SourceMatch:
    line: int
    text: str


@dataclass
class GrepResult:
    id: str
    pattern: str
    total_matches: int
    returned: int
    truncated: bool
    matches: list[SourceMatch] = field(default_factory=list)
