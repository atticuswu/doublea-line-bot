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
