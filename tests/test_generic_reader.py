"""Response-driven rendering and egress contracts. All pages and credentials are synthetic."""

from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import socket
import sys

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))
import plugin as fetch_plugin

URL = "https://previously-unseen.example/article"
TEXT = "这是通用读取测试的有效正文，不依赖任何特定网站名称或一级标题。" * 12
ARTICLE = f"<html><head><title>通用文档</title></head><body><main><p>{TEXT}</p></main></body></html>"
SHELL = "<html><head><title>应用</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline test attempted a real connection")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def pipeline(monkeypatch):
    instance = fetch_plugin.create_plugin()
    instance._ctx = SimpleNamespace(logger=Mock(), llm=SimpleNamespace(generate=AsyncMock(side_effect=AssertionError("Unexpected model call"))))
    instance._jina_enabled = False
    instance._llm_summarize = False
    instance._alt_max_images = 0
    instance._fetch_cache_enabled = True
    instance._fetch_cache = fetch_plugin.FetchResultCache(8, 600)
    state = {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "content": ARTICLE.encode(), "handler": None}
    requests = []
    def handle(request):
        requests.append(request)
        if state["handler"] is not None:
            return state["handler"](request)
        return httpx.Response(state["status"], headers=state["headers"], content=state["content"], request=request)
    monkeypatch.setattr(instance, "_assert_host_allowed", AsyncMock())
    monkeypatch.setattr(instance, "_build_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True))
    monkeypatch.setattr(instance, "_apply_vlm_alt_text", AsyncMock(side_effect=lambda text, url: (text, 0)))
    monkeypatch.setattr(instance, "_fetch_browser_document", AsyncMock(return_value=(ARTICLE, "https://canonical.example/document", {"content_type": "text/html", "browser_route": "generic"})))
    monkeypatch.setattr(instance, "_fetch_via_jina", AsyncMock(side_effect=AssertionError("Unexpected Jina call")))
    return instance, state, requests


def read(instance, url=URL, **kwargs):
    return asyncio.run(instance._fetch_url_impl(url=url, start_char=kwargs.get("start", 0), end_char=kwargs.get("end", -1), on_exceed="truncate", summary_focus=""))


def test_good_http_article_never_launches_browser(pipeline):
    instance, _, requests = pipeline
    result = read(instance)
    assert result["provider"] == "markdownify"
    assert TEXT in result["content"]
    assert len(requests) == 1
    instance._fetch_browser_document.assert_not_awaited()


@pytest.mark.parametrize("url", [URL, "https://another-unseen.example/a", "https://unrelated.example/a.pdf"])
def test_script_shell_upgrades_once_by_content_and_keeps_original_url(pipeline, url):
    instance, state, _ = pipeline
    state["content"] = SHELL.encode()
    result = read(instance, url)
    assert result["provider"] == "browser"
    assert result["final_url"] == "https://canonical.example/document"
    assert result["metadata"]["browser_route"] == "generic"
    instance._fetch_browser_document.assert_awaited_once_with(url)
    assert TEXT in result["content"]
    cached = read(instance, url)
    assert cached["cached"] is True
    instance._fetch_browser_document.assert_awaited_once()


def test_automatic_challenge_may_upgrade_but_explicit_denial_may_not(pipeline):
    instance, state, _ = pipeline
    state.update(status=403, headers={"content-type": "text/html", "cf-mitigated": "challenge"}, content=SHELL.encode())
    assert read(instance)["provider"] == "browser"
    instance._fetch_browser_document.assert_awaited_once()


@pytest.mark.parametrize(("status", "headers", "content", "code"), [
    (403, {"content-type": "text/html"}, b"<html><body>Access denied</body></html>", "ACCESS_DENIED"),
    (429, {"content-type": "text/html"}, ARTICLE.encode(), "RATE_LIMITED"),
    (401, {"content-type": "text/html"}, ARTICLE.encode(), "AUTH_REQUIRED"),
    (202, {"content-type": "application/json", "x-amzn-waf-action": "challenge"}, b'{"challenge": true}', "BROWSER_CHALLENGE"),
    (200, {"content-type": "text/html"}, b"<title>Human Verification</title><body>Complete CAPTCHA</body>", "CAPTCHA_REQUIRED"),
])
def test_denials_never_upgrade_or_cache(pipeline, status, headers, content, code):
    instance, state, _ = pipeline
    state.update(status=status, headers=headers, content=content)
    with pytest.raises(fetch_plugin.FetchUrlError, match=code):
        read(instance)
    instance._fetch_browser_document.assert_not_awaited()
    assert instance._fetch_cache.get(instance._fetch_cache_key(URL)) is None


def test_rendering_disabled_is_an_explicit_error(pipeline):
    instance, state, _ = pipeline
    state["content"] = SHELL.encode()
    instance._browser_enabled = False
    with pytest.raises(fetch_plugin.FetchUrlError, match="RENDER_REQUIRED"):
        read(instance)
    instance._fetch_browser_document.assert_not_awaited()


def test_failed_render_is_not_retried_or_cached(pipeline):
    instance, state, _ = pipeline
    state["content"] = SHELL.encode()
    instance._fetch_browser_document.side_effect = fetch_plugin.FetchUrlError("CAPTCHA_REQUIRED: synthetic")
    with pytest.raises(fetch_plugin.FetchUrlError, match="CAPTCHA_REQUIRED"):
        read(instance)
    instance._fetch_browser_document.assert_awaited_once()
    assert instance._fetch_cache.get(instance._fetch_cache_key(URL)) is None


def test_jina_is_not_retried_after_its_direct_fallback_requires_rendering(pipeline):
    instance, state, requests = pipeline
    state["content"] = SHELL.encode()
    instance._jina_enabled = True
    instance._fetch_via_jina.side_effect = fetch_plugin.FetchUrlError("synthetic Jina failure")
    assert read(instance)["provider"] == "browser"
    instance._fetch_via_jina.assert_awaited_once()
    instance._fetch_browser_document.assert_awaited_once()
    assert len(requests) == 2


def test_enabled_jina_can_resolve_automatic_challenge_before_browser(pipeline):
    instance, state, _ = pipeline
    state.update(status=403, headers={"content-type": "text/html", "cf-mitigated": "challenge"}, content=SHELL.encode())
    instance._jina_enabled = True
    instance._fetch_via_jina.side_effect = None
    instance._fetch_via_jina.return_value = "# Synthetic remote document\n\n" + TEXT
    assert read(instance)["provider"] == "jina"
    instance._fetch_via_jina.assert_awaited_once()
    instance._fetch_browser_document.assert_not_awaited()


def test_json_keeps_structured_text_path(pipeline):
    instance, state, _ = pipeline
    state.update(headers={"content-type": "application/json"}, content=b'{"answer": 42}')
    result = read(instance)
    assert "42" in result["content"]
    instance._fetch_browser_document.assert_not_awaited()


def test_image_keeps_binary_path(pipeline, monkeypatch):
    instance, state, _ = pipeline
    state.update(headers={"content-type": "image/png"}, content=b"\x89PNG\r\n\x1a\n" + b"synthetic" * 4)
    image = AsyncMock(return_value={"success": True, "provider": "image"})
    monkeypatch.setattr(instance, "_build_image_result", image)
    assert read(instance)["provider"] == "image"
    image.assert_awaited_once()
    instance._fetch_browser_document.assert_not_awaited()


def test_pdf_keeps_pdf_extraction_path(pipeline, monkeypatch):
    instance, state, _ = pipeline
    state.update(headers={"content-type": "application/pdf"}, content=b"%PDF-1.7\nsynthetic document")
    extract = Mock(return_value=(TEXT, {"title": "Synthetic PDF"}))
    monkeypatch.setattr(fetch_plugin, "_pdf_to_markdown_blocking", extract)
    assert read(instance)["provider"] == "pypdf"
    extract.assert_called_once()
    instance._fetch_browser_document.assert_not_awaited()


def test_first_response_chunk_cannot_bypass_download_limit(pipeline):
    instance, state, _ = pipeline
    instance._max_download_size = 16
    state["content"] = b"x" * 100
    with pytest.raises(fetch_plugin.FetchUrlError, match="DOWNLOAD_LIMIT"):
        read(instance)
    instance._fetch_browser_document.assert_not_awaited()


def test_empty_render_is_never_cached(pipeline):
    instance, state, _ = pipeline
    state["content"] = SHELL.encode()
    instance._fetch_browser_document.return_value = ("", URL, {})
    with pytest.raises(fetch_plugin.FetchUrlError, match="EMPTY_DOCUMENT"):
        read(instance)
    assert instance._fetch_cache.get(instance._fetch_cache_key(URL)) is None


class MemoryWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False
    def write(self, data):
        self.data.extend(data)
    async def drain(self):
        return None
    def can_write_eof(self):
        return False
    def is_closing(self):
        return self.closed
    def close(self):
        self.closed = True
    async def wait_closed(self):
        return None


async def serve_proxy(guard, monkeypatch, raw):
    incoming = asyncio.StreamReader()
    incoming.feed_data(raw)
    incoming.feed_eof()
    upstream = asyncio.StreamReader()
    upstream.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    upstream.feed_eof()
    client_writer, origin_writer = MemoryWriter(), MemoryWriter()
    connect = AsyncMock(return_value=(upstream, origin_writer))
    monkeypatch.setattr(guard, "_connect", connect)
    answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 80))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
    await guard._serve(incoming, client_writer)
    return client_writer, origin_writer, connect


def auth_header(guard):
    return "Proxy-Authorization: Basic " + b64encode(guard._credentials).decode() + "\r\n"


def test_plain_http_proxy_pins_destination_and_strips_proxy_credentials(monkeypatch):
    guard = fetch_plugin._BrowserTunnelGuard()
    raw = ("GET http://public.example/path?q=1 HTTP/1.1\r\nHost: attacker.invalid\r\n" + auth_header(guard) + "Proxy-Connection: keep-alive\r\n\r\n").encode()
    client, origin, connect = asyncio.run(serve_proxy(guard, monkeypatch, raw))
    connect.assert_awaited_once_with([(socket.AF_INET, "8.8.8.8")], 80, upstream_authority="public.example:80")
    assert bytes(origin.data).startswith(b"GET /path?q=1 HTTP/1.1\r\nHost: public.example\r\n")
    assert b"proxy-authorization" not in bytes(origin.data).lower()
    assert b"attacker.invalid" not in origin.data
    assert bytes(client.data).startswith(b"HTTP/1.1 200 OK")
    assert client.closed and origin.closed


def test_proxy_rejects_missing_authentication(monkeypatch):
    guard = fetch_plugin._BrowserTunnelGuard()
    raw = b"GET http://public.example/ HTTP/1.1\r\nHost: public.example\r\n\r\n"
    client, _, connect = asyncio.run(serve_proxy(guard, monkeypatch, raw))
    assert b"407 Proxy Authentication Required" in client.data
    connect.assert_not_awaited()


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://169.254.169.254/", "http://public.example:8080/"])
def test_plain_http_proxy_blocks_unsafe_targets_before_connect(monkeypatch, target):
    guard = fetch_plugin._BrowserTunnelGuard()
    raw = (f"GET {target} HTTP/1.1\r\nHost: public.example\r\n" + auth_header(guard) + "\r\n").encode()
    client, _, connect = asyncio.run(serve_proxy(guard, monkeypatch, raw))
    assert b"403 Forbidden" in client.data
    connect.assert_not_awaited()


def test_proxy_response_budget_is_shared_across_resources():
    guard = fetch_plugin._BrowserTunnelGuard(max_download_size=8)
    async def run():
        for data in (b"12345", b"67890"):
            stream = asyncio.StreamReader()
            stream.feed_data(data)
            stream.feed_eof()
            if guard._received_bytes:
                with pytest.raises(fetch_plugin.FetchUrlError, match="DOWNLOAD_LIMIT"):
                    await guard._pump(stream, MemoryWriter(), receiving=True)
            else:
                await guard._pump(stream, MemoryWriter(), receiving=True)
    asyncio.run(run())


def test_direct_socket_connect_uses_validated_numeric_ip(monkeypatch):
    guard = fetch_plugin._BrowserTunnelGuard()
    open_connection = AsyncMock(return_value=(SimpleNamespace(), MemoryWriter()))
    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    asyncio.run(guard._connect([(socket.AF_INET, "8.8.8.8")], 80))
    assert open_connection.await_args.args == ("8.8.8.8", 80)
    assert open_connection.await_args.kwargs["flags"] == socket.AI_NUMERICHOST


@pytest.mark.parametrize("fake", [False, True])
def test_upstream_pins_public_ip_and_only_delegates_authorized_fake_dns(monkeypatch, fake):
    guard = fetch_plugin._BrowserTunnelGuard("http://127.0.0.1:7890", allow_system_fake_ip=True)
    guard._upstream_options = guard._parse_upstream()
    async def run():
        ip = "198.18.33.154" if fake else "8.8.8.8"
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        addresses = await guard._target("origin.example:443")
        stream = asyncio.StreamReader()
        stream.feed_data(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        stream.feed_eof()
        writer = MemoryWriter()
        connect = AsyncMock(return_value=(stream, writer))
        monkeypatch.setattr(asyncio, "open_connection", connect)
        await guard._connect(addresses, upstream_authority="origin.example:443")
        expected = "origin.example:443" if fake else "8.8.8.8:443"
        assert bytes(writer.data).startswith(f"CONNECT {expected} HTTP/1.1\r\n".encode())
        await guard._close_writer(writer)
    asyncio.run(run())
