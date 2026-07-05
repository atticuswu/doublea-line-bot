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
    # 單張照片（無 image_set）→ 帶 reply_token、總數 1
    assert action == ("archive_photo", "imgmsg1", "rtoken", 1)


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


def test_register_chat_fetches_group_name(monkeypatch):
    monkeypatch.setattr(
        main.bot_config, "load_config", lambda: {"known_chats": {}}
    )
    register_mock = MagicMock()
    monkeypatch.setattr(main.bot_config, "register_chat", register_mock)

    summary = MagicMock()
    summary.group_name = "旅遊團"
    api_instance = MagicMock()
    api_instance.get_group_summary.return_value = summary
    fake_messaging_api_cls = MagicMock(return_value=api_instance)
    monkeypatch.setattr(main, "MessagingApi", fake_messaging_api_cls)

    main._register_chat("Cnew", "group")

    register_mock.assert_called_once_with("Cnew", "group", "旅遊團")
    api_instance.get_group_summary.assert_called_once_with("Cnew")


def test_register_chat_room_skips_api(monkeypatch):
    monkeypatch.setattr(
        main.bot_config, "load_config", lambda: {"known_chats": {}}
    )
    register_mock = MagicMock()
    monkeypatch.setattr(main.bot_config, "register_chat", register_mock)

    fake_messaging_api_cls = MagicMock()
    monkeypatch.setattr(main, "MessagingApi", fake_messaging_api_cls)

    main._register_chat("Rroom", "room")

    fake_messaging_api_cls.assert_not_called()
    register_mock.assert_called_once_with("Rroom", "room", "")


def test_archive_image_set_only_last_gets_reply_token(route):
    """一組照片只有最後一張帶 reply_token。"""
    class ImgSet:
        def __init__(self, index, total):
            self.index = index
            self.total = total

    ev_mid = _fake_event("Ctrip", "Uany", is_image=True)
    object.__setattr__(ev_mid.message, "image_set", ImgSet(1, 3))
    ev_last = _fake_event("Ctrip", "Uany", is_image=True)
    object.__setattr__(ev_last.message, "image_set", ImgSet(3, 3))

    with (
        patch.object(main.bot_config, "get_mom_user_id", return_value="Umom"),
        patch.object(
            main.bot_config, "is_feature_on",
            side_effect=lambda f, c: f == "photo_archive" and c == "Ctrip",
        ),
    ):
        assert route(ev_mid) == ("archive_photo", "imgmsg1", "", 3)
        assert route(ev_last) == ("archive_photo", "imgmsg1", "rtoken", 3)
