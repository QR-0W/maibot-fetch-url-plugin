"""Browser reader contracts. All documents, configuration and services are synthetic."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import socket
import sys

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import plugin as fetch_plugin


BAIKE_URL = "https://baike.baidu.com/item/offline-document/123456"
KKBOX_URL = "https://www.kkbox.com/sg/sc/song/offline-song-id"
PARAGRAPH = "这是离线测试的原文段落，用于验证正文读取、字符窗口和缓存，所有内容均为合成数据。" * 12
DOCUMENT = f"<html><head><title>离线文档</title></head><body><h1>离线文档</h1><p>{PARAGRAPH}</p></body></html>"
CAPTCHA = "<html><head><title>Human Verification</title></head><body><h1>Human Verification</h1><p>Complete the CAPTCHA.</p></body></html>"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Offline test attempted DNS or a network connection")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def reader(monkeypatch):
    instance = fetch_plugin.create_plugin()
    instance._ctx = SimpleNamespace(logger=Mock(), llm=SimpleNamespace(generate=AsyncMock(side_effect=AssertionError("Unexpected model service"))))
    instance._browser_enabled = True
    instance._browser_timeout = 30
    instance._browser_baike_proxy = "inherit"
    instance._browser_kkbox_proxy = "direct"
    instance._proxy = ""
    instance._user_agent = "OfflineReader/1.0"
    instance._global_cookies = ""
    instance._domain_cookies = {}
    instance._jina_enabled = False
    instance._llm_summarize = False
    instance._alt_max_images = 0
    instance._max_content_length = 8192
    instance._fetch_cache_enabled = True
    instance._fetch_cache = fetch_plugin.FetchResultCache(8, 600)
    monkeypatch.setattr(instance, "_assert_host_allowed", AsyncMock())
    monkeypatch.setattr(instance, "_probe_url", AsyncMock(return_value={"kind": "text", "data": DOCUMENT.encode(), "encoding": "utf-8", "content_type": "text/html", "final_url": BAIKE_URL}))
    monkeypatch.setattr(instance, "_summarize", AsyncMock(side_effect=AssertionError("Unexpected summary service")))
    monkeypatch.setattr(instance, "_apply_vlm_alt_text", AsyncMock(side_effect=lambda text, url: (text, 0)))
    context = SimpleNamespace()
    # A real async context manager is needed only by the explicitly tested plain-HTTP route.
    class OfflineClient:
        async def __aenter__(self):
            return context

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(instance, "_build_client", Mock(return_value=OfflineClient()))
    monkeypatch.setattr(instance, "_fetch_browser_document", AsyncMock(return_value=(DOCUMENT, BAIKE_URL, {"title": "离线文档", "content_type": "text/html"})), raising=False)
    return instance


async def fetch(instance, url=BAIKE_URL, start=0, end=-1, on_exceed="truncate"):
    return await instance._fetch_url_impl(url=url, start_char=start, end_char=end, on_exceed=on_exceed, summary_focus="")


@pytest.mark.parametrize("url", [
    BAIKE_URL, KKBOX_URL, "https://example.org/", "http://another.example/path",
    "https://cdn.unrelated.example/script.js", "https://8.8.8.8/", "https://[2606:4700:4700::1111]/",
])
def test_browser_resource_policy_is_not_site_specific(url):
    assert fetch_plugin._browser_resource_allowed(url)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/", "http://10.0.0.1/", "https://[::1]/", "https://169.254.169.254/",
    "https://localhost/", "https://service.internal/", "https://host.local/",
    "https://user:password@example.org/", "http://example.org:8080/", "https://example.org:444/",
    "file:///tmp/document", "data:text/html,body", "ftp://example.org/a", "https://[invalid/",
])
def test_browser_resource_policy_rejects_local_credentials_ports_and_protocols(url):
    assert not fetch_plugin._browser_resource_allowed(url)


@pytest.mark.parametrize(("status", "headers", "html", "code"), [
    (202, {"x-amzn-waf-action": "challenge"}, "<script>run()</script>", "RENDER_REQUIRED"),
    (403, {"cf-mitigated": "challenge"}, "<html><body><div id='root'></div><script>run()</script></body></html>", "RENDER_REQUIRED"),
    (405, {"x-amzn-waf-action": "captcha"}, CAPTCHA, "CAPTCHA_REQUIRED"),
    (403, {}, "<html><body>Access Denied</body></html>", "ACCESS_DENIED"),
    (429, {}, DOCUMENT, "RATE_LIMITED"),
    (401, {}, DOCUMENT, "AUTH_REQUIRED"),
    (200, {}, CAPTCHA, "CAPTCHA_REQUIRED"),
    (200, {}, "<title>某服务安全验证</title><body>请完成验证码。</body>", "CAPTCHA_REQUIRED"),
    (200, {}, "<title>Sign in</title><input type='password'>", "AUTH_REQUIRED"),
    (200, {}, "<title>Access Denied</title><body>Request rejected</body>", "ACCESS_DENIED"),
])
def test_response_classification_is_explicit(status, headers, html, code):
    assert code in fetch_plugin._browser_access_error(status, headers, html)


def test_normal_article_is_not_a_challenge_or_render_trigger():
    assert fetch_plugin._browser_access_error(200, {}, DOCUMENT) == ""
    assert not fetch_plugin._html_needs_rendering(DOCUMENT)


@pytest.mark.parametrize("html", [
    "<html><body><div id='root'></div><script src='/app.js'></script></body></html>",
    "<html><body><noscript>Please enable JavaScript</noscript></body></html>",
    "<html><head><title>Title alone is not content</title></head><body></body></html>",
])
def test_empty_and_script_dependent_html_requires_rendering(html):
    assert fetch_plugin._html_needs_rendering(html)


@pytest.mark.parametrize("heading", ["", "通用文档", "任意网站名称"])
def test_rendered_body_does_not_require_h1_or_site_heading(heading):
    html = f"<html><body><h2>{heading}</h2><p>{PARAGRAPH}</p></body></html>"
    fetch_plugin._validate_browser_document("https://one.example/short", "https://another.example/canonical", 200, {}, html)


@pytest.mark.parametrize("html", [
    "<html><body>空</body></html>",
    f"<html><head><script>{PARAGRAPH}</script><style>{PARAGRAPH}</style></head><body></body></html>",
])
def test_scripts_and_tiny_body_are_not_cached_as_rendered_document(html):
    with pytest.raises(fetch_plugin.FetchUrlError, match="EMPTY_DOCUMENT"):
        fetch_plugin._validate_browser_document(BAIKE_URL, BAIKE_URL, 200, {}, html)


def test_rendered_challenge_is_an_error_not_another_upgrade():
    with pytest.raises(fetch_plugin.FetchUrlError, match="BROWSER_CHALLENGE"):
        fetch_plugin._validate_browser_document(KKBOX_URL, KKBOX_URL, 202, {"x-amzn-waf-action": "challenge"}, DOCUMENT)


def test_navigation_and_footer_paragraphs_do_not_hide_script_shell():
    html = "<html><body><div><h1>资料列表</h1><a>登录</a></div><div class='content'></div><footer><p>页脚说明文字，用于说明资料来源与版权信息。</p></footer><script src='/data.js'></script></body></html>"
    assert fetch_plugin._html_needs_rendering(html)


def test_short_real_body_is_not_overridden_by_noscript_hint():
    html = "<html><body><noscript>Please enable JavaScript</noscript><p>这是一段已经由服务器返回的有效正文，不需要执行脚本也能够读取。</p><script src='/analytics.js'></script></body></html>"
    assert not fetch_plugin._html_needs_rendering(html)


def test_browser_defaults_and_effective_configuration_are_offline():
    cfg = fetch_plugin.FetchUrlConfig()
    assert cfg.browser.enabled is False
    cfg.browser.enabled = True  # 旧配置不能重新启用已退役的站点分流。
    assert cfg.browser.timeout == 30
    assert cfg.browser.baike_proxy == "inherit"
    assert cfg.browser.kkbox_proxy == "direct"
    effective = fetch_plugin.resolve_effective_fetch_url_config(cfg)
    assert effective.browser_enabled is False
    assert effective.browser_timeout == 30
    assert effective.browser_baike_proxy == "inherit"
    assert effective.browser_kkbox_proxy == "direct"


@pytest.mark.parametrize(("url", "site"), [(BAIKE_URL, "baike"), (KKBOX_URL, "kkbox")])
def test_retired_target_sites_use_common_http_path(reader, monkeypatch, url, site):
    browser = AsyncMock(side_effect=AssertionError("Retired site browser must not run"))
    monkeypatch.setattr(reader, "_fetch_browser_document", browser)
    monkeypatch.setattr(reader, "_probe_url", AsyncMock(return_value={"kind": "text", "data": DOCUMENT.encode(), "encoding": "utf-8", "content_type": "text/html", "final_url": url}))
    result = asyncio.run(fetch(reader, url))
    assert result["success"] is True
    assert result["provider"] == "markdownify"
    assert result["final_url"] == url
    assert result["cached"] is False
    browser.assert_not_awaited()
    reader._probe_url.assert_awaited_once()
    reader._build_client.assert_called_once()
    reader._summarize.assert_not_awaited()


@pytest.mark.parametrize(("url", "enabled"), [(BAIKE_URL, False), ("https://example.invalid/article", True)])
def test_disabled_browser_or_other_site_keeps_existing_http_path(reader, monkeypatch, url, enabled):
    reader._browser_enabled = enabled
    monkeypatch.setattr(reader, "_probe_url", AsyncMock(return_value={"kind": "text", "data": DOCUMENT.encode(), "encoding": "utf-8", "content_type": "text/html", "final_url": url}))
    result = asyncio.run(fetch(reader, url))
    assert result["provider"] == "markdownify"
    reader._probe_url.assert_awaited_once()
    reader._fetch_browser_document.assert_not_awaited()


def test_http_original_markdown_cache_is_reused_across_summary_and_windows(reader, monkeypatch):
    reader._max_content_length = 48
    reader._llm_summarize = True
    monkeypatch.setattr(reader, "_summarize", AsyncMock(return_value="离线摘要"))
    markdown = fetch_plugin.FetchUrlPlugin._html_to_markdown_blocking(DOCUMENT, BAIKE_URL)

    async def run():
        first = await fetch(reader, on_exceed="summarize")
        second = await fetch(reader, start=90, end=130)
        return first, second

    first, second = asyncio.run(run())
    assert first["processed"] == "summarized"
    assert "离线摘要" in first["content"]
    assert second["cached"] is True
    assert second["provider"] == "markdownify"
    assert second["returned_range"] == [90, 130]
    assert second["total_chars"] == len(markdown)
    assert markdown[90:130] in second["content"]
    assert "离线摘要" not in second["content"]
    cached = reader._fetch_cache.get(reader._fetch_cache_key(BAIKE_URL))
    assert cached["markdown"] == markdown
    reader._fetch_browser_document.assert_not_awaited()
    reader._summarize.assert_awaited_once()
    reader._probe_url.assert_awaited_once()


@pytest.mark.parametrize(("url", "attribute", "value"), [
    (BAIKE_URL, "_jina_enabled", True),
    (BAIKE_URL, "_jina_engine", "auto"),
    (KKBOX_URL, "_allow_private_networks", True),
    (BAIKE_URL, "_proxy", "http://offline.invalid:7890"),
    (BAIKE_URL, "_user_agent", "OfflineReader/2.0"),
])
def test_fetch_cache_key_changes_with_reading_strategy(reader, monkeypatch, url, attribute, value):
    original = reader._fetch_cache_key(url)
    monkeypatch.setattr(reader, attribute, value)
    assert reader._fetch_cache_key(url) != original


def test_retired_browser_settings_do_not_change_http_cache(reader):
    async def run():
        first = await fetch(reader)
        reader._browser_baike_proxy = "direct"
        second = await fetch(reader)
        third = await fetch(reader)
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first["cached"] is False
    assert second["cached"] is True
    assert third["cached"] is True
    reader._fetch_browser_document.assert_not_awaited()
    reader._probe_url.assert_awaited_once()


def test_http_failure_is_not_cached_or_hidden_by_browser_retry(reader, monkeypatch):
    error = fetch_plugin.FetchUrlError("ACCESS_DENIED: offline HTTP rejection")
    probe = AsyncMock(side_effect=[error, {"kind": "text", "data": DOCUMENT.encode(), "encoding": "utf-8", "content_type": "text/html", "final_url": BAIKE_URL}])
    monkeypatch.setattr(reader, "_probe_url", probe)

    async def run():
        with pytest.raises(fetch_plugin.FetchUrlError, match="ACCESS_DENIED"):
            await fetch(reader)
        assert reader._fetch_cache.get(reader._fetch_cache_key(BAIKE_URL)) is None
        result = await fetch(reader)
        assert result["cached"] is False
        assert result["provider"] == "markdownify"

    asyncio.run(run())
    assert probe.await_count == 2
    reader._fetch_browser_document.assert_not_awaited()
    assert reader._build_client.call_count == 2


def test_cooldown_calls_do_not_extend_expiry_or_repeat_message(reader):
    expiry = fetch_plugin.time.monotonic() + 50
    reader._browser_cooldown["https://baike.baidu.com:443"] = (expiry, "ACCESS_DENIED: offline")

    async def run():
        for _ in range(2):
            with pytest.raises(fetch_plugin.FetchUrlError, match="ACCESS_DENIED"):
                await fetch_plugin.FetchUrlPlugin._fetch_browser_document(reader, BAIKE_URL)
            assert reader._browser_cooldown["https://baike.baidu.com:443"] == (expiry, "ACCESS_DENIED: offline")
            assert not reader._browser_lock.locked()
        assert "https://baike.baidu.com:443" not in reader._browser_last_request

    asyncio.run(run())


def test_failed_browser_cleanup_keeps_session_lock_until_teardown(reader, monkeypatch):
    from playwright.async_api import Error as PlaywrightError

    calls = []
    async def close_guard():
        assert reader._browser_lock.locked()
        calls.append("guard.close")
    async def close_context():
        assert reader._browser_lock.locked()
        calls.append("context.close")
    async def close_browser():
        assert reader._browser_lock.locked()
        calls.append("browser.close")
    page = SimpleNamespace(on=Mock(), goto=AsyncMock(side_effect=PlaywrightError("offline navigation failure")))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), route=AsyncMock(), close=AsyncMock(side_effect=close_context))
    browser = SimpleNamespace(close=AsyncMock(side_effect=close_browser))
    guard = SimpleNamespace(last_error="", close=AsyncMock(side_effect=close_guard))
    monkeypatch.setattr(reader, "_browser_context", AsyncMock(return_value=(browser, context, guard)))

    async def run():
        with pytest.raises(fetch_plugin.FetchUrlError, match="BROWSER_NETWORK_ERROR"):
            await fetch_plugin.FetchUrlPlugin._fetch_browser_document(reader, BAIKE_URL)
        assert calls == ["context.close", "browser.close", "guard.close"]
        assert not reader._browser_lock.locked()

    asyncio.run(run())


@pytest.mark.parametrize("authority", ["127.0.0.1:443", "[::1]:443", "localhost:443", "example.org:8080", "user@example.org:443", "example.org:443/path"])
def test_browser_guard_rejects_unsafe_authority_before_dns(monkeypatch, authority):
    guard = fetch_plugin._BrowserTunnelGuard()
    async def run():
        lookup = AsyncMock(side_effect=AssertionError("Unsafe authority must not be resolved"))
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
        with pytest.raises(fetch_plugin.FetchUrlError):
            await guard._target(authority)
        lookup.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("allow", "upstream", "accepted"), [
    (False, "", False), (True, "", True),
    (False, "http://127.0.0.1:7890", False),
    (True, "http://127.0.0.1:7890", True),
    (True, "http://localhost:7890", True),
    (True, "http://[::1]:7890", True),
    (True, "http://proxy.example:7890", False),
    (True, "http://192.168.1.1:7890", False),
])
def test_system_fake_ip_requires_existing_authorization_and_local_routing(monkeypatch, allow, upstream, accepted):
    guard = fetch_plugin._BrowserTunnelGuard(upstream, allow_system_fake_ip=allow)
    guard._upstream_options = guard._parse_upstream()

    async def run():
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("198.18.33.154", 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        if accepted:
            assert await guard._target("www.kkbox.com:443") == [(socket.AF_INET, "198.18.33.154")]
            assert guard.uses_system_fake_ip is True
        else:
            with pytest.raises(fetch_plugin.FetchUrlError, match="Fake-IP"):
                await guard._target("www.kkbox.com:443")

    asyncio.run(run())


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254"])
def test_fake_ip_authorization_does_not_allow_other_private_addresses(monkeypatch, address):
    guard = fetch_plugin._BrowserTunnelGuard(allow_system_fake_ip=True)

    async def run():
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        with pytest.raises(fetch_plugin.FetchUrlError, match="非公网"):
            await guard._target("www.kkbox.com:443")

    asyncio.run(run())


def test_mixed_fake_and_public_dns_is_rejected(monkeypatch):
    guard = fetch_plugin._BrowserTunnelGuard(allow_system_fake_ip=True)

    async def run():
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)) for address in ("198.18.33.154", "1.1.1.1")]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        with pytest.raises(fetch_plugin.FetchUrlError, match="混合"):
            await guard._target("www.kkbox.com:443")

    asyncio.run(run())
