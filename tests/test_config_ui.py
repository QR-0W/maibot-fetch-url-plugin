"""Offline UI layout contracts; no runtime configuration or network access."""

from pathlib import Path

import socket
import sys

import pytest
from maibot_sdk.config import generate_plugin_config_schema

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import plugin as fetch_plugin


@pytest.fixture
def schemas(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Schema generation must not access the network")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    source = generate_plugin_config_schema(fetch_plugin.FetchUrlConfig)
    merged = fetch_plugin.create_plugin().get_webui_config_schema()
    return source, merged


def test_four_cards_and_functional_groups(schemas):
    _, merged = schemas
    sections = merged["sections"]
    assert [section["title"] for section in sections.values()] == [
        "工具开关与网站连接", "网页读取与正文处理", "图片返回与内容描述", "读取结果与图片描述缓存",
    ]
    assert [section["order"] for section in sections.values()] == list(range(4))
    assert sections["webpage"]["fields"]["jina_timeout"]["group"] == "网页正文读取（Jina Reader）"
    assert not any(field.get("config_section") == "browser" for section in sections.values() for field in section["fields"].values())
    for name in ("proxy", "timeout", "max_download_size", "user_agent", "allow_private_networks"):
        assert sections["access"]["fields"][f"fetch_{name}"]["group"] == "连接"
    assert sections["caches"]["fields"]["alt_text_cache_size"]["group"] == "图片描述缓存（持久化）"


def test_every_original_field_keeps_its_path_and_metadata(schemas):
    source, merged = schemas
    expected = {
        (section_name, field["name"]): field
        for section_name, section in source["sections"].items()
        if section_name != "browser"
        for field in section["fields"].values()
    }
    actual = {}
    for section in merged["sections"].values():
        for key, field in section["fields"].items():
            path = (field["config_section"], field["name"])
            assert path not in actual
            assert key == f"{path[0]}_{path[1]}"
            actual[path] = field
    assert actual.keys() == expected.keys()
    for path, original in expected.items():
        for key, value in original.items():
            if path == ("fetch", "domain_cookies") and key in {"ui_type", "item_fields"}:
                continue
            if path == ("alt_text", "prompt") and key in {"ui_type", "rows", "placeholder"}:
                continue
            if key not in {"group", "order", "hidden", "label", "hint"}:
                assert actual[path][key] == value, (path, key)
        assert actual[path]["hidden"] == (original["hidden"] or path == ("plugin", "config_version"))


def test_visible_fields_have_chinese_names_and_help(schemas):
    _, merged = schemas
    for section in merged["sections"].values():
        assert section["description"]
        for field in section["fields"].values():
            if field["hidden"]:
                continue
            assert any("\u4e00" <= char <= "\u9fff" for char in field["label"]), field["name"]
            assert field["hint"], field["name"]
    fields = merged["sections"]["access"]["fields"]
    assert fields["fetch_proxy"]["label"] == "代理"
    assert "Jina" in fields["fetch_domain_cookies"]["hint"]


def test_domain_cookies_uses_masked_key_value_editor(schemas):
    source, merged = schemas
    field = merged["sections"]["access"]["fields"]["fetch_domain_cookies"]
    assert field["type"] == "object"
    assert field["ui_type"] == "key_value"
    assert field["config_section"] == "fetch"
    assert field["name"] == "domain_cookies"
    assert field["default"] == source["sections"]["fetch"]["fields"]["domain_cookies"]["default"]
    assert field["item_fields"]["key"]["label"] == "域名"
    assert field["item_fields"]["value"]["type"] == "password"


def test_image_description_stays_disabled_and_default_prompt_is_visible(schemas):
    _, merged = schemas
    config = fetch_plugin.FetchUrlConfig()
    effective = fetch_plugin.resolve_effective_fetch_url_config(config)
    assert effective.alt_max_images == 0
    assert config.alt_text.prompt == ""
    assert effective.alt_prompt == fetch_plugin.DEFAULT_ALT_TEXT_PROMPT
    prompt = merged["sections"]["images"]["fields"]["alt_text_prompt"]
    assert prompt["ui_type"] == "textarea"
    assert prompt["placeholder"] == fetch_plugin.DEFAULT_ALT_TEXT_PROMPT
    assert prompt["default"] == ""


def test_nested_vlm_image_remains_one_original_object(schemas):
    source, merged = schemas
    field = merged["sections"]["images"]["fields"]["alt_text_image"]
    original = source["sections"]["alt_text"]["fields"]["image"]
    assert field["name"] == "image"
    assert field["config_section"] == "alt_text"
    assert field["type"] == original["type"] == "object"
    assert field["ui_type"] == original["ui_type"]
    assert field["default"] == original["default"]
    assert "alt_text_cache_size" not in merged["sections"]["images"]["fields"]


def test_cross_section_write_paths_preserve_synthetic_config(schemas):
    _, merged = schemas
    config = fetch_plugin.FetchUrlConfig().model_dump(mode="python")
    updates = {
        ("access", "fetch_timeout"): 18.0,
        ("webpage", "jina_timeout"): 24.0,
        ("caches", "alt_text_cache_size"): 25,
        ("images", "alt_text_image"): {"max_dimension": 1024},
    }
    for (card, key), value in updates.items():
        field = merged["sections"][card]["fields"][key]
        config[field["config_section"]][field["name"]] = value
    validated = fetch_plugin.FetchUrlConfig.model_validate(config)
    assert validated.fetch.timeout == 18.0
    assert validated.jina.timeout == 24.0
    assert validated.alt_text.cache_size == 25
    assert validated.alt_text.image.max_dimension == 1024
    assert not {"access", "webpage", "images", "caches"}.intersection(config)
