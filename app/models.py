from dataclasses import dataclass


@dataclass
class project:
    name: str
    description: str
    command: str
    fingerprint: str
    likes: int
    views: int
    creator: str
    snippet: str


@dataclass
class ProjectList:
    projects: list[project]


@dataclass
class SearchResult:
    query: str
    results: list[project]


@dataclass
class UserProfile:
    username: str
    projects: list[project]
