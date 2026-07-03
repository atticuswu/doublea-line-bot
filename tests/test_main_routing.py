"""webhook 路由測試：功能開關 × 群組綁定。"""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "testsecret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "testtoken")

from linebot.v3.webhooks import MessageEvent

import main


def _fake_event(chat_id: str, user_id: str, text: str | None = None, is_image: bool = False):
    """建立最小可用的 MessageEvent 替身（真 subclass，通過 isinstance）。"""
    from linebot.v3.webhooks import TextMessageContent, ImageMessageContent

    ev = object.__new__(MessageEvent)
    source = MagicMock()
    source.user_id = user_id
    source.group_id = chat_id if chat_id.startswith("C") else None
    source.room_id = chat_id if chat_id.startswith("R") else None
    object.__setattr__(ev, "source", source)
    object.__setattr__(ev, "reply_token", "rtoken")
    if is_image:
        msg = object.__new__(ImageMessageContent)
        object.__setattr__(msg, "id", "imgmsg1")
    else:
        msg = object.__new__(TextMessageContent)
        object.__setattr__(msg, "text", text or "hi")
    object.__setattr__(ev, "message", msg)
    return ev


@pytest.fixture
def route(monkeypatch):
    """直接測 main._route_event，繞過簽章驗證。"""
    monkeypatch.setattr(main.bot_config, "register_chat", MagicMock())
    return main._route_event


def test_mom_message_triggers_photo(route):
    ev = _fake_event("Rfam", "Umom")
    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(main.bot_config, "is_feature_on", return_value=False),
        patch.object(main, "_dispatch_mom") as dm,
    ):
        action = route(ev)
    assert action == ("mom_photo", ev.reply_token)


def test_archive_image_in_bound_group(route):
    ev = _fake_event("Ctrip", "Uany", is_image=True)
    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(
            main.bot_config, "is_feature_on",
            side_effect=lambda f, c: f == "photo_archive" and c == "Ctrip",
        ),
    ):
        action = route(ev)
    assert action == ("archive_photo", "imgmsg1")


def test_album_command_in_bound_group(route):
    ev = _fake_event("Ctrip", "Uany", text="相簿 薄荷島")
    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(
            main.bot_config, "is_feature_on",
            side_effect=lambda f, c: f == "photo_archive" and c == "Ctrip",
        ),
    ):
        action = route(ev)
    assert action == ("album_command", "相簿 薄荷島", "rtoken")


def test_todo_text_in_bound_group(route):
    ev = _fake_event("Cdoublea", "Uatticus", text="明天開會")
    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(
            main.bot_config, "is_feature_on",
            side_effect=lambda f, c: f == "todo" and c == "Cdoublea",
        ),
    ):
        action = route(ev)
    assert action == ("todo", "明天開會", "Cdoublea", "rtoken")


def test_unbound_group_ignored(route):
    ev = _fake_event("Cstranger", "Uany", text="hello")
    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(main.bot_config, "is_feature_on", return_value=False),
    ):
        action = route(ev)
    assert action is None
