from dataclasses import dataclass


@dataclass
class Project:
    name: str
    description: str
    url: str | None = None
    slug: str | None = None
    creator: str | None = None
    likes: int | None = None
    views: str | None = None
    command: str | None = None
    fingerprint: str | None = None
    snippet: str | None = None


@dataclass
class ProjectList:
    projects: list[Project]


@dataclass
class SearchResult:
    query: str
    results: list[Project]


@dataclass
class UserProfile:
    username: str
    projects: list[Project]
