# Dashboard + 照片歸檔 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Firestore config 驅動的功能路由 + `/admin` dashboard 網頁 + 指定群組照片自動歸檔 Google Drive。

**Architecture:** 新模組 `bot_config.py`（config 讀寫 + 60s 快取）與 `photo_archive.py`（LINE Content API 下載 → Drive 上傳，月份/事件資料夾）。`main.py` webhook 改為統一路由讀 config，新增 `/admin` GET/POST endpoint（token 驗證）。既有環境變數 `DOUBLEA_GROUP_ID`、`MOM_USER_ID` 由一次性初始化腳本遷入 Firestore `doublea/config`。

**Tech Stack:** Python 3.13, FastAPI, line-bot-sdk v3, google-api-python-client, google-cloud-firestore, httpx, pytest。

## Global Constraints

- 專案路徑：`/Users/atticus/Documents/daily-photo-post/line-bot`
- Firestore collection `doublea`，config 文件 ID `config`（沿用 `state_service.py` 的 `K_SERVICE` 判斷 + 本機 JSON fallback 模式）
- 不消耗 LINE push 額度：確認訊息一律用 reply API
- Cloud Run 記憶體維持 512MB；下載照片單張處理完即釋放（照片歸檔在 background task 逐張處理，不並發緩衝）
- OAuth SCOPES 同時需要 `drive.readonly`（媽媽照片讀既有資料夾）與 `drive.file`（歸檔上傳），兩者並列
- 測試指令一律 `python3 -m pytest tests/ -v`，在專案根目錄執行
- 每個 commit 訊息結尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: `bot_config.py` — config 讀寫、快取、路由判斷

**Files:**
- Create: `bot_config.py`
- Test: `tests/test_bot_config.py`

**Interfaces:**
- Consumes: `state_service.py` 的 Firestore 模式判斷慣例（`os.environ.get("K_SERVICE")`）
- Produces:
  - `load_config() -> dict`（60 秒快取）
  - `save_config(cfg: dict) -> None`（寫入並使快取失效）
  - `invalidate_cache() -> None`
  - `is_feature_on(feature: str, chat_id: str) -> bool`（enabled 且 chat_id 在綁定清單）
  - `get_mom_user_id() -> str`
  - `get_todo_chat_id() -> str | None`（`features.todo.chat_ids[0]`）
  - `register_chat(chat_id: str, chat_type: str, name: str = "") -> None`（已存在則跳過，不覆寫備註）
  - `DEFAULT_CONFIG: dict`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_bot_config.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_bot_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bot_config'`）

- [ ] **Step 3: 實作 `bot_config.py`**

```python
"""功能 × 群組 × 開關 的集中設定。Firestore doublea/config，本機 JSON fallback。"""
import json
import os
import time

FIRESTORE_COLLECTION = "doublea"
CONFIG_DOC = "config"
LOCAL_CONFIG_PATH = "bot_config.json"
CACHE_TTL_SEC = 60

DEFAULT_CONFIG = {
    "features": {
        "todo": {"enabled": True, "chat_ids": []},
        "mom_photo": {"enabled": True, "chat_ids": []},
        "photo_archive": {"enabled": False, "chat_ids": []},
    },
    "known_chats": {},
    "mom_user_id": "",
}

_cache: dict | None = None
_cache_at: float = 0.0


def _use_firestore() -> bool:
    return os.environ.get("K_SERVICE") is not None


def _get_db():
    from google.cloud import firestore
    return firestore.Client()


def _merge_default(cfg: dict) -> dict:
    """補齊缺少的 keys（新功能加入 DEFAULT_CONFIG 後自動出現）。"""
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in cfg.items() if k not in ("features",)})
    for name, feat in cfg.get("features", {}).items():
        merged["features"][name] = feat
    return merged


def _read_config() -> dict:
    if _use_firestore():
        doc = _get_db().collection(FIRESTORE_COLLECTION).document(CONFIG_DOC).get()
        return _merge_default(doc.to_dict() or {}) if doc.exists else json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(LOCAL_CONFIG_PATH):
        with open(LOCAL_CONFIG_PATH) as f:
            return _merge_default(json.load(f))
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_config() -> dict:
    global _cache, _cache_at
    if _cache is not None and time.time() - _cache_at < CACHE_TTL_SEC:
        return _cache
    _cache = _read_config()
    _cache_at = time.time()
    return _cache


def save_config(cfg: dict) -> None:
    global _cache, _cache_at
    if _use_firestore():
        _get_db().collection(FIRESTORE_COLLECTION).document(CONFIG_DOC).set(cfg)
    else:
        with open(LOCAL_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    _cache = cfg
    _cache_at = time.time()


def invalidate_cache() -> None:
    global _cache
    _cache = None


def is_feature_on(feature: str, chat_id: str) -> bool:
    feat = load_config()["features"].get(feature)
    if not feat or not feat.get("enabled"):
        return False
    return chat_id in feat.get("chat_ids", [])


def get_mom_user_id() -> str:
    return load_config().get("mom_user_id", "")


def get_todo_chat_id() -> str | None:
    ids = load_config()["features"].get("todo", {}).get("chat_ids", [])
    return ids[0] if ids else None


def register_chat(chat_id: str, chat_type: str, name: str = "") -> None:
    cfg = load_config()
    if chat_id in cfg.get("known_chats", {}):
        return
    cfg.setdefault("known_chats", {})[chat_id] = {
        "name": name, "type": chat_type, "note": ""
    }
    save_config(cfg)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_bot_config.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add bot_config.py tests/test_bot_config.py
git commit -m "feat: bot_config 模組——Firestore 集中設定、60s 快取、功能路由判斷"
```

---

### Task 2: `photo_archive.py` — 下載、資料夾邏輯、上傳

**Files:**
- Create: `photo_archive.py`
- Test: `tests/test_photo_archive.py`

**Interfaces:**
- Consumes: `google_auth.get_credentials()`、`mom_photo.build_drive_service(creds)`、`bot_config`（僅由 main.py 判斷路由，本模組不讀 config）
- Produces:
  - `handle_album_command(text: str, reply_token: str, line_api) -> bool`（「相簿 <地點>」→ 建資料夾、進事件模式、reply 確認、回 True；其他文字回 False）
  - `archive_photo(message_id: str) -> bool`（下載 → 決定資料夾 → 上傳，成功 True）
  - `_get_target_folder(service) -> str`（回 folder_id：事件模式未逾時→事件資料夾；否則月份資料夾）
  - `_ensure_folder(service, name: str, parent_id: str | None) -> str`
  - `_make_filename(message_id: str, now) -> str`（`YYYYMMDD-HHMMSS_{message_id}.jpg`）
  - 常數 `ROOT_FOLDER_NAME = "LINE相簿"`、`EVENT_TIMEOUT_SEC = 3 * 86400`
  - 事件狀態存 Firestore `doublea/photo_archive_state`（本機 fallback `photo_archive_state.json`）：`{mode, event_folder, event_folder_id, last_photo_at}`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_photo_archive.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_photo_archive.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'photo_archive'`）

- [ ] **Step 3: 實作 `photo_archive.py`**

```python
"""照片自動歸檔 Google Drive：LINE Content API 下載 → 月份/事件資料夾。"""
import io
import json
import os
import time
from datetime import datetime

import httpx
import pytz
from googleapiclient.http import MediaIoBaseUpload

from google_auth import get_credentials
from mom_photo import build_drive_service

ROOT_FOLDER_NAME = "LINE相簿"
EVENT_TIMEOUT_SEC = 3 * 86400
FIRESTORE_COLLECTION = "doublea"
STATE_DOC = "photo_archive_state"
LOCAL_STATE_PATH = "photo_archive_state.json"
TAIPEI_TZ = pytz.timezone("Asia/Taipei")

DEFAULT_STATE = {"mode": "monthly", "event_folder": "", "event_folder_id": "", "last_photo_at": 0.0}


def _use_firestore() -> bool:
    return os.environ.get("K_SERVICE") is not None


def _load_state() -> dict:
    if _use_firestore():
        from google.cloud import firestore
        doc = firestore.Client().collection(FIRESTORE_COLLECTION).document(STATE_DOC).get()
        return doc.to_dict() if doc.exists else dict(DEFAULT_STATE)
    if os.path.exists(LOCAL_STATE_PATH):
        with open(LOCAL_STATE_PATH) as f:
            return json.load(f)
    return dict(DEFAULT_STATE)


def _save_state(state: dict) -> None:
    if _use_firestore():
        from google.cloud import firestore
        firestore.Client().collection(FIRESTORE_COLLECTION).document(STATE_DOC).set(state)
    else:
        with open(LOCAL_STATE_PATH, "w") as f:
            json.dump(state, f, ensure_ascii=False)


def _make_filename(message_id: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%d-%H%M%S')}_{message_id}.jpg"


def _ensure_folder(service, name: str, parent_id: str | None) -> str:
    """找同名資料夾，沒有就建立，回傳 folder_id。"""
    q = (
        f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    return service.files().create(body=body, fields="id").execute()["id"]


def _monthly_folder(service) -> str:
    root_id = _ensure_folder(service, ROOT_FOLDER_NAME, None)
    month = datetime.now(TAIPEI_TZ).strftime("%Y-%m")
    return _ensure_folder(service, month, root_id)


def _get_target_folder(service) -> str:
    state = _load_state()
    if state.get("mode") == "event":
        if time.time() - state.get("last_photo_at", 0) <= EVENT_TIMEOUT_SEC:
            return state["event_folder_id"]
        state.update({"mode": "monthly", "event_folder": "", "event_folder_id": ""})
        _save_state(state)
        print("[Archive] 事件逾時，退回月份模式")
    return _monthly_folder(service)


def _start_event(location: str) -> None:
    creds = get_credentials()
    service = build_drive_service(creds)
    root_id = _ensure_folder(service, ROOT_FOLDER_NAME, None)
    name = f"{datetime.now(TAIPEI_TZ).strftime('%Y-%m')}_{location}"
    folder_id = _ensure_folder(service, name, root_id)
    _save_state({
        "mode": "event", "event_folder": name,
        "event_folder_id": folder_id, "last_photo_at": time.time(),
    })
    print(f"[Archive] 事件模式開始：{name}")


def handle_album_command(text: str, reply_token: str, line_api) -> bool:
    """「相簿 <地點>」→ 進事件模式並回覆確認。非此指令回 False。"""
    from linebot.v3.messaging import ReplyMessageRequest, TextMessage

    text = text.strip()
    if not text.startswith("相簿 "):
        return False
    location = text[len("相簿 "):].strip()
    if not location:
        return False
    _start_event(location)
    state = _load_state()
    line_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(
                text=f"📸 相簿「{state['event_folder']}」開好了！"
                     f"接下來的照片會自動存進去（3 天沒新照片自動結束）"
            )],
        )
    )
    return True


def _download_line_content(message_id: str) -> bytes:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.content


def _upload_to_drive(service, data: bytes, filename: str, folder_id: str) -> None:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg", resumable=False)
    service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id",
    ).execute()


def archive_photo(message_id: str) -> bool:
    """下載 LINE 照片並上傳 Drive。失敗重試 3 次。"""
    try:
        data = _download_line_content(message_id)
    except Exception as e:
        print(f"[Archive] 下載失敗 message_id={message_id}: {e}")
        return False

    creds = get_credentials()
    service = build_drive_service(creds)
    folder_id = _get_target_folder(service)
    filename = _make_filename(message_id, datetime.now(TAIPEI_TZ))

    for attempt in range(3):
        try:
            _upload_to_drive(service, data, filename, folder_id)
            state = _load_state()
            state["last_photo_at"] = time.time()
            _save_state(state)
            print(f"[Archive] 已歸檔 {filename}")
            return True
        except Exception as e:
            print(f"[Archive] 上傳失敗（第 {attempt + 1} 次）message_id={message_id}: {e}")
            time.sleep(2)
    return False
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_photo_archive.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add photo_archive.py tests/test_photo_archive.py
git commit -m "feat: photo_archive 模組——Content API 下載、月份/事件資料夾、重試上傳"
```

---

### Task 3: OAuth scope 升級 + 一次性 config 初始化腳本

**Files:**
- Modify: `google_auth.py:8-12`（SCOPES）
- Modify: `auth_setup.py:16-20`（SCOPES）
- Create: `init_config.py`

**Interfaces:**
- Consumes: `bot_config.DEFAULT_CONFIG`、`bot_config.save_config`
- Produces: Firestore `doublea/config` 初始資料（現有綁定遷入）

- [ ] **Step 1: 兩個檔案的 SCOPES 都改為**

```python
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
```

- [ ] **Step 2: 建立 `init_config.py`**

```python
"""一次性：把現有環境變數綁定遷入 Firestore doublea/config。
Cloud Run 部署後執行：本機以 gcloud ADC 直接寫 Firestore。
用法：GOOGLE_CLOUD_PROJECT=doubla-family-todo python3 init_config.py
"""
import os

os.environ["K_SERVICE"] = "local-init"  # 強制 bot_config 走 Firestore

import bot_config

cfg = {
    "features": {
        "todo": {"enabled": True, "chat_ids": ["C6d18ccad490e34abb7cafd01676b0502"]},
        "mom_photo": {"enabled": True, "chat_ids": []},
        "photo_archive": {"enabled": False, "chat_ids": []},
    },
    "known_chats": {
        "C6d18ccad490e34abb7cafd01676b0502": {
            "name": "DoubleA", "type": "group", "note": "Atticus+Angel 待辦群組"
        },
        "R27e7c4f6ad2228ee12112597d28f79f2": {
            "name": "", "type": "room", "note": "家族群組（媽媽姊姊）"
        },
    },
    "mom_user_id": "Uc6f4be62dce38f87dbab76bc3d26ecc1",
}
bot_config.save_config(cfg)
print("✅ config 已寫入 Firestore doublea/config")
print(bot_config.load_config())
```

- [ ] **Step 3: 語法驗證**

Run: `python3 -c "import ast; [ast.parse(open(f).read()) for f in ['google_auth.py','auth_setup.py','init_config.py']]; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add google_auth.py auth_setup.py init_config.py
git commit -m "feat: 新增 drive.file scope 與 config 初始化腳本"
```

**注意：** 此 task 完成後，**使用者必須手動重跑 `python3 auth_setup.py`** 取得含 `drive.file` 的新 token（部署階段 Task 6 的前置條件，執行到 Task 6 時再提醒使用者，本 task 不需要）。

---

### Task 4: `main.py` webhook 統一路由

**Files:**
- Modify: `main.py`（webhook handler 區段，約 492-530 行；`process_message` 的 `save_chat_id` 行為；`morning-briefing`/`check-reminders` 的 `load_chat_id`）
- Test: `tests/test_main_routing.py`

**Interfaces:**
- Consumes: `bot_config.is_feature_on / get_mom_user_id / get_todo_chat_id / register_chat`、`photo_archive.handle_album_command / archive_photo`、既有 `mom_photo.handle_mom_message`
- Produces: webhook 路由順序 mom_photo → photo_archive → todo；`get_briefing_chat_id() -> str | None`（推播目標改讀 config，取代 `load_chat_id`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_main_routing.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_main_routing.py -v`
Expected: FAIL（`AttributeError: module 'main' has no attribute '_route_event'`）

- [ ] **Step 3: 修改 `main.py`**

3a. import 區新增：

```python
import bot_config
import photo_archive
```

3b. 新增純函式 `_route_event`（放在 `_dispatch_mom` 附近），並改寫 webhook：

```python
def _route_event(event) -> tuple | None:
    """決定事件的處理方式。回傳 (action, *args) 或 None（略過）。"""
    if not isinstance(event, MessageEvent):
        return None
    chat_id = _get_chat_id(event)
    user_id = getattr(event.source, "user_id", None) or ""

    # 1. 媽媽照片：不限群組
    if user_id and user_id == bot_config.get_mom_user_id():
        return ("mom_photo", event.reply_token)

    # 2. 照片歸檔：綁定群組的圖片與「相簿」指令
    if bot_config.is_feature_on("photo_archive", chat_id):
        if isinstance(event.message, ImageMessageContent):
            return ("archive_photo", event.message.id)
        if isinstance(event.message, TextMessageContent):
            text = event.message.text.strip()
            if text.startswith("相簿 "):
                return ("album_command", text, event.reply_token)

    # 3. 待辦：綁定群組的文字訊息
    if bot_config.is_feature_on("todo", chat_id) and isinstance(
        event.message, TextMessageContent
    ):
        return ("todo", event.message.text.strip(), chat_id, event.reply_token)

    return None


def _dispatch_album_command(text: str, reply_token: str) -> None:
    with ApiClient(line_config) as api_client:
        photo_archive.handle_album_command(text, reply_token, MessagingApi(api_client))


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent):
            chat_id = _get_chat_id(event)
            chat_type = "group" if getattr(event.source, "group_id", None) else (
                "room" if getattr(event.source, "room_id", None) else "user"
            )
            background_tasks.add_task(bot_config.register_chat, chat_id, chat_type)

        action = _route_event(event)
        if action is None:
            continue
        kind = action[0]
        if kind == "mom_photo":
            background_tasks.add_task(_dispatch_mom, action[1])
        elif kind == "archive_photo":
            background_tasks.add_task(photo_archive.archive_photo, action[1])
        elif kind == "album_command":
            background_tasks.add_task(_dispatch_album_command, action[1], action[2])
        elif kind == "todo":
            background_tasks.add_task(process_message, action[1], action[2], action[3])
    return JSONResponse(content={"status": "ok"})
```

3c. import 區補 `ImageMessageContent`（來自 `linebot.v3.webhooks`）。

3d. `process_message` 內移除 `save_chat_id(chat_id)`（337 行）——推播目標改由 config 提供。

3e. `morning_briefing` 與 `check_reminders` 中 `chat_id = load_chat_id()` 改為：

```python
chat_id = bot_config.get_todo_chat_id()
```

（`load_chat_id`/`save_chat_id` 的 import 與 `state_service` 中的函式保留不動——僅不再被 main.py 呼叫，避免本次改動範圍擴大。）

- [ ] **Step 4: 跑全部測試**

Run: `python3 -m pytest tests/ -v`
Expected: 全部 PASS（含既有 13 個測試 + 新增路由測試。若 `tests/test_main_mom.py` 因 `is_mom` env var 邏輯改動而失敗，將其中對 `MOM_USER_ID` env 的 patch 改為 patch `main.bot_config.get_mom_user_id`）

- [ ] **Step 5: 語法驗證 + Commit**

```bash
python3 -c "import ast; ast.parse(open('main.py').read()); print('OK')"
git add main.py tests/test_main_routing.py tests/test_main_mom.py
git commit -m "refactor: webhook 統一路由——config 驅動 mom_photo/photo_archive/todo"
```

---

### Task 5: `/admin` dashboard

**Files:**
- Modify: `main.py`（新增 `/admin` GET 與 `/admin/config` POST）
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: `bot_config.load_config / save_config`
- Produces: `GET /admin?token=` 回 HTML；`POST /admin/config?token=`（JSON body 為完整 config）寫入並回 `{"status":"ok"}`；token 錯誤一律 403

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_admin.py
import os
from unittest.mock import patch

os.environ.setdefault("LINE_CHANNEL_SECRET", "testsecret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "testtoken")
os.environ["ADMIN_TOKEN"] = "sekret"

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_admin_rejects_bad_token():
    assert client.get("/admin?token=wrong").status_code == 403
    assert client.get("/admin").status_code == 403


def test_admin_page_renders():
    with patch.object(main.bot_config, "load_config", return_value=main.bot_config.DEFAULT_CONFIG):
        r = client.get("/admin?token=sekret")
    assert r.status_code == 200
    assert "todo" in r.text


def test_admin_config_post_saves():
    new_cfg = {
        "features": {"todo": {"enabled": False, "chat_ids": []}},
        "known_chats": {}, "mom_user_id": "",
    }
    with patch.object(main.bot_config, "save_config") as sc:
        r = client.post("/admin/config?token=sekret", json=new_cfg)
    assert r.status_code == 200
    sc.assert_called_once()


def test_admin_config_post_rejects_bad_token():
    with patch.object(main.bot_config, "save_config") as sc:
        r = client.post("/admin/config?token=nope", json={})
    assert r.status_code == 403
    sc.assert_not_called()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_admin.py -v`
Expected: FAIL（404 not 403——route 不存在）

- [ ] **Step 3: 在 `main.py` 新增**

```python
FEATURE_LABELS = {
    "todo": "待辦 + 早安推播",
    "mom_photo": "媽媽照片回覆",
    "photo_archive": "照片歸檔",
}


def _check_admin_token(token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not _hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(token: str = ""):
    _check_admin_token(token)
    cfg = bot_config.load_config()
    import json as _json
    return ADMIN_HTML.replace("__CONFIG__", _json.dumps(cfg, ensure_ascii=False)) \
                     .replace("__LABELS__", _json.dumps(FEATURE_LABELS, ensure_ascii=False)) \
                     .replace("__TOKEN__", token)


@app.post("/admin/config")
async def admin_save(request: Request, token: str = ""):
    _check_admin_token(token)
    cfg = await request.json()
    bot_config.save_config(cfg)
    return JSONResponse(content={"status": "ok"})


ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DoubleA Bot Dashboard</title>
<style>
body{font-family:-apple-system,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;background:#fafafa}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem;margin:.8rem 0}
.feature-head{display:flex;justify-content:space-between;align-items:center}
label.chat{display:block;margin:.3rem 0 .3rem 1rem}
input[type=text]{width:12rem;padding:.2rem}
button{background:#06c755;color:#fff;border:none;border-radius:6px;padding:.6rem 1.4rem;font-size:1rem;cursor:pointer}
.toggle{width:2.6rem;height:1.5rem}
#msg{margin-left:1rem;color:#06c755}
</style></head><body>
<h1>🤖 DoubleA Bot Dashboard</h1>
<div id="features"></div>
<h2>已知群組</h2>
<div id="chats"></div>
<button onclick="save()">儲存</button><span id="msg"></span>
<script>
const cfg = __CONFIG__;
const labels = __LABELS__;
const token = "__TOKEN__";

function chatLabel(id){
  const c = cfg.known_chats[id] || {};
  return (c.note || c.name || id) + " (" + id.slice(0,8) + "…)";
}
function render(){
  const f = document.getElementById("features");
  f.innerHTML = "";
  for(const [name, feat] of Object.entries(cfg.features)){
    const card = document.createElement("div"); card.className = "card";
    let html = `<div class="feature-head"><strong>${labels[name]||name}</strong>
      <input class="toggle" type="checkbox" ${feat.enabled?"checked":""}
        onchange="cfg.features['${name}'].enabled=this.checked"></div>`;
    if(name !== "mom_photo"){
      for(const id of Object.keys(cfg.known_chats)){
        const on = feat.chat_ids.includes(id);
        html += `<label class="chat"><input type="checkbox" ${on?"checked":""}
          onchange="toggleChat('${name}','${id}',this.checked)"> ${chatLabel(id)}</label>`;
      }
    } else {
      html += `<label class="chat">依媽媽的 user_id 觸發，不限群組</label>`;
    }
    card.innerHTML = html; f.appendChild(card);
  }
  const ch = document.getElementById("chats");
  ch.innerHTML = "";
  for(const [id, c] of Object.entries(cfg.known_chats)){
    const card = document.createElement("div"); card.className = "card";
    card.innerHTML = `<code>${id}</code> [${c.type}] ${c.name||""}
      備註：<input type="text" value="${c.note||""}"
        onchange="cfg.known_chats['${id}'].note=this.value">`;
    ch.appendChild(card);
  }
}
function toggleChat(f, id, on){
  const arr = cfg.features[f].chat_ids;
  if(on && !arr.includes(id)) arr.push(id);
  if(!on) cfg.features[f].chat_ids = arr.filter(x=>x!==id);
}
async function save(){
  const r = await fetch(`/admin/config?token=${token}`, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(cfg)});
  document.getElementById("msg").textContent = r.ok ? "已儲存 ✓" : "失敗！";
  setTimeout(()=>document.getElementById("msg").textContent="", 3000);
}
render();
</script></body></html>"""
```

import 區補 `from fastapi.responses import HTMLResponse`（與既有 responses import 併列）。

- [ ] **Step 4: 跑全部測試**

Run: `python3 -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
python3 -c "import ast; ast.parse(open('main.py').read()); print('OK')"
git add main.py tests/test_admin.py
git commit -m "feat: /admin dashboard——功能矩陣、群組綁定、開關、token 驗證"
```

---

### Task 6: 部署 + 驗收（含手動步驟）

**Files:**
- Modify: Cloud Run service `doublea-bot`（env vars）
- 無程式碼變更

**前置手動步驟（執行前明確請使用者操作）：**

1. **重新授權**：使用者在 `line-bot/` 目錄跑 `python3 auth_setup.py`（需含新 scope `drive.file`），把輸出的 base64 提供給你
2. 產生 `ADMIN_TOKEN`：`openssl rand -hex 24`

- [ ] **Step 1: 初始化 Firestore config**

```bash
GOOGLE_CLOUD_PROJECT=doubla-family-todo python3 init_config.py
```
Expected: `✅ config 已寫入 Firestore doublea/config`
（若本機 ADC 未設定，fallback：用 `gcloud auth print-access-token` + Firestore REST API PATCH 寫入，同上次修 `group_chat_id` 的方法）

- [ ] **Step 2: 更新 env vars 並部署**

```bash
gcloud run deploy doublea-bot --source . \
  --project=doubla-family-todo --region=asia-east1 --memory=512Mi \
  --update-env-vars="GOOGLE_TOKEN_JSON=<新token>,ADMIN_TOKEN=<token>" \
  --remove-env-vars="DOUBLEA_GROUP_ID,MOM_USER_ID" --quiet
```
Expected: 部署成功，新 revision serving 100%

- [ ] **Step 3: 驗收既有功能不回歸**

- 使用者在 DoubleA 群組發文字 → bot 正常回應
- 請媽媽（或等她自然發訊息）→ 回女兒照片
- `curl -s -X POST https://doublea-bot-709846269791.asia-east1.run.app/morning-briefing` → 推播到 DoubleA 群組（或確認 log 目標 chat_id 正確）

- [ ] **Step 4: 驗收 dashboard**

- 開 `https://doublea-bot-709846269791.asia-east1.run.app/admin?token=<ADMIN_TOKEN>` → 顯示功能矩陣
- 關閉 todo → DoubleA 群組發文字無回應 → 重新開啟恢復（注意 60s 快取延遲）

- [ ] **Step 5: 驗收照片歸檔**

- 使用者拉 bot 進新群組、發一句話 → dashboard known_chats 出現該群組
- dashboard 綁定 photo_archive + 開啟 → 群組傳照片 → Drive `LINE相簿/YYYY-MM/` 出現檔案
- 發「相簿 測試」→ bot 回確認 → 傳照片進 `YYYY-MM_測試/`

- [ ] **Step 6: 自訂網域（可後補，不阻塞驗收）**

```bash
gcloud beta run domain-mappings create --service=doublea-bot \
  --domain=familybot.atticus.tw --project=doubla-family-todo --region=asia-east1
```
然後請使用者在 DNS 商加 CNAME：`familybot → ghs.googlehosted.com`（若 Search Console 未驗證 atticus.tw，先照指令輸出的指示加 TXT 驗證）。等待憑證簽發後以 `https://familybot.atticus.tw/admin?token=...` 驗證。

- [ ] **Step 7: Push**

```bash
git push origin main
```

---

## Self-Review 紀錄

- **Spec coverage**：資料模型→Task 1；路由重構→Task 4；dashboard→Task 5；照片歸檔→Task 2；scope/遷移→Task 3、6；自訂網域→Task 6 Step 6；成本（無新服務、512MB）→ Global Constraints。無缺口。
- **Placeholder scan**：無 TBD/TODO；所有步驟含完整程式碼。
- **Type consistency**：`is_feature_on(feature, chat_id)`、`archive_photo(message_id)`、`handle_album_command(text, reply_token, line_api)`、`get_todo_chat_id()` 在 Task 1/2/4/5 間簽名一致。`_route_event` 回傳 tuple 的形狀在測試與實作一致。
