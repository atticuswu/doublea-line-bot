from unittest.mock import MagicMock, patch
import hashlib
import hmac as _hmac
import io
import time

import pytest
from fastapi.testclient import TestClient
from linebot.v3.webhooks import MessageEvent


class MockSource:
    """Minimal mock of MessageEvent.source for testing isinstance."""
    def __init__(self, user_id):
        self.user_id = user_id


class FakeMessageEvent(MessageEvent):
    """Fake subclass of MessageEvent that passes isinstance checks."""
    def __init__(self):
        # Bypass Pydantic initialization
        object.__setattr__(self, '__dict__', {})


def _make_test_sig(file_id: str, secret: str, expires: int) -> str:
    msg = f"{file_id}:{expires}".encode()
    return _hmac.HMAC(secret.encode(), msg, hashlib.sha256).hexdigest()


def test_photo_endpoint_streams_image():
    """GET /photo/{file_id} 應回傳圖片串流（需帶有效 HMAC sig）。"""
    fake_image = b"\xff\xd8\xff"  # JPEG magic bytes
    secret = "test_secret"
    file_id = "file_abc123"
    expires = int(time.time()) + 3600
    sig = _make_test_sig(file_id, secret, expires)

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

    with patch.dict("os.environ", {"PHOTO_SERVE_SECRET": secret}):
        with patch("main.build_drive_service", return_value=mock_service):
            with patch("main.MediaIoBaseDownload", side_effect=fake_download):
                with patch("main.get_credentials", return_value=MagicMock()):
                    from main import app
                    client = TestClient(app)
                    response = client.get(f"/photo/{file_id}?sig={sig}&expires={expires}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == fake_image


def test_webhook_mom_message_triggers_photo():
    """媽媽的任意訊息應觸發 handle_mom_message，不走原本 process_message。"""
    import os
    os.environ["LINE_CHANNEL_SECRET"] = "test_secret"
    os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test_token"

    body = b'{"destination":"Ubot","events":[]}'

    mock_event = FakeMessageEvent()
    object.__setattr__(mock_event, 'source', MockSource("Umom"))
    object.__setattr__(mock_event, 'reply_token', "tok123")

    with patch("main.is_mom", return_value=True) as mock_is_mom:
        with patch("main.handle_mom_message", return_value=True) as mock_handle:
            with patch("main.parser") as mock_parser:
                mock_parser.parse.return_value = [mock_event]
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
    mock_event = FakeMessageEvent()
    object.__setattr__(mock_event, 'source', MockSource("Uother"))
    object.__setattr__(mock_event, 'reply_token', "tok456")
    # Set message to None to avoid AttributeError in the second isinstance check
    object.__setattr__(mock_event, 'message', None)

    with patch("main.is_mom", return_value=False) as mock_is_mom:
        with patch("main.handle_mom_message") as mock_handle:
            with patch("main.parser") as mock_parser:
                mock_parser.parse.return_value = [mock_event]
                from main import app
                client = TestClient(app)
                response = client.post(
                    "/webhook",
                    content=b'{}',
                    headers={"X-Line-Signature": "dummy"},
                )

    mock_handle.assert_not_called()
