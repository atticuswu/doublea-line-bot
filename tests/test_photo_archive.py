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
