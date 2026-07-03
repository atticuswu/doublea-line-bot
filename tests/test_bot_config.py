import json
import os
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    """本機 JSON 模式：無 K_SERVICE，config 存 tmp 檔。"""
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr("bot_config.LOCAL_CONFIG_PATH", str(tmp_path / "config.json"))
    import bot_config
    bot_config.invalidate_cache()
    return bot_config


def test_load_config_returns_default_when_empty(cfg_env):
    cfg = cfg_env.load_config()
    assert "features" in cfg
    assert "known_chats" in cfg
    assert cfg["features"]["todo"]["enabled"] is True


def test_save_and_load_roundtrip(cfg_env):
    cfg = cfg_env.load_config()
    cfg["features"]["todo"]["chat_ids"] = ["Cabc"]
    cfg_env.save_config(cfg)
    cfg_env.invalidate_cache()
    assert cfg_env.load_config()["features"]["todo"]["chat_ids"] == ["Cabc"]


def test_cache_avoids_reread_within_ttl(cfg_env):
    cfg_env.load_config()
    with patch.object(cfg_env, "_read_config") as m:
        cfg_env.load_config()
        m.assert_not_called()


def test_is_feature_on(cfg_env):
    cfg = cfg_env.load_config()
    cfg["features"]["photo_archive"] = {"enabled": True, "chat_ids": ["Cxyz"]}
    cfg_env.save_config(cfg)
    assert cfg_env.is_feature_on("photo_archive", "Cxyz") is True
    assert cfg_env.is_feature_on("photo_archive", "Cother") is False
    cfg["features"]["photo_archive"]["enabled"] = False
    cfg_env.save_config(cfg)
    assert cfg_env.is_feature_on("photo_archive", "Cxyz") is False


def test_is_feature_on_unknown_feature_is_false(cfg_env):
    assert cfg_env.is_feature_on("nonexistent", "Cxyz") is False


def test_register_chat_new_and_no_overwrite(cfg_env):
    cfg_env.register_chat("Cnew", "group", "旅遊團")
    chats = cfg_env.load_config()["known_chats"]
    assert chats["Cnew"] == {"name": "旅遊團", "type": "group", "note": ""}
    # 已存在：不覆寫（使用者可能已編輯備註）
    cfg = cfg_env.load_config()
    cfg["known_chats"]["Cnew"]["note"] = "手動備註"
    cfg_env.save_config(cfg)
    cfg_env.register_chat("Cnew", "group", "改名了")
    assert cfg_env.load_config()["known_chats"]["Cnew"]["note"] == "手動備註"
    assert cfg_env.load_config()["known_chats"]["Cnew"]["name"] == "旅遊團"


def test_get_todo_chat_id(cfg_env):
    assert cfg_env.get_todo_chat_id() is None
    cfg = cfg_env.load_config()
    cfg["features"]["todo"]["chat_ids"] = ["Cfirst", "Csecond"]
    cfg_env.save_config(cfg)
    assert cfg_env.get_todo_chat_id() == "Cfirst"


def test_get_mom_user_id(cfg_env):
    cfg = cfg_env.load_config()
    cfg["mom_user_id"] = "Umom"
    cfg_env.save_config(cfg)
    assert cfg_env.get_mom_user_id() == "Umom"
