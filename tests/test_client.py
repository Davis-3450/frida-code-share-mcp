import httpx
import pytest

from app.client import MAX_MEMORY_ENTRIES, CodeShareClient, NotFound


def build(monkeypatch, tmp_path, ttl: str, handler) -> tuple[CodeShareClient, list[str]]:
    """A client whose transport is a callable, plus the log of URLs it hit."""
    seen: list[str] = []

    def track(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    monkeypatch.setenv("FRIDA_CS_CACHE_TTL", ttl)
    monkeypatch.setenv("FRIDA_CS_CACHE_DIR", str(tmp_path))
    client = CodeShareClient()
    client.client = httpx.Client(
        base_url=CodeShareClient.BASE_URL,
        transport=httpx.MockTransport(track),
        follow_redirects=True,
    )
    return client, seen


def ok(body="<html>hi</html>"):
    return lambda request: httpx.Response(200, text=body)


def test_second_call_is_served_from_cache(monkeypatch, tmp_path):
    client, seen = build(monkeypatch, tmp_path, "3600", ok())
    assert client.search("root") == "<html>hi</html>"
    client.search("root")
    assert len(seen) == 1


def test_disk_cache_survives_a_new_client(monkeypatch, tmp_path):
    first, _ = build(monkeypatch, tmp_path, "3600", ok())
    first.search("root")

    second, seen = build(monkeypatch, tmp_path, "3600", ok("<html>other</html>"))
    assert second.search("root") == "<html>hi</html>"
    assert seen == []


def test_ttl_zero_disables_caching(monkeypatch, tmp_path):
    client, seen = build(monkeypatch, tmp_path, "0", ok())
    client.search("root")
    client.search("root")
    assert len(seen) == 2
    assert list(tmp_path.glob("*.html")) == []


def test_empty_cache_dir_disables_the_disk_layer(monkeypatch, tmp_path):
    client, _ = build(monkeypatch, tmp_path, "3600", ok())
    monkeypatch.setenv("FRIDA_CS_CACHE_DIR", "")
    client.search("root")
    assert list(tmp_path.glob("*.html")) == []


def test_invalid_ttl_falls_back_to_the_default(monkeypatch, tmp_path):
    client, seen = build(monkeypatch, tmp_path, "not-a-number", ok())
    client.search("root")
    client.search("root")
    assert len(seen) == 1


def test_different_queries_do_not_collide(monkeypatch, tmp_path):
    client, seen = build(monkeypatch, tmp_path, "3600", ok())
    client.search("root")
    client.search("ssl")
    client.get_item("akabe1")
    client.get_item("akabe1", "frida-multiple-unpinning")
    assert len(seen) == 4
    assert seen[-1].endswith("/@akabe1/frida-multiple-unpinning/")


def test_memory_cache_is_bounded(monkeypatch, tmp_path):
    client, _ = build(monkeypatch, tmp_path, "3600", ok())
    for n in range(MAX_MEMORY_ENTRIES + 10):
        client.search(f"query-{n}")
    assert len(client._memory) == MAX_MEMORY_ENTRIES


def test_clear_cache_drops_the_memory_layer(monkeypatch, tmp_path):
    client, _ = build(monkeypatch, tmp_path, "3600", ok())
    client.search("root")
    client.clear_cache()
    assert client._memory == {}


def test_404_raises_not_found(monkeypatch, tmp_path):
    client, _ = build(
        monkeypatch, tmp_path, "3600", lambda r: httpx.Response(404, text="nope")
    )
    with pytest.raises(NotFound):
        client.get_item("nobody")


def test_500_raises_an_http_error(monkeypatch, tmp_path):
    client, _ = build(
        monkeypatch, tmp_path, "3600", lambda r: httpx.Response(500, text="boom")
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.search("root")


def test_transport_errors_propagate(monkeypatch, tmp_path):
    def explode(request):
        raise httpx.ConnectError("down", request=request)

    client, _ = build(monkeypatch, tmp_path, "3600", explode)
    with pytest.raises(httpx.HTTPError):
        client.search("root")


def test_failures_are_not_cached(monkeypatch, tmp_path):
    state = {"fail": True}

    def flaky(request):
        if state["fail"]:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="<html>ok</html>")

    client, _ = build(monkeypatch, tmp_path, "3600", flaky)
    with pytest.raises(httpx.HTTPStatusError):
        client.search("root")
    state["fail"] = False
    assert client.search("root") == "<html>ok</html>"


def test_path_segments_are_escaped(monkeypatch, tmp_path):
    client, seen = build(monkeypatch, tmp_path, "0", ok())
    client.get_item("weird user", "a/b")
    assert seen[0].endswith("/@weird%20user/a%2Fb/")
