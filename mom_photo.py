import hashlib
import hmac as _hmac
import os
import random
import time
import time as _time_module

from google_auth import get_credentials
from googleapiclient.discovery import build as _gapi_build
from linebot.v3.messaging import (
    ImageMessage,
    ReplyMessageRequest,
)

MOM_USER_ID = os.environ.get("MOM_USER_ID", "")
DAUGHTER_PHOTOS_FOLDER_ID = os.environ.get("DAUGHTER_PHOTOS_FOLDER_ID", "")
MOM_PHOTO_COOLDOWN_SEC = int(os.environ.get("MOM_PHOTO_COOLDOWN_SEC", "3600"))
DOUBLEA_PUBLIC_URL = os.environ.get("DOUBLEA_PUBLIC_URL", "")

FIRESTORE_COLLECTION = "doublea"
CAROUSEL_DOC = "mom_photo_carousel"
CAROUSEL_STATE_FILE = "mom_photo_carousel.json"

_last_sent_at: float = 0.0
_carousel_memory: dict = {}  # in-memory cache for carousel state


def _make_photo_url(file_id: str) -> str:
    secret = os.environ.get("PHOTO_SERVE_SECRET", os.environ.get("LINE_CHANNEL_SECRET", ""))
    expires = int(_time_module.time()) + 3600  # 1 hour
    msg = f"{file_id}:{expires}".encode()
    sig = _hmac.HMAC(secret.encode(), msg, hashlib.sha256).hexdigest()
    return f"{DOUBLEA_PUBLIC_URL}/photo/{file_id}?sig={sig}&expires={expires}"


def is_mom(user_id: str) -> bool:
    return bool(MOM_USER_ID) and user_id == MOM_USER_ID


def is_on_cooldown() -> bool:
    global _last_sent_at
    if _last_sent_at == 0.0:
        return False
    return (time.time() - _last_sent_at) < MOM_PHOTO_COOLDOWN_SEC


def mark_sent() -> None:
    global _last_sent_at
    _last_sent_at = time.time()


def build_drive_service(creds):
    return _gapi_build("drive", "v3", credentials=creds)


def _load_carousel_state() -> dict:
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
    query = (
        f"'{folder_id}' in parents "
        f"and (mimeType='image/jpeg' or mimeType='image/png') "
        f"and trashed=false"
    )
    files = []
    page_token = None
    while True:
        kwargs = dict(q=query, fields="nextPageToken,files(id,name,mimeType)", pageSize=200)
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.files().list(**kwargs).execute()
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return files


def get_next_photo_file_id(creds, folder_id: str) -> str:
    global _carousel_memory
    service = build_drive_service(creds)
    files = _list_photo_files(service, folder_id)
    if not files:
        raise ValueError(f"Google Drive 資料夾 {folder_id} 中找不到 JPG/PNG 照片")

    all_ids = [f["id"] for f in files]
    # Merge persisted state with in-memory cache (in-memory wins for queue)
    state = _load_carousel_state()
    if _carousel_memory.get("queue"):
        state = _carousel_memory
    queue: list = list(state.get("queue", []))

    valid = set(all_ids)
    queue = [fid for fid in queue if fid in valid]

    if not queue:
        shuffled = all_ids[:]
        random.shuffle(shuffled)
        last = state.get("last_sent")
        if last and len(shuffled) > 1 and shuffled[0] == last:
            shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
        queue = shuffled

    file_id = queue.pop(0)
    new_state = {"queue": queue, "last_sent": file_id}
    _carousel_memory = new_state
    _save_carousel_state(new_state)
    return file_id


def handle_mom_message(reply_token: str, line_api) -> bool:
    if is_on_cooldown():
        print("[MomPhoto] 冷卻中，略過")
        return True

    try:
        creds = get_credentials()
        file_id = get_next_photo_file_id(creds, DAUGHTER_PHOTOS_FOLDER_ID)
        photo_url = _make_photo_url(file_id)
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
