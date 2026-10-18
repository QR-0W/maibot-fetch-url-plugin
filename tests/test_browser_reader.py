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
    monkeypatch.setattr(instance, "_probe_url", AsyncMock(side_effect=AssertionError("Browser targets must not be probed first")))
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


@pytest.mark.parametrize(("url", "site"), [
    (BAIKE_URL, "baike"),
    ("https://baike.baidu.com/item/%E7%A6%BB%E7%BA%BF/123456?from=offline", "baike"),
    (KKBOX_URL, "kkbox"),
    ("https://kkbox.com/tw/tc/song/offline-song-id", "kkbox"),
])
def test_browser_site_routes_only_supported_documents(url, site):
    assert fetch_plugin._browser_site_for_url(url) == site


@pytest.mark.parametrize("url", [
    "https://baike.baidu.com/",
    "https://baidu.com/item/offline/123456",
    "https://baike.baidu.com.evil.invalid/item/offline/123456",
    "https://baike.baidu.com@evil.invalid/item/offline/123456",
    "https://www.kkbox.com.evil.invalid/sg/sc/song/offline-song-id",
    "https://widget.kkbox.com/v1/?id=offline-song-id",
    "https://api.kkbox.com/sg/sc/song/offline-song-id",
    "https://www.kkbox.com/sg/sc/album/offline-id",
    "https://www.kkbox.com/sg/sc/song/",
    "https://baike.baidu.com/item/offline.pdf",
    "https://baike.baidu.com/item/offline.PNG",
    "https://www.kkbox.com/sg/sc/song/offline.pdf",
    "https://www.kkbox.com/sg/sc/song/offline.jpg?size=large",
    "https://127.0.0.1/item/offline/123456",
    "https://example.invalid/article",
])
def test_browser_site_rejects_media_and_lookalike_hosts(url):
    assert fetch_plugin._browser_site_for_url(url) is None


@pytest.mark.parametrize(("site", "url"), [
    ("baike", "https://baidu.com/resource.js"),
    ("baike", "https://baike.baidu.com/item/offline/123456"),
    ("baike", "https://img.bdimg.com/offline.png"),
    ("baike", "https://a.bdstatic.com/offline.js"),
    ("kkbox", "https://www.kkbox.com/offline"),
    ("kkbox", "https://kkbox.com.tw/offline"),
    ("kkbox", "https://cdn.kkbox.com.tw/offline.js"),
    ("kkbox", "https://a.kfs.io/offline.js"),
    ("kkbox", "https://awswaf.com/offline.js"),
    ("kkbox", "https://token.a.awswaf.com/offline.js"),
])
def test_browser_resource_allows_only_site_https_domains(site, url):
    assert fetch_plugin._browser_resource_allowed(site, url)


@pytest.mark.parametrize(("site", "url"), [
    ("baike", "http://baike.baidu.com/item/offline/123456"),
    ("kkbox", "http://www.kkbox.com/sg/sc/song/offline-song-id"),
    ("baike", "https://baidu.com.evil.invalid/script.js"),
    ("kkbox", "https://evil-kkbox.com/script.js"),
    ("kkbox", "https://awswaf.com.evil.invalid/script.js"),
    ("baike", "https://kkbox.com/script.js"),
    ("kkbox", "https://baidu.com/script.js"),
    ("baike", "https://127.0.0.1/script.js"),
    ("kkbox", "https://[::1]/script.js"),
    ("kkbox", "file:///tmp/offline.js"),
    ("baike", "data:text/html,offline"),
])
def test_browser_resource_blocks_non_https_foreign_domains_and_ips(site, url):
    assert not fetch_plugin._browser_resource_allowed(site, url)


@pytest.mark.parametrize(("status", "headers", "html", "code"), [
    (202, {"x-amzn-waf-action": "challenge"}, "<script>AwsWafIntegration</script>", "BROWSER_CHALLENGE"),
    (405, {"x-amzn-waf-action": "captcha"}, CAPTCHA, "CAPTCHA_REQUIRED"),
    (405, {"X-Amzn-Waf-Action": "CAPTCHA"}, CAPTCHA, "CAPTCHA_REQUIRED"),
    (403, {}, "<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>", "ACCESS_DENIED"),
    (200, {}, CAPTCHA, "CAPTCHA_REQUIRED"),
])
def test_browser_access_error_classifies_rejection_instead_of_returning_content(status, headers, html, code):
    assert code in fetch_plugin._browser_access_error(status, headers, html)


def test_browser_access_error_accepts_normal_article():
    assert fetch_plugin._browser_access_error(200, {"content-type": "text/html"}, DOCUMENT) == ""


@pytest.mark.parametrize("url", [BAIKE_URL, KKBOX_URL])
def test_validate_browser_document_accepts_real_visible_body(url):
    assert fetch_plugin._validate_browser_document(url, url, 200, {}, DOCUMENT) is None


@pytest.mark.parametrize(("requested", "final"), [
    (BAIKE_URL, "https://baike.baidu.com/item/offline-document/999999"),
    (KKBOX_URL, "https://www.kkbox.com/sg/sc/song/different-song-id"),
    (KKBOX_URL, "https://widget.kkbox.com/v1/?id=offline-song-id"),
    (BAIKE_URL, "https://baike.baidu.com.evil.invalid/item/offline-document/123456"),
])
def test_validate_browser_document_rejects_changed_document_identity(requested, final):
    with pytest.raises(fetch_plugin.FetchUrlError):
        fetch_plugin._validate_browser_document(requested, final, 200, {}, DOCUMENT)


@pytest.mark.parametrize("heading", ["百度百科", "KKBOX", "Human Verification", "安全验证", ""])
def test_validate_browser_document_rejects_generic_or_verification_heading(heading):
    html = f"<html><body><h1>{heading}</h1><p>{PARAGRAPH}</p></body></html>"
    with pytest.raises(fetch_plugin.FetchUrlError):
        fetch_plugin._validate_browser_document(BAIKE_URL, BAIKE_URL, 200, {}, html)


@pytest.mark.parametrize("html", [
    "<html><body><h1>离线文档</h1><p>正文过短。</p></body></html>",
    f"<html><body><h1>离线文档</h1><script>{PARAGRAPH}</script><style>{PARAGRAPH}</style></body></html>",
], ids=["short_visible_body", "scripts_and_styles_only"])
def test_validate_browser_document_does_not_count_scripts_or_short_body(html):
    with pytest.raises(fetch_plugin.FetchUrlError):
        fetch_plugin._validate_browser_document(BAIKE_URL, BAIKE_URL, 200, {}, html)


def test_validate_browser_document_rejects_challenge_even_with_article_sized_html():
    with pytest.raises(fetch_plugin.FetchUrlError, match="BROWSER_CHALLENGE"):
        fetch_plugin._validate_browser_document(KKBOX_URL, KKBOX_URL, 202, {"x-amzn-waf-action": "challenge"}, DOCUMENT)


def test_browser_defaults_and_effective_configuration_are_offline():
    cfg = fetch_plugin.FetchUrlConfig()
    assert cfg.browser.enabled is True
    assert cfg.browser.timeout == 30
    assert cfg.browser.baike_proxy == "inherit"
    assert cfg.browser.kkbox_proxy == "direct"
    effective = fetch_plugin.resolve_effective_fetch_url_config(cfg)
    assert effective.browser_enabled is True
    assert effective.browser_timeout == 30
    assert effective.browser_baike_proxy == "inherit"
    assert effective.browser_kkbox_proxy == "direct"


@pytest.mark.parametrize(("url", "site"), [(BAIKE_URL, "baike"), (KKBOX_URL, "kkbox")])
def test_target_site_goes_to_browser_without_original_http_probe(reader, monkeypatch, url, site):
    async def document(url, site):
        assert (url, site) == expected
        return DOCUMENT, url, {"content_type": "text/html", "title": "离线文档"}

    expected = (url, site)
    browser = AsyncMock(side_effect=document)
    monkeypatch.setattr(reader, "_fetch_browser_document", browser)
    result = asyncio.run(fetch(reader, url))
    assert result["success"] is True
    assert result["provider"] == "browser"
    assert result["final_url"] == url
    assert result["cached"] is False
    browser.assert_awaited_once()
    reader._probe_url.assert_not_awaited()
    reader._build_client.assert_not_called()
    reader._summarize.assert_not_awaited()


@pytest.mark.parametrize(("url", "enabled"), [(BAIKE_URL, False), ("https://example.invalid/article", True)])
def test_disabled_browser_or_other_site_keeps_existing_http_path(reader, monkeypatch, url, enabled):
    reader._browser_enabled = enabled
    monkeypatch.setattr(reader, "_probe_url", AsyncMock(return_value={"kind": "text", "data": DOCUMENT.encode(), "encoding": "utf-8", "content_type": "text/html", "final_url": url}))
    result = asyncio.run(fetch(reader, url))
    assert result["provider"] == "markdownify"
    reader._probe_url.assert_awaited_once()
    reader._fetch_browser_document.assert_not_awaited()


def test_browser_original_markdown_cache_is_reused_across_summary_and_windows(reader, monkeypatch):
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
    assert second["provider"] == "browser"
    assert second["returned_range"] == [90, 130]
    assert second["total_chars"] == len(markdown)
    assert markdown[90:130] in second["content"]
    assert "离线摘要" not in second["content"]
    cached = reader._fetch_cache.get(reader._fetch_cache_key(BAIKE_URL))
    assert cached["markdown"] == markdown
    reader._fetch_browser_document.assert_awaited_once()
    reader._summarize.assert_awaited_once()
    reader._probe_url.assert_not_awaited()


@pytest.mark.parametrize(("url", "attribute", "value"), [
    (BAIKE_URL, "_browser_enabled", False),
    (BAIKE_URL, "_browser_baike_proxy", "direct"),
    (KKBOX_URL, "_browser_kkbox_proxy", "inherit"),
    (BAIKE_URL, "_proxy", "http://offline.invalid:7890"),
    (BAIKE_URL, "_user_agent", "OfflineReader/2.0"),
])
def test_fetch_cache_key_changes_with_reading_strategy(reader, monkeypatch, url, attribute, value):
    original = reader._fetch_cache_key(url)
    monkeypatch.setattr(reader, attribute, value)
    assert reader._fetch_cache_key(url) != original


def test_new_browser_route_does_not_reuse_previous_route_cache(reader):
    async def run():
        first = await fetch(reader)
        reader._browser_baike_proxy = "direct"
        second = await fetch(reader)
        third = await fetch(reader)
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first["cached"] is False
    assert second["cached"] is False
    assert third["cached"] is True
    assert reader._fetch_browser_document.await_count == 2
    reader._probe_url.assert_not_awaited()


def test_browser_challenge_is_not_cached_or_hidden_by_plain_http_retry(reader, monkeypatch):
    error = fetch_plugin.FetchUrlError("CAPTCHA_REQUIRED: offline human verification")
    browser = AsyncMock(side_effect=[error, (DOCUMENT, BAIKE_URL, {"content_type": "text/html"})])
    monkeypatch.setattr(reader, "_fetch_browser_document", browser)

    async def run():
        with pytest.raises(fetch_plugin.FetchUrlError, match="CAPTCHA_REQUIRED"):
            await fetch(reader)
        assert reader._fetch_cache.get(reader._fetch_cache_key(BAIKE_URL)) is None
        result = await fetch(reader)
        assert result["cached"] is False
        assert result["provider"] == "browser"

    asyncio.run(run())
    assert browser.await_count == 2
    reader._probe_url.assert_not_awaited()
    reader._build_client.assert_not_called()


def test_cooldown_calls_do_not_extend_expiry_or_repeat_message(reader):
    expiry = fetch_plugin.time.monotonic() + 50
    reader._browser_cooldown["baike"] = (expiry, "ACCESS_DENIED: offline")

    async def run():
        for _ in range(2):
            with pytest.raises(fetch_plugin.FetchUrlError, match="ACCESS_DENIED"):
                await fetch_plugin.FetchUrlPlugin._fetch_browser_document(reader, BAIKE_URL, "baike")
            assert reader._browser_cooldown["baike"] == (expiry, "ACCESS_DENIED: offline")
            assert not reader._browser_lock.locked()
        assert "baike" not in reader._browser_last_request

    asyncio.run(run())


def test_failed_browser_cleanup_keeps_session_lock_until_teardown(reader, monkeypatch):
    from playwright.async_api import Error as PlaywrightError

    calls = []
    async def close_page():
        assert reader._browser_lock.locked()
        calls.append("page.close")
    async def unroute(*args):
        assert reader._browser_lock.locked()
        calls.append("context.unroute")
    page = SimpleNamespace(on=Mock(), goto=AsyncMock(side_effect=PlaywrightError("offline navigation failure")), is_closed=Mock(return_value=False), close=AsyncMock(side_effect=close_page))
    network = SimpleNamespace(send=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), new_cdp_session=AsyncMock(return_value=network), route=AsyncMock(), unroute=AsyncMock(side_effect=unroute))
    monkeypatch.setattr(reader, "_browser_context", AsyncMock(return_value=context))

    async def run():
        with pytest.raises(fetch_plugin.FetchUrlError, match="浏览器读取失败"):
            await fetch_plugin.FetchUrlPlugin._fetch_browser_document(reader, BAIKE_URL, "baike")
        assert calls == ["page.close", "context.unroute"]
        assert not reader._browser_lock.locked()

    asyncio.run(run())


def test_browser_native_url_policy_uses_structured_hosts_and_deny_default():
    patterns = fetch_plugin._browser_network_patterns("baike")
    assert patterns[-1] == {"urlPattern": "*://*:*/*", "block": True}
    assert all(item["urlPattern"].startswith("https://") for item in patterns[:-1])
    assert {"urlPattern": "https://baidu.com/*", "block": False} in patterns
    assert {"urlPattern": "https://*.baidu.com/*", "block": False} in patterns


@pytest.mark.parametrize(("allow", "upstream", "accepted"), [(False, "", False), (True, "", True), (True, "http://127.0.0.1:7890", False)])
def test_system_fake_ip_requires_existing_authorization_and_no_upstream(monkeypatch, allow, upstream, accepted):
    guard = fetch_plugin._BrowserTunnelGuard("kkbox", upstream, allow_system_fake_ip=allow)
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
    guard = fetch_plugin._BrowserTunnelGuard("kkbox", allow_system_fake_ip=True)

    async def run():
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        with pytest.raises(fetch_plugin.FetchUrlError, match="非公网"):
            await guard._target("www.kkbox.com:443")

    asyncio.run(run())


def test_mixed_fake_and_public_dns_is_rejected(monkeypatch):
    guard = fetch_plugin._BrowserTunnelGuard("kkbox", allow_system_fake_ip=True)

    async def run():
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)) for address in ("198.18.33.154", "1.1.1.1")]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answer))
        with pytest.raises(fetch_plugin.FetchUrlError, match="混合"):
            await guard._target("www.kkbox.com:443")

    asyncio.run(run())
