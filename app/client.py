"""HTTP access to codeshare.frida.re, with a two-layer page cache.

Pages are static enough that repeated tool calls should not hit the network.
A bounded in-memory LRU sits in front of a plain directory of ``<sha256>.html``
files, so the cache also survives a process restart.

Both layers are controlled by the environment, read on every request:

``FRIDA_CS_CACHE_TTL``  seconds; ``0`` disables caching entirely
``FRIDA_CS_CACHE_DIR``  directory; empty string disables the disk layer
"""

import hashlib
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

from httpx import Client

DEFAULT_TTL = 3600
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "frida-codeshare-mcp"
MAX_MEMORY_ENTRIES = 64  # pages are up to ~100KB each; keep the cache bounded
PRUNE_INTERVAL = 300  # seconds between disk-cache sweeps


class NotFound(Exception):
    """The requested user or project does not exist on codeshare."""


def _ttl() -> int:
    try:
        return int(os.environ["FRIDA_CS_CACHE_TTL"])
    except (KeyError, ValueError):
        return DEFAULT_TTL


def _cache_dir() -> Path | None:
    raw = os.environ.get("FRIDA_CS_CACHE_DIR")
    if raw is None:
        return DEFAULT_CACHE_DIR
    raw = raw.strip()
    return Path(raw) if raw else None


class CodeShareClient:
    BASE_URL = "https://codeshare.frida.re/"

    def __init__(self):
        self.client = Client(
            base_url=self.BASE_URL, timeout=10.0, follow_redirects=True
        )
        self._memory: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._last_prune = 0.0

    # https://codeshare.frida.re/search/?query=root
    def query(self, endpoint: str, params: dict[str, str] | None = None) -> str:
        url = self.client.build_request("GET", endpoint, params=params).url
        key = hashlib.sha256(str(url).encode("utf-8")).hexdigest()
        ttl = _ttl()

        if ttl > 0:
            cached = self._from_memory(key, ttl)
            if cached is None:
                cached = self._from_disk(key, ttl)
                if cached is not None:
                    self._to_memory(key, cached)
            if cached is not None:
                return cached

        response = self.client.get(url)
        if response.status_code == 404:
            raise NotFound(endpoint)
        response.raise_for_status()
        html = response.text

        if ttl > 0:
            self._to_memory(key, html)
            self._to_disk(key, html, ttl)
        return html

    # https://codeshare.frida.re/@dzonerzy/fridantiroot/
    # https://codeshare.frida.re/@akabe1/
    def get_item(self, username: str, project_name: str | None = None) -> str:
        # safe="" so that a slash inside a segment cannot forge a new path.
        endpoint = f"@{quote(username, safe='')}/"
        if project_name:
            endpoint += f"{quote(project_name, safe='')}/"
        return self.query(endpoint)

    # https://codeshare.frida.re/search/?query=root
    def search(self, query: str) -> str:
        return self.query("search/", params={"query": query})

    # https://codeshare.frida.re/browse/?page=1
    def browse(self, page: int = 1) -> str:
        return self.query("browse/", params={"page": str(page)})

    def clear_cache(self) -> None:
        self._memory.clear()
        directory = _cache_dir()
        if directory is None:
            return
        for path in directory.glob("*.html"):
            _unlink(path)

    # -- memory layer ----------------------------------------------------

    def _from_memory(self, key: str, ttl: int) -> str | None:
        entry = self._memory.get(key)
        if entry is None:
            return None
        stored_at, html = entry
        if time.time() - stored_at >= ttl:
            del self._memory[key]
            return None
        self._memory.move_to_end(key)
        return html

    def _to_memory(self, key: str, html: str) -> None:
        self._memory[key] = (time.time(), html)
        self._memory.move_to_end(key)
        while len(self._memory) > MAX_MEMORY_ENTRIES:
            self._memory.popitem(last=False)

    # -- disk layer ------------------------------------------------------
    # Cache failures are never fatal: a broken cache dir must not break a
    # request that the network already answered.

    def _from_disk(self, key: str, ttl: int) -> str | None:
        directory = _cache_dir()
        if directory is None:
            return None
        path = directory / f"{key}.html"
        try:
            if time.time() - path.stat().st_mtime >= ttl:
                _unlink(path)
                return None
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _to_disk(self, key: str, html: str, ttl: int) -> None:
        directory = _cache_dir()
        if directory is None:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{key}.html"
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(html, encoding="utf-8")
            os.replace(tmp, path)  # atomic: readers never see a partial page
        except OSError:
            return
        self._prune(directory, ttl)

    def _prune(self, directory: Path, ttl: int) -> None:
        now = time.time()
        if now - self._last_prune < PRUNE_INTERVAL:
            return
        self._last_prune = now
        try:
            entries = list(directory.glob("*.html"))
        except OSError:
            return
        for path in entries:
            try:
                expired = now - path.stat().st_mtime >= ttl
            except OSError:
                continue
            if expired:
                _unlink(path)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


_client_ = CodeShareClient()
