# Mom Photo Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 當媽媽在家庭 LINE 群組發任何訊息時，DoubleA bot 自動回傳一張女兒的照片（亂數不重複輪播，1 小時冷卻）。

**Architecture:** 擴充現有 DoubleA bot（`~/Documents/daily-photo-post/line-bot/`）。新增 `mom_photo.py` 模組負責 Drive 照片列舉、洗牌輪播、冷卻判斷；`main.py` 新增 `GET /photo/{file_id}` 串流端點，並在 webhook 前段注入媽媽偵測邏輯。輪播狀態存進現有 Firestore（collection `doublea`），冷卻記憶體管理。

**Tech Stack:** Python 3.13, FastAPI, LINE Bot SDK v3, Google Drive API v3 (`google-api-python-client` 已在 requirements.txt), Google Cloud Firestore, Cloud Run

## Global Constraints

- 全程用 `reply_message`（不耗 push 額度），不新增 push 呼叫
- 媽媽訊息（任何類型：文字/圖片/影片/貼圖/連結）都觸發，不限 TextMessageContent
- 冷卻 1 小時：同一小時內媽媽無論發幾則，只回一張照片
- 照片來源：Google Drive 資料夾（`DAUGHTER_PHOTOS_FOLDER_ID`），只取 JPG/PNG
- Firestore collection `doublea`，輪播狀態文件 `mom_photo_carousel`
- 本機開發：Firestore fallback 到 `chat_state.json`（沿用 `state_service.py` 的 `USE_FIRESTORE` 模式）
- 新環境變數：`MOM_USER_ID`, `DAUGHTER_PHOTOS_FOLDER_ID`, `MOM_PHOTO_COOLDOWN_SEC`（預設 3600）, `DOUBLEA_PUBLIC_URL`（Bot 自己的 Cloud Run URL）

---

## File Map

| 動作 | 路徑 | 負責 |
|------|------|------|
| 修改 | `google_auth.py` | 加入 `drive.readonly` scope |
| 修改 | `auth_setup.py` | 加入 `drive.readonly` scope（重新授權用） |
| 新增 | `mom_photo.py` | Drive 照片列舉、洗牌輪播、冷卻邏輯 |
| 修改 | `main.py` | `/photo/{file_id}` endpoint + webhook 媽媽注入 |
| 新增 | `tests/test_mom_photo.py` | mom_photo.py 單元測試 |
| 修改 | `.env.example` | 補充新環境變數說明 |

---

## Task 1: Drive Scope + Re-auth

**Files:**
- Modify: `google_auth.py`
- Modify: `auth_setup.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `get_credentials()` 回傳的 token 包含 `drive.readonly` scope（後續 Task 2、3 依賴）

- [ ] **Step 1: 修改 `google_auth.py`，加入 Drive scope**

```python
# google_auth.py — 完整替換 SCOPES 清單
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
]
```

- [ ] **Step 2: 修改 `auth_setup.py`，同步加入 Drive scope**

```python
# auth_setup.py — 完整替換 SCOPES 清單
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
]
```

- [ ] **Step 3: 重新授權，取得含 Drive scope 的新 token**

在 `line-bot/` 目錄執行：
```bash
cd ~/Documents/daily-photo-post/line-bot
python auth_setup.py
```
瀏覽器開啟，用 `atticus.wu@gmail.com` 登入並勾選所有權限（Calendar + Tasks + Drive）。
完成後輸出新的 base64 `GOOGLE_TOKEN_JSON` 字串，複製備用。

- [ ] **Step 4: 更新 Cloud Run 環境變數**

```bash
# 把 GOOGLE_TOKEN_JSON 換成新的（含 Drive scope）
# 同時加入新環境變數（先填佔位，Task 4 填實際值）
gcloud run services update doublea-line-bot \
  --region asia-east1 \
  --update-env-vars \
  "GOOGLE_TOKEN_JSON=<新的base64字串>,\
MOM_USER_ID=PLACEHOLDER,\
DAUGHTER_PHOTOS_FOLDER_ID=PLACEHOLDER,\
MOM_PHOTO_COOLDOWN_SEC=3600,\
DOUBLEA_PUBLIC_URL=https://<你的Cloud Run URL>"
```

> **注意：** `DOUBLEA_PUBLIC_URL` 是 DoubleA bot 自己的 Cloud Run URL，格式如 `https://doublea-line-bot-xxxx-de.a.run.app`。從 GCP Console → Cloud Run → doublea-line-bot → URL 複製。

- [ ] **Step 5: 更新 `.env.example`**

在 `.env.example` 尾端加入：
```bash
# Mom Photo Bot
MOM_USER_ID=                        # 媽媽的 LINE userId（部署後從 log 取得）
DAUGHTER_PHOTOS_FOLDER_ID=          # Google Drive 資料夾 ID
MOM_PHOTO_COOLDOWN_SEC=3600         # 冷卻秒數（預設 1 小時）
DOUBLEA_PUBLIC_URL=                 # Bot 自己的 Cloud Run HTTPS URL（給 LINE 抓圖用）
```

- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/daily-photo-post/line-bot add google_auth.py auth_setup.py .env.example
git -C ~/Documents/daily-photo-post/line-bot commit -m "feat: add drive.readonly scope for mom photo bot"
```

---

## Task 2: mom_photo.py — 照片輪播 + 冷卻邏輯

**Files:**
- Create: `mom_photo.py`
- Create: `tests/test_mom_photo.py`

**Interfaces:**
- Consumes: `google_auth.get_credentials()` → `google.oauth2.credentials.Credentials`
- Consumes: `state_service.USE_FIRESTORE` (bool), `state_service._get_db()` (Firestore client)
- Produces:
  - `is_mom(user_id: str) -> bool`
  - `is_on_cooldown() -> bool`
  - `mark_sent() -> None`
  - `get_next_photo_file_id(creds: Credentials, folder_id: str) -> str`（回傳 Drive file_id）
  - `handle_mom_message(reply_token: str, line_api_client) -> bool`（回傳是否已處理）

- [ ] **Step 1: 建立測試檔，寫 `is_mom` 測試**

```python
# tests/test_mom_photo.py
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
```

- [ ] **Step 2: 執行，確認 FAIL（mom_photo 不存在）**

```bash
cd ~/Documents/daily-photo-post/line-bot
python -m pytest tests/test_mom_photo.py -v
```
預期：`ModuleNotFoundError: No module named 'mom_photo'`

- [ ] **Step 3: 建立 `mom_photo.py`，實作 `is_mom`**

```python
# mom_photo.py
import os
import random
import time

MOM_USER_ID = os.environ.get("MOM_USER_ID", "")
DAUGHTER_PHOTOS_FOLDER_ID = os.environ.get("DAUGHTER_PHOTOS_FOLDER_ID", "")
MOM_PHOTO_COOLDOWN_SEC = int(os.environ.get("MOM_PHOTO_COOLDOWN_SEC", "3600"))
DOUBLEA_PUBLIC_URL = os.environ.get("DOUBLEA_PUBLIC_URL", "")

_last_sent_at: float = 0.0


def is_mom(user_id: str) -> bool:
    return bool(MOM_USER_ID) and user_id == MOM_USER_ID
```

- [ ] **Step 4: 執行，確認 `is_mom` 測試通過**

```bash
python -m pytest tests/test_mom_photo.py::test_is_mom_match tests/test_mom_photo.py::test_is_mom_no_match tests/test_mom_photo.py::test_is_mom_empty_env -v
```
預期：3 PASSED

- [ ] **Step 5: 新增冷卻測試**

```python
# 加入 tests/test_mom_photo.py

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
```

- [ ] **Step 6: 實作 `is_on_cooldown` 和 `mark_sent`，加入 `mom_photo.py`**

```python
def is_on_cooldown() -> bool:
    global _last_sent_at
    if _last_sent_at == 0.0:
        return False
    return (time.time() - _last_sent_at) < MOM_PHOTO_COOLDOWN_SEC


def mark_sent() -> None:
    global _last_sent_at
    _last_sent_at = time.time()
```

- [ ] **Step 7: 執行冷卻測試**

```bash
python -m pytest tests/test_mom_photo.py -k "cooldown or mark_sent" -v
```
預期：3 PASSED

- [ ] **Step 8: 新增輪播邏輯測試**

```python
# 加入 tests/test_mom_photo.py

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
                # 3 次應取到 3 個不同 file_id
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
                for _ in range(4):  # 2 張跑 2 輪
                    mom_photo.get_next_photo_file_id(MagicMock(), "folder123")
    # 跑了 4 次（2 輪），應存了 4 次狀態
    assert len(states_saved) == 4
```

- [ ] **Step 9: 實作 `build_drive_service`、`_load_carousel_state`、`_save_carousel_state`、`get_next_photo_file_id`**

```python
# 加入 mom_photo.py

from googleapiclient.discovery import build as _gapi_build

FIRESTORE_COLLECTION = "doublea"
CAROUSEL_DOC = "mom_photo_carousel"
CAROUSEL_STATE_FILE = "mom_photo_carousel.json"  # 本機 fallback

ALLOWED_MIME = {"image/jpeg", "image/png"}


def build_drive_service(creds):
    return _gapi_build("drive", "v3", credentials=creds)


def _load_carousel_state() -> dict:
    """從 Firestore（雲端）或本機 JSON 讀取輪播狀態。"""
    from state_service import USE_FIRESTORE
    if USE_FIRESTORE:
        from state_service import _get_db
        doc = _get_db().collection(FIRESTORE_COLLECTION).document(CAROUSEL_DOC).get()
        return doc.to_dict() or {}
    else:
        import json
        if os.path.exists(CAROUSEL_STATE_FILE):
            with open(CAROUSEL_STATE_FILE) as f:
                return json.load(f)
        return {}


def _save_carousel_state(state: dict) -> None:
    from state_service import USE_FIRESTORE
    if USE_FIRESTORE:
        from state_service import _get_db
        _get_db().collection(FIRESTORE_COLLECTION).document(CAROUSEL_DOC).set(state)
    else:
        import json
        with open(CAROUSEL_STATE_FILE, "w") as f:
            json.dump(state, f)


def _list_photo_files(service, folder_id: str) -> list[dict]:
    """列出 Drive 資料夾中所有 JPG/PNG 檔案。"""
    query = (
        f"'{folder_id}' in parents "
        f"and (mimeType='image/jpeg' or mimeType='image/png') "
        f"and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id,name,mimeType)",
        pageSize=200,
    ).execute()
    return result.get("files", [])


def get_next_photo_file_id(creds, folder_id: str) -> str:
    """洗牌輪播：回傳下一張照片的 Drive file_id。"""
    service = build_drive_service(creds)
    files = _list_photo_files(service, folder_id)
    if not files:
        raise ValueError(f"Google Drive 資料夾 {folder_id} 中找不到 JPG/PNG 照片")

    all_ids = [f["id"] for f in files]
    state = _load_carousel_state()
    queue: list = state.get("queue", [])

    # 過濾掉已被刪除的照片
    valid = set(all_ids)
    queue = [fid for fid in queue if fid in valid]

    # 跑完一輪或空白：重新洗牌（但避免第一張和上一輪最後一張相同）
    if not queue:
        shuffled = all_ids[:]
        random.shuffle(shuffled)
        last = state.get("last_sent")
        if last and len(shuffled) > 1 and shuffled[0] == last:
            shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
        queue = shuffled

    file_id = queue.pop(0)
    _save_carousel_state({"queue": queue, "last_sent": file_id})
    return file_id
```

- [ ] **Step 10: 執行所有測試**

```bash
python -m pytest tests/test_mom_photo.py -v
```
預期：全部 PASSED

- [ ] **Step 11: 新增 `handle_mom_message` 測試**

```python
# 加入 tests/test_mom_photo.py

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

    assert result is True  # 已處理（靜音）
    mock_line_api.reply_message.assert_not_called()
```

- [ ] **Step 12: 實作 `handle_mom_message`**

```python
# 加入 mom_photo.py

from linebot.v3.messaging import (
    ImageMessage,
    ReplyMessageRequest,
)


def handle_mom_message(reply_token: str, line_api) -> bool:
    """媽媽的訊息處理器。回傳 True 代表已處理（含靜音），False 代表非媽媽訊息。"""
    if is_on_cooldown():
        print("[MomPhoto] 冷卻中，略過")
        return True

    try:
        from google_auth import get_credentials
        creds = get_credentials()
        file_id = get_next_photo_file_id(creds, DAUGHTER_PHOTOS_FOLDER_ID)
        photo_url = f"{DOUBLEA_PUBLIC_URL}/photo/{file_id}"
        line_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    ImageMessage(
                        original_content_url=photo_url,
                        preview_image_url=photo_url,
                    )
                ],
            )
        )
        mark_sent()
        print(f"[MomPhoto] 回傳照片：{file_id}")
    except Exception as e:
        print(f"[MomPhoto] 錯誤：{e}")
    return True
```

- [ ] **Step 13: 執行所有測試**

```bash
python -m pytest tests/test_mom_photo.py -v
```
預期：全部 PASSED

- [ ] **Step 14: Commit**

```bash
git -C ~/Documents/daily-photo-post/line-bot add mom_photo.py tests/test_mom_photo.py
git -C ~/Documents/daily-photo-post/line-bot commit -m "feat: add mom_photo module with carousel and cooldown"
```

---

## Task 3: main.py — `/photo/{file_id}` + Webhook 注入

**Files:**
- Modify: `main.py`
- Create: `tests/test_main_mom.py`

**Interfaces:**
- Consumes: `mom_photo.is_mom(user_id: str) -> bool`
- Consumes: `mom_photo.handle_mom_message(reply_token: str, line_api) -> bool`
- Produces: `GET /photo/{file_id}` → StreamingResponse（JPEG/PNG 圖片串流）

- [ ] **Step 1: 新增 `/photo/{file_id}` 測試**

```python
# tests/test_main_mom.py
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
```

- [ ] **Step 2: 執行，確認 FAIL（endpoint 不存在）**

```bash
python -m pytest tests/test_main_mom.py -v
```
預期：FAIL（404 或 import error）

- [ ] **Step 3: 在 `main.py` 加入必要 import 和 `/photo/{file_id}` endpoint**

在 `main.py` 的 import 區加入：
```python
import io
from fastapi.responses import StreamingResponse
from google_auth import get_credentials
from googleapiclient.discovery import build as _gapi_build
from googleapiclient.http import MediaIoBaseDownload
from mom_photo import build_drive_service, is_mom, handle_mom_message
```

在 `main.py` 的 routes 區（`@app.get("/health")` 前）加入：
```python
@app.get("/photo/{file_id}")
async def serve_photo(file_id: str):
    """將 Google Drive 照片串流給 LINE 伺服器。"""
    creds = get_credentials()
    service = build_drive_service(creds)
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    meta = service.files().get(fileId=file_id, fields="mimeType").execute()
    return StreamingResponse(buf, media_type=meta.get("mimeType", "image/jpeg"))
```

- [ ] **Step 4: 執行，確認 `/photo` 測試通過**

```bash
python -m pytest tests/test_main_mom.py::test_photo_endpoint_streams_image -v
```
預期：PASSED

- [ ] **Step 5: 新增 webhook 注入測試**

```python
# 加入 tests/test_main_mom.py

def test_webhook_mom_message_triggers_photo():
    """媽媽的任意訊息應觸發 handle_mom_message，不走原本 process_message。"""
    import os
    os.environ["LINE_CHANNEL_SECRET"] = "test_secret"
    os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test_token"

    body = b'{"destination":"Ubot","events":[{"type":"message","replyToken":"tok123","source":{"type":"group","groupId":"Cgroup","userId":"Umom"},"message":{"type":"image","id":"msg1"}}]}'

    with patch("main.is_mom", return_value=True) as mock_is_mom:
        with patch("main.handle_mom_message", return_value=True) as mock_handle:
            with patch("main.parser") as mock_parser:
                # 模擬 parser.parse 回傳一個 MessageEvent
                mock_event = MagicMock()
                mock_event.source.user_id = "Umom"
                mock_event.reply_token = "tok123"
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
    with patch("main.is_mom", return_value=False) as mock_is_mom:
        with patch("main.handle_mom_message") as mock_handle:
            with patch("main.parser") as mock_parser:
                mock_event = MagicMock()
                mock_event.source.user_id = "Uother"
                mock_event.reply_token = "tok456"
                mock_parser.parse.return_value = [mock_event]

                from main import app
                client = TestClient(app)
                response = client.post(
                    "/webhook",
                    content=b'{}',
                    headers={"X-Line-Signature": "dummy"},
                )

    mock_handle.assert_not_called()
```

- [ ] **Step 6: 執行，確認 FAIL**

```bash
python -m pytest tests/test_main_mom.py -k "webhook" -v
```
預期：FAIL（webhook 還沒有媽媽偵測邏輯）

- [ ] **Step 7: 修改 `main.py` webhook handler，在事件迴圈前段注入媽媽偵測**

找到 `main.py` 的 `webhook()` 函數，將 `for event in events:` 迴圈改為：

```python
@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        # 媽媽偵測：任意訊息類型都觸發，優先於其他 handler
        if isinstance(event, MessageEvent):
            user_id = getattr(event.source, "user_id", None) or ""
            if is_mom(user_id):
                with ApiClient(line_config) as api_client:
                    handle_mom_message(event.reply_token, MessagingApi(api_client))
                continue  # 跳過原本的 process_message

        if isinstance(event, MessageEvent) and isinstance(
            event.message, TextMessageContent
        ):
            background_tasks.add_task(
                process_message,
                event.message.text.strip(),
                _get_chat_id(event),
                event.reply_token,
            )
    return JSONResponse(content={"status": "ok"})
```

- [ ] **Step 8: 執行所有測試**

```bash
python -m pytest tests/ -v
```
預期：全部 PASSED

- [ ] **Step 9: Commit**

```bash
git -C ~/Documents/daily-photo-post/line-bot add main.py tests/test_main_mom.py
git -C ~/Documents/daily-photo-post/line-bot commit -m "feat: add /photo endpoint and mom detection in webhook"
```

---

## Task 4: 部署 + 驗收

**Files:**
- 無程式碼變更

**Interfaces:**
- 消費：Task 1–3 的全部成果

- [ ] **Step 1: 確認 Drive 資料夾設定**

1. 在 Google Drive 建立一個資料夾（或使用現有資料夾）
2. 把女兒的照片（JPG/PNG）丟進去（至少 1 張）
3. 複製資料夾 URL 中的 ID（`https://drive.google.com/drive/folders/<這段>`）
4. 把資料夾**分享給 `atticus.wu@gmail.com`**（就是你自己，OAuth 登入的帳號）

- [ ] **Step 2: 部署到 Cloud Run**

```bash
cd ~/Documents/daily-photo-post/line-bot
# 確認 GCP project 是 doubla-family-todo
gcloud config set project doubla-family-todo

# 部署
gcloud run deploy doublea-line-bot \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated
```

- [ ] **Step 3: 更新環境變數（填入實際值）**

```bash
gcloud run services update doublea-line-bot \
  --region asia-east1 \
  --update-env-vars \
  "DAUGHTER_PHOTOS_FOLDER_ID=<Drive資料夾ID>,DOUBLEA_PUBLIC_URL=<CloudRunURL>"
```

`DOUBLEA_PUBLIC_URL` 從部署完成後輸出的 Service URL 取得。

- [ ] **Step 4: 取得媽媽的 LINE userId**

媽媽在 LINE 群組發一則任意訊息（例如傳一張圖），然後：

```bash
gcloud logs read "resource.type=cloud_run_revision AND resource.labels.service_name=doublea-line-bot" \
  --limit 20 \
  --format "value(textPayload)" \
  --region asia-east1 | grep -E "userId|user_id"
```

或前往 GCP Console → Cloud Run → doublea-line-bot → Logs，搜尋 `userId`。

- [ ] **Step 5: 設定 MOM_USER_ID**

```bash
gcloud run services update doublea-line-bot \
  --region asia-east1 \
  --update-env-vars "MOM_USER_ID=<媽媽的userId>"
```

- [ ] **Step 6: 端到端驗收**

1. 媽媽傳一則訊息（或自己用另一個帳號測）
2. 確認 bot 在群組回了一張女兒的照片
3. 馬上再傳一則訊息，確認 bot **不回應**（冷卻中）
4. 確認 Cloud Run log 出現 `[MomPhoto] 回傳照片：<file_id>`

- [ ] **Step 7: 最終 commit（補 log 確認）**

```bash
git -C ~/Documents/daily-photo-post/line-bot commit --allow-empty -m "deploy: mom-photo-bot live on Cloud Run"
```
