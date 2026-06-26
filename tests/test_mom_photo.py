import os
import time
from unittest.mock import MagicMock, patch

import pytest


def test_is_mom_match(monkeypatch):
    monkeypatch.setenv("MOM_USER_ID", "Uabc123")
    import importlib, mom_photo
    importlib.reload(mom_photo)
    assert mom_photo.is_mom("Uabc123") is True


def test_is_mom_no_match(monkeypatch):
    monkeypatch.setenv("MOM_USER_ID", "Uabc123")
    import importlib, mom_photo
    importlib.reload(mom_photo)
    assert mom_photo.is_mom("Uother") is False


def test_is_mom_empty_env(monkeypatch):
    monkeypatch.setenv("MOM_USER_ID", "")
    import importlib, mom_photo
    importlib.reload(mom_photo)
    assert mom_photo.is_mom("Uabc123") is False


def test_cooldown_initial_not_on_cooldown(monkeypatch):
    import importlib, mom_photo
    importlib.reload(mom_photo)
    monkeypatch.setattr(mom_photo, "_last_sent_at", 0.0)
    assert mom_photo.is_on_cooldown() is False


def test_cooldown_just_sent(monkeypatch):
    import importlib, mom_photo
    importlib.reload(mom_photo)
    monkeypatch.setattr(mom_photo, "MOM_PHOTO_COOLDOWN_SEC", 3600)
    monkeypatch.setattr(mom_photo, "_last_sent_at", time.time())
    assert mom_photo.is_on_cooldown() is True


def test_mark_sent_updates_timestamp(monkeypatch):
    import importlib, mom_photo
    importlib.reload(mom_photo)
    monkeypatch.setattr(mom_photo, "_last_sent_at", 0.0)
    before = time.time()
    mom_photo.mark_sent()
    assert mom_photo._last_sent_at >= before


def test_get_next_photo_returns_file_id(monkeypatch):
    """Drive 回傳 3 張照片，get_next_photo_file_id 應循序（洗牌後）不重複回傳 file_id。"""
    import importlib, mom_photo
    importlib.reload(mom_photo)

    fake_files = [
        {"id": "file1", "name": "a.jpg", "mimeType": "image/jpeg"},
        {"id": "file2", "name": "b.png", "mimeType": "image/png"},
        {"id": "file3", "name": "c.jpg", "mimeType": "image/jpeg"},
    ]

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": fake_files}

    with patch("mom_photo.build_drive_service", return_value=mock_service):
        with patch("mom_photo._load_carousel_state", return_value={}):
            with patch("mom_photo._save_carousel_state"):
                ids = set()
                for _ in range(3):
                    fid = mom_photo.get_next_photo_file_id(MagicMock(), "folder123")
                    ids.add(fid)
                assert len(ids) == 3


def test_get_next_photo_reshuffles_after_full_cycle(monkeypatch):
    """跑完一輪後重新洗牌，不會停在最後一張。"""
    import importlib, mom_photo
    importlib.reload(mom_photo)

    fake_files = [
        {"id": "file1", "name": "a.jpg", "mimeType": "image/jpeg"},
        {"id": "file2", "name": "b.jpg", "mimeType": "image/jpeg"},
    ]

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": fake_files}

    states_saved = []
    with patch("mom_photo.build_drive_service", return_value=mock_service):
        with patch("mom_photo._load_carousel_state", return_value={}):
            with patch("mom_photo._save_carousel_state", side_effect=lambda s: states_saved.append(s)):
                for _ in range(4):
                    mom_photo.get_next_photo_file_id(MagicMock(), "folder123")
    assert len(states_saved) == 4


def test_handle_mom_message_sends_reply(monkeypatch):
    import importlib, mom_photo
    importlib.reload(mom_photo)

    monkeypatch.setenv("MOM_USER_ID", "Umom")
    monkeypatch.setenv("DAUGHTER_PHOTOS_FOLDER_ID", "folder123")
    monkeypatch.setenv("DOUBLEA_PUBLIC_URL", "https://bot.example.com")
    importlib.reload(mom_photo)

    monkeypatch.setattr(mom_photo, "_last_sent_at", 0.0)
    monkeypatch.setattr(mom_photo, "get_next_photo_file_id", lambda creds, fid: "file_abc")

    mock_line_api = MagicMock()
    with patch("mom_photo.get_credentials", return_value=MagicMock()):
        result = mom_photo.handle_mom_message("reply_token_xyz", mock_line_api)

    assert result is True
    mock_line_api.reply_message.assert_called_once()


def test_handle_mom_message_skips_on_cooldown(monkeypatch):
    import importlib, mom_photo
    importlib.reload(mom_photo)

    monkeypatch.setenv("MOM_USER_ID", "Umom")
    monkeypatch.setenv("DOUBLEA_PUBLIC_URL", "https://bot.example.com")
    importlib.reload(mom_photo)

    monkeypatch.setattr(mom_photo, "MOM_PHOTO_COOLDOWN_SEC", 3600)
    monkeypatch.setattr(mom_photo, "_last_sent_at", time.time())

    mock_line_api = MagicMock()
    result = mom_photo.handle_mom_message("reply_token_xyz", mock_line_api)

    assert result is True
    mock_line_api.reply_message.assert_not_called()
