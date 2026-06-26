import builtins
from unittest.mock import MagicMock, patch
import io

import pytest
from fastapi.testclient import TestClient


def test_photo_endpoint_streams_image():
    """GET /photo/{file_id} 應回傳圖片串流。"""
    fake_image = b"\xff\xd8\xff"  # JPEG magic bytes

    mock_service = MagicMock()
    # MediaIoBaseDownload 寫入 buf 的 mock
    def fake_download(buf, request):
        downloader = MagicMock()
        def next_chunk():
            buf.write(fake_image)
            return MagicMock(), True
        downloader.next_chunk = next_chunk
        return downloader

    mock_service.files().get_media.return_value = MagicMock()
    mock_service.files().get().execute.return_value = {"mimeType": "image/jpeg"}

    with patch("main.build_drive_service", return_value=mock_service):
        with patch("main.MediaIoBaseDownload", side_effect=fake_download):
            with patch("main.get_credentials", return_value=MagicMock()):
                from main import app
                client = TestClient(app)
                response = client.get("/photo/file_abc123")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == fake_image


def _make_isinstance_patch(target_class):
    """Return a side_effect function that makes MagicMock instances pass isinstance(obj, target_class)."""
    real_isinstance = builtins.isinstance

    def fake_isinstance(obj, cls):
        if cls is target_class and real_isinstance(obj, MagicMock):
            return True
        return real_isinstance(obj, cls)

    return fake_isinstance


def test_webhook_mom_message_triggers_photo():
    """媽媽的任意訊息應觸發 handle_mom_message，不走原本 process_message。"""
    import os
    os.environ["LINE_CHANNEL_SECRET"] = "test_secret"
    os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test_token"

    body = b'{"destination":"Ubot","events":[]}'

    from linebot.v3.webhooks import MessageEvent
    mock_event = MagicMock()
    mock_event.source.user_id = "Umom"
    mock_event.reply_token = "tok123"

    with patch("main.is_mom", return_value=True) as mock_is_mom:
        with patch("main.handle_mom_message", return_value=True) as mock_handle:
            with patch("main.parser") as mock_parser:
                mock_parser.parse.return_value = [mock_event]
                with patch("main.isinstance", side_effect=_make_isinstance_patch(MessageEvent)):
                    from main import app
                    client = TestClient(app)
                    response = client.post(
                        "/webhook",
                        content=body,
                        headers={"X-Line-Signature": "dummy"},
                    )

    assert response.status_code == 200
    mock_is_mom.assert_called_once_with("Umom")
    mock_handle.assert_called_once()


def test_webhook_non_mom_message_skips_photo():
    """非媽媽的訊息不觸發 handle_mom_message。"""
    from linebot.v3.webhooks import MessageEvent
    mock_event = MagicMock()
    mock_event.source.user_id = "Uother"
    mock_event.reply_token = "tok456"

    with patch("main.is_mom", return_value=False) as mock_is_mom:
        with patch("main.handle_mom_message") as mock_handle:
            with patch("main.parser") as mock_parser:
                mock_parser.parse.return_value = [mock_event]
                with patch("main.isinstance", side_effect=_make_isinstance_patch(MessageEvent)):
                    from main import app
                    client = TestClient(app)
                    response = client.post(
                        "/webhook",
                        content=b'{}',
                        headers={"X-Line-Signature": "dummy"},
                    )

    mock_handle.assert_not_called()
