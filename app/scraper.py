import json
import re

from bs4 import BeautifulSoup

from app.models import Project, SearchResult, UserProfile

_URL_RE = re.compile(r"@([^/]+)/([^/]+)/?\s*$")
_STATS_RE = re.compile(r"([\d.,]+[KMkm]?)")
_FINGERPRINT_RE = re.compile(r"Fingerprint:\s*([0-9a-fA-F]{64})")
_QUERY_RE = re.compile(r'Search Results for\s*"(.*)"\s*$', re.DOTALL)
_USERNAME_RE = re.compile(r"@(.+?)'s Projects")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _split_url(href: str | None) -> tuple[str | None, str | None]:
    """('https://.../@dzonerzy/fridantiroot/') -> ('dzonerzy', 'fridantiroot')"""
    if not href:
        return None, None
    match = _URL_RE.search(href)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _vue_str(html: str, key: str) -> str | None:
    """Read a JS string literal from the Vue data block and decode its escapes."""
    match = re.search(rf'{key}:\s*("(?:[^"\\]|\\.)*")', html)
    if not match:
        return None
    return json.loads(match.group(1))


def _parse_stats(text: str) -> tuple[int | None, str | None]:
    """'53 | 192K' -> (53, '192K')"""
    values = _STATS_RE.findall(text)
    likes = None
    views = None
    if values:
        try:
            likes = int(values[0].replace(",", ""))
        except ValueError:
            likes = None
    if len(values) > 1:
        views = values[1]
    return likes, views


def _parse_article(article, default_creator: str | None = None) -> Project:
    link = article.select_one("h2 a")
    href = link.get("href") if link else None
    creator, slug = _split_url(href)

    uploader = article.select_one("h4 a")
    if uploader:
        creator = uploader.get_text(strip=True).lstrip("@") or creator
    if not creator:
        creator = default_creator

    stats = article.select_one("h3")
    likes, views = (
        _parse_stats(stats.get_text(" ", strip=True)) if stats else (None, None)
    )

    desc = article.select_one("p")

    return Project(
        name=link.get_text(strip=True) if link else "",
        description=desc.get_text(strip=True) if desc else "",
        url=href,
        slug=slug,
        creator=creator,
        likes=likes,
        views=views,
        command=f"frida --codeshare {creator}/{slug} -f YOUR_BINARY"
        if creator and slug
        else None,
    )


def parse_articles(html: str, default_creator: str | None = None) -> list[Project]:
    soup = _soup(html)
    posts = soup.select_one("div.posts")
    if posts is None:
        raise ValueError("no 'div.posts' block found: not a project listing page")
    return [
        _parse_article(a, default_creator)
        for a in posts.select("article")
        if a.select_one("h2 a") is not None
    ]


def parse_search(html: str) -> SearchResult:
    soup = _soup(html)
    heading = soup.select_one("section header.major h2")
    query = ""
    if heading:
        match = _QUERY_RE.search(heading.get_text(strip=True))
        if match:
            query = match.group(1)
    return SearchResult(query=query, results=parse_articles(html))


def parse_user(html: str) -> UserProfile:
    soup = _soup(html)
    heading = soup.select_one("section header.major h2")
    username = ""
    if heading:
        match = _USERNAME_RE.search(heading.get_text(strip=True))
        if match:
            username = match.group(1)
    return UserProfile(
        username=username, projects=parse_articles(html, default_creator=username)
    )


def parse_project(html: str) -> Project:
    soup = _soup(html)
    section = soup.select_one("section#editProject")
    if section is None:
        raise ValueError("no 'section#editProject' block found: not a project page")

    name = _vue_str(html, "projectName")
    slug = _vue_str(html, "projectSlug")
    description = _vue_str(html, "projectDesc")
    snippet = _vue_str(html, "projectSource")

    if name is None and slug is None:
        raise ValueError("no Vue data block found: not a project page")

    fingerprint = None
    match = _FINGERPRINT_RE.search(section.get_text(" ", strip=True))
    if match:
        fingerprint = match.group(1)

    creator = None
    for tag in soup.select("a[href*='/@']"):
        href = tag.get("href")
        if not isinstance(href, str):
            continue
        href_creator, _ = _split_url(href.rstrip("/") + "/x/")
        if href_creator:
            creator = href_creator
            break

    command = None
    pre = section.select_one("pre")
    if pre:
        command = pre.get_text(strip=True).lstrip("$ ").strip()
        if slug:
            command = command.replace("${projectSlug}", slug)
        if creator is None:
            cmd_match = re.search(r"--codeshare\s+([^/\s]+)/", command)
            if cmd_match:
                creator = cmd_match.group(1)
    elif creator and slug:
        command = f"frida --codeshare {creator}/{slug} -f YOUR_BINARY"

    return Project(
        name=name or slug or "",
        description=description or "",
        url=f"https://codeshare.frida.re/@{creator}/{slug}/"
        if creator and slug
        else None,
        slug=slug,
        creator=creator,
        command=command,
        fingerprint=fingerprint,
        snippet=snippet,
    )
