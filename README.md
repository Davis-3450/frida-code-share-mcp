# frida-codeshare-mcp

MCP server for [codeshare.frida.re](https://codeshare.frida.re) — search, read and
grep published Frida instrumentation scripts.

Tool output is optimized for LLM context: listings return a lean `id`/name/
description/likes/views record (no repeated URLs or `frida --codeshare` boilerplate),
are paginated, and script sources can be read in slices or grepped instead of pulled
whole. A default `search_projects` call costs ~1.1K tokens instead of ~15.5K.

## Install

```bash
uv sync
```

## Run

```bash
uv run main.py            # or: uv run frida-code-share-mcp
```

MCP client config:

```json
{
  "mcpServers": {
    "frida-codeshare": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/frida-code-share-mcp", "main.py"]
    }
  }
}
```

## Guide

### Which tool for which question

| You want | Use |
|---|---|
| "find a script that does X" | `search_projects` — one request, no setup |
| "find *every* script about X, including by description" | `build_index` once, then `search_index` |
| "what is published on the site at all" | `browse_projects` page by page, or `search_index(query="")` |
| "everything by this author" | `get_user_projects` |
| "read this script" | `get_project` |
| "does this script hook API Y?" | `grep_project_source` — far cheaper than reading it |

### The normal workflow

1. **Find** — `search_projects(query="ssl pinning", platform="android")`.
   Every result carries an `id` like `pcipolloni/universal-android-ssl-pinning-bypass-with-frida`.
2. **Pick** — sort by `likes` or `views` to skip abandoned scripts;
   `min_likes=10` prunes noise.
3. **Inspect** — pass that same `id` straight to `get_project` or
   `grep_project_source` as `username`. No need to split it.
4. **Run** — `frida --codeshare {id} -f YOUR_BINARY`. The tools never return
   that command per result; build it from the `id`.

```
search_projects(query="ssl pinning", platform="android", sort_by="likes")
  -> id = "akabe1/frida-multiple-unpinning"
grep_project_source(username="akabe1/frida-multiple-unpinning", pattern="SSLContext")
get_project(username="akabe1/frida-multiple-unpinning", source_max_chars=4000)
frida --codeshare akabe1/frida-multiple-unpinning -f com.example.app
```

### Reading long scripts without burning context

Scripts run to tens of thousands of characters. Don't pull one whole unless you
need it.

- `grep_project_source(pattern=...)` returns only matching lines plus context.
  Start here.
- `get_project` returns the first 8000 chars by default and reports
  `source_total_chars` / `source_truncated`. Page with
  `source_offset=8000`, then `16000`, … Slices are contiguous, so concatenating
  them reproduces the file exactly.
- `get_project(include_source=False)` for metadata only (fingerprint, creator,
  run command).

### Going offline with the index

`search_projects` hits the site's search endpoint, which **only matches
titles**. A script whose title is "MyBypass" but whose description says "SSL
pinning" is invisible to it. The local index fixes that:

```
build_index()                       # ~45 requests, ~11s, once
search_index(query="ssl pinning")   # 0 requests, ~20ms, matches descriptions too
```

The index is a single JSON file (~130KB, ~684 projects). It never expires on its
own — call `build_index(refresh=True)` to rebuild. `build_index()` with no
arguments is also the cheap way to *check* an existing index: it returns the
status without re-downloading.

`search_index` only knows what the browse listing shows (id, name, description,
likes, views). It does **not** search script bodies — for that, narrow with
`search_index`, then `grep_project_source` the few candidates.

### Cost notes

- Everything is paginated and descriptions are clipped to 200 chars by default.
  `limit=0` returns the whole result set and is expensive — a bare
  `search_projects(query="root", limit=0)` is 144 entries.
- Pages are cached for an hour, so repeating a call is free. Re-reading a
  project at different `source_offset`s costs one request total.

## Tools

Every listing entry carries an `id` of the form `creator/slug`. Derive the rest
yourself instead of asking for it:

- page: `https://codeshare.frida.re/@{id}/`
- run: `frida --codeshare {id} -f YOUR_BINARY`

### `search_projects`

| Param | Default | Notes |
|---|---|---|
| `query` | — | e.g. `"ssl pinning"` |
| `limit` | `20` | `0` = all matches (expensive) |
| `offset` | `0` | page with `total` |
| `sort_by` | `relevance` | `relevance` \| `likes` \| `views` |
| `min_likes` | `0` | drop low-signal results |
| `platform` | `any` | `android` \| `ios` \| `windows` \| `linux` \| `macos` — keyword heuristic over name/description, not an official tag |
| `max_description_chars` | `200` | `0` = untruncated |

Returns `{query, total, offset, returned, truncated, results[]}`.

### `browse_projects`

`page=1`, `limit=20`, `offset=0`, `max_description_chars=200`.
Returns `{page, total_pages, total, offset, returned, truncated, projects[]}`.

`total_pages` is read from the site's pagination widget (45 at the time of
writing). The site answers **200 for out-of-range pages**, clamping them to the
last one instead of 404ing — so trust the returned `page`, not the one you asked
for.

### `build_index` / `search_index`

`build_index(refresh=False, max_pages=0)` walks every browse page once (~45
requests, ~11s) and writes the whole catalogue to disk. `search_index` then
queries it **with zero network requests**.

| | `search_projects` | `search_index` |
|---|---|---|
| source | site search endpoint | local index |
| requests per query | 1 | 0 |
| matches | title only | title + id + description |
| coverage | the site's own hits | every published project (~684) |
| needs setup | no | `build_index` once |

`search_index` takes the same `query`/`limit`/`offset`/`sort_by`/`min_likes`/
`platform`/`max_description_chars` as `search_projects` and returns the same
shape; an empty `query` returns the whole catalogue. Terms are ANDed and ranked
by where they matched (name > id > description), ties broken by views.

`build_index` returns `{exists, projects, pages, built_at, age_seconds, path,
failed_pages}`. A couple of browse pages answer **500 permanently** (broken rows
on the site); they are skipped and listed in `failed_pages` rather than aborting
the build.

### `get_user_projects`

`username` (with or without `@`), `limit=50`, `offset=0`, `max_description_chars=200`.
Returns `{username, total, offset, returned, truncated, projects[]}`.

### `get_project`

`username` (accepts a whole `creator/slug` id), `project_name=""`,
`include_source=True`, `source_offset=0`, `source_max_chars=8000`.

Returns the project plus `source_total_chars` / `source_offset` /
`source_truncated`, so a long script can be paged through.

### `grep_project_source`

`username` (or `creator/slug`), `pattern` (regex), `project_name=""`,
`context_lines=3`, `max_matches=20`, `ignore_case=True`.

Returns matching line numbers with context — the cheap way to inspect a large
script for a specific API.

## Examples

```
search_projects(query="ssl pinning", platform="android", sort_by="likes")
get_project(username="dzonerzy/fridantiroot", source_max_chars=2000)
grep_project_source(username="dzonerzy/fridantiroot", pattern="Java\\.perform")

build_index()                                     # once, ~11s
search_index(query="ssl pinning", sort_by="views")  # offline afterwards
```

## Caching

Pages are cached in memory and on disk for 1 hour.

| Env var | Default | Effect |
|---|---|---|
| `FRIDA_CS_CACHE_TTL` | `3600` | Seconds. `0` disables caching. |
| `FRIDA_CS_CACHE_DIR` | system temp `/frida-codeshare-mcp` | Empty string disables the disk layer. |
| `FRIDA_CS_INDEX_PATH` | `$FRIDA_CS_CACHE_DIR/browse-index.json` | Where `build_index` stores the offline catalogue (~130KB). |

Expired disk entries are swept periodically, and the memory layer keeps at most
64 pages (LRU).

## Development

```bash
uv sync --group dev
uv run pytest
```

The suite is fully offline: it replays the HTML fixtures in `tests/fixtures/`.
`uv run python scripts/dump_fixtures.py` refreshes them from the live site — do
that whenever codeshare.frida.re changes its markup and the scraper tests start
failing.
