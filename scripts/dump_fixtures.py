"""Re-download the HTML fixtures used to develop app/scraper.py."""

import os
from pathlib import Path

os.environ["FRIDA_CS_CACHE_TTL"] = "0"  # always hit the network here

from app.client import CodeShareClient  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FIXTURES.mkdir(parents=True, exist_ok=True)

client = CodeShareClient()

(FIXTURES / "query.html").write_text(client.search("root"), encoding="utf-8")
(FIXTURES / "project.html").write_text(
    client.get_item("dzonerzy", "fridantiroot"), encoding="utf-8"
)
(FIXTURES / "user.html").write_text(client.get_item("akabe1"), encoding="utf-8")

# Page 1 plus the last page, which is also what the site serves for anything
# past the end (it clamps instead of 404ing).
from app.scraper import parse_pagination  # noqa: E402

first = client.browse(1)
(FIXTURES / "browse.html").write_text(first, encoding="utf-8")
_, last_page = parse_pagination(first)
(FIXTURES / "browse_last.html").write_text(client.browse(last_page), encoding="utf-8")
