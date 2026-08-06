import json
import re

from bs4 import BeautifulSoup

from app.models import Project, ProjectSummary

_URL_RE = re.compile(r"@([^/]+)/([^/]+)/?\s*$")
_STATS_RE = re.compile(r"([\d.,]+[KMkm]?)")
_FINGERPRINT_RE = re.compile(r"Fingerprint:\s*([0-9a-fA-F]{64})")
_QUERY_RE = re.compile(r'Search Results for\s*"(.*)"\s*$', re.DOTALL)
_USERNAME_RE = re.compile(r"@(.+?)'s Projects")
_PAGE_RE = re.compile(r"[?&]page=(\d+)")

_SUFFIXES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


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


def views_to_int(views: str | None) -> int:
    """'192K' -> 192000, '1.2M' -> 1200000, '834' -> 834, None -> 0"""
    if not views:
        return 0
    text = views.replace(",", "").strip()
    multiplier = 1
    if text and text[-1].lower() in _SUFFIXES:
        multiplier = _SUFFIXES[text[-1].lower()]
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


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


def _parse_article(article, default_creator: str | None = None) -> ProjectSummary:
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
    name = link.get_text(strip=True) if link else ""

    return ProjectSummary(
        id=f"{creator}/{slug}" if creator and slug else name,
        name=name,
        description=desc.get_text(strip=True) if desc else "",
        likes=likes,
        views=views,
    )


def parse_articles(
    html: str, default_creator: str | None = None
) -> list[ProjectSummary]:
    soup = _soup(html)
    posts = soup.select_one("div.posts")
    if posts is None:
        raise ValueError("no 'div.posts' block found: not a project listing page")
    return [
        _parse_article(a, default_creator)
        for a in posts.select("article")
        if a.select_one("h2 a") is not None
    ]


def parse_search(html: str) -> tuple[str, list[ProjectSummary]]:
    """-> (echoed query, summaries)"""
    soup = _soup(html)
    heading = soup.select_one("section header.major h2")
    query = ""
    if heading:
        match = _QUERY_RE.search(heading.get_text(strip=True))
        if match:
            query = match.group(1)
    return query, parse_articles(html)


def parse_user(html: str) -> tuple[str, list[ProjectSummary]]:
    """-> (username, summaries)"""
    soup = _soup(html)
    heading = soup.select_one("section header.major h2")
    username = ""
    if heading:
        match = _USERNAME_RE.search(heading.get_text(strip=True))
        if match:
            username = match.group(1)
    return username, parse_articles(html, default_creator=username)


def parse_pagination(html: str) -> tuple[int, int]:
    """-> (current page, total pages); (1, 1) when there is no pagination widget.

    The widget lists every page, so the highest number in it is the last page.
    """
    soup = _soup(html)
    block = soup.select_one("ul.pagination")
    if block is None:
        return 1, 1

    current = 1
    active = block.select_one("li.active")
    if active:
        match = re.search(r"\d+", active.get_text(" ", strip=True))
        if match:
            current = int(match.group())

    pages = {current}
    for link in block.select("li a[href]"):
        href = link.get("href")
        if isinstance(href, str):
            match = _PAGE_RE.search(href)
            if match:
                pages.add(int(match.group(1)))
    return current, max(pages)


def parse_browse(html: str) -> tuple[int, int, list[ProjectSummary]]:
    """-> (current page, total pages, summaries)"""
    current, total_pages = parse_pagination(html)
    return current, total_pages, parse_articles(html)


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
        id=f"{creator}/{slug}" if creator and slug else (slug or name or ""),
        name=name or slug or "",
        description=description or "",
        creator=creator,
        slug=slug,
        command=command,
        fingerprint=fingerprint,
        snippet=snippet,
        source_total_chars=len(snippet) if snippet else 0,
    )
