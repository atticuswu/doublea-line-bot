import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def pa(tmp_path, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(
        "photo_archive.LOCAL_STATE_PATH", str(tmp_path / "state.json")
    )
    import photo_archive
    return photo_archive


def test_make_filename(pa):
    from datetime import datetime
    now = datetime(2026, 7, 3, 14, 30, 5)
    assert pa._make_filename("msg123", now) == "20260703-143005_msg123.jpg"


def test_album_command_parses_location(pa):
    line_api = MagicMock()
    with patch.object(pa, "_start_event") as start:
        assert pa.handle_album_command("相簿 菲律賓薄荷島", "rtoken", line_api) is True
        start.assert_called_once_with("菲律賓薄荷島")
    line_api.reply_message.assert_called_once()


def test_album_command_ignores_other_text(pa):
    line_api = MagicMock()
    assert pa.handle_album_command("午餐吃什麼", "rtoken", line_api) is False
    line_api.reply_message.assert_not_called()


def test_target_folder_monthly_by_default(pa):
    service = MagicMock()
    with patch.object(pa, "_ensure_folder", side_effect=["root_id", "month_id"]) as ef:
        folder_id = pa._get_target_folder(service)
    assert folder_id == "month_id"
    # 第二次呼叫是月份資料夾，parent 是 root
    assert ef.call_args_list[1].args[2] == "root_id"


def test_target_folder_event_mode_active(pa):
    pa._save_state({
        "mode": "event", "event_folder": "2026-07_薄荷島",
        "event_folder_id": "evt_id", "last_photo_at": time.time(),
    })
    service = MagicMock()
    assert pa._get_target_folder(service) == "evt_id"


def test_target_folder_event_mode_expired(pa):
    pa._save_state({
        "mode": "event", "event_folder": "2026-07_薄荷島",
        "event_folder_id": "evt_id",
        "last_photo_at": time.time() - pa.EVENT_TIMEOUT_SEC - 1,
    })
    service = MagicMock()
    with patch.object(pa, "_ensure_folder", side_effect=["root_id", "month_id"]):
        assert pa._get_target_folder(service) == "month_id"
    assert pa._load_state()["mode"] == "monthly"


def test_archive_photo_success(pa):
    with (
        patch.object(pa, "_download_line_content", return_value=b"jpegbytes") as dl,
        patch.object(pa, "get_credentials"),
        patch.object(pa, "build_drive_service"),
        patch.object(pa, "_get_target_folder", return_value="folder_id"),
        patch.object(pa, "_upload_to_drive") as up,
    ):
        assert pa.archive_photo("msg789") is True
        dl.assert_called_once_with("msg789")
        up.assert_called_once()
        # 上傳後更新 last_photo_at
        assert pa._load_state()["last_photo_at"] > 0


def test_archive_photo_upload_retries_then_fails(pa):
    with (
        patch.object(pa, "_download_line_content", return_value=b"x"),
        patch.object(pa, "get_credentials"),
        patch.object(pa, "build_drive_service"),
        patch.object(pa, "_get_target_folder", return_value="fid"),
        patch.object(pa, "_upload_to_drive", side_effect=RuntimeError("boom")) as up,
        patch.object(pa.time, "sleep"),
    ):
        assert pa.archive_photo("msg789") is False
        assert up.call_count == 3


def test_ensure_folder_escapes_quotes(pa):
    """Verify _ensure_folder escapes single quotes in folder name."""
    service = MagicMock()
    service.files().list().execute.return_value = {"files": []}
    service.files().create().execute.return_value = {"id": "new_folder_id"}

    result = pa._ensure_folder(service, "Tom's Island", "parent_id_123")

    # Check that files().list() was called with escaped query
    call_args = service.files().list.call_args
    q_param = call_args.kwargs["q"]
    assert "Tom\\'s Island" in q_param, f"Expected escaped quote in query: {q_param}"
    assert "parent_id_123" in q_param

    # Verify creation was called for the non-existent folder
    assert service.files().create.called
    assert result == "new_folder_id"


def test_root_folder_uses_env_id(pa, monkeypatch):
    monkeypatch.setattr(pa, "ARCHIVE_ROOT_FOLDER_ID", "user_root_id")
    service = MagicMock()
    with patch.object(pa, "_ensure_folder") as ef:
        assert pa._root_folder(service) == "user_root_id"
        ef.assert_not_called()


def test_parse_album_command_with_quotes(pa):
    assert pa.parse_album_command("「相簿 202606台東遊」") == "202606台東遊"
    assert pa.parse_album_command("相簿 薄荷島") == "薄荷島"
    assert pa.parse_album_command('"相簿 Tokyo"') == "Tokyo"
    assert pa.parse_album_command("午餐吃什麼") is None
    assert pa.parse_album_command("相簿 ") is None


def test_archive_photo_replies_on_last_of_set(pa):
    with (
        patch.object(pa, "_download_line_content", return_value=b"x"),
        patch.object(pa, "get_credentials"),
        patch.object(pa, "build_drive_service"),
        patch.object(pa, "_get_target_folder", return_value="fid"),
        patch.object(pa, "_upload_to_drive"),
        patch.object(pa, "_current_folder_label", return_value="2026-07_台東遊"),
        patch.object(pa, "_reply_text") as reply,
    ):
        assert pa.archive_photo("msg1", reply_token="rt", announce_total=3) is True
        reply.assert_called_once()
        text = reply.call_args.args[1]
        assert "3 張" in text and "2026-07_台東遊" in text


def test_archive_photo_retries_credential_failure(pa):
    """取憑證的暫時性錯誤也要重試（過去在重試圈外導致照片遺失）。"""
    with (
        patch.object(pa, "_download_line_content", return_value=b"x"),
        patch.object(pa, "get_credentials", side_effect=[RuntimeError("ssl eof"), MagicMock()]),
        patch.object(pa, "build_drive_service"),
        patch.object(pa, "_get_target_folder", return_value="fid"),
        patch.object(pa, "_upload_to_drive"),
        patch.object(pa.time, "sleep"),
    ):
        assert pa.archive_photo("msg1") is True
