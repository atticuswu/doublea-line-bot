"""
State persistence:
- 本機開發：使用 chat_state.json
- Cloud Run：使用 Google Firestore
"""
import json
import os
from datetime import datetime

# Cloud Run 會設定 K_SERVICE 環境變數
USE_FIRESTORE = os.environ.get("K_SERVICE") is not None

CHAT_STATE_FILE = os.path.join(os.path.dirname(__file__), "chat_state.json")
FIRESTORE_COLLECTION = "doublea"
FIRESTORE_STATE_DOC = "state"
FIRESTORE_REMINDERS = "reminders"


# ── Firestore backend ─────────────────────────────────────────────────────────

def _get_db():
    from google.cloud import firestore
    return firestore.Client()


def _fs_load_state() -> dict:
    doc = _get_db().collection(FIRESTORE_COLLECTION).document(FIRESTORE_STATE_DOC).get()
    return doc.to_dict() or {}


def _fs_save_state(data: dict) -> None:
    _get_db().collection(FIRESTORE_COLLECTION).document(FIRESTORE_STATE_DOC).set(
        data, merge=True
    )


# ── Local file backend ────────────────────────────────────────────────────────

def _local_load_state() -> dict:
    if os.path.exists(CHAT_STATE_FILE):
        with open(CHAT_STATE_FILE) as f:
            return json.load(f)
    return {}


def _local_save_state(data: dict) -> None:
    state = _local_load_state()
    state.update(data)
    with open(CHAT_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Public API ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    return _fs_load_state() if USE_FIRESTORE else _local_load_state()


def save_state(data: dict) -> None:
    if USE_FIRESTORE:
        _fs_save_state(data)
    else:
        _local_save_state(data)


def load_chat_id() -> str | None:
    return load_state().get("group_chat_id")


def save_chat_id(chat_id: str) -> None:
    save_state({"group_chat_id": chat_id})


def load_last_event() -> dict | None:
    return load_state().get("last_event")


def save_last_event(event_id: str, event_data: dict) -> None:
    save_state({
        "last_event": {
            "id": event_id,
            "title": event_data["title"],
            "start": event_data["start"],
            "end": event_data["end"],
        }
    })


# ── Reminder persistence (Firestore only; local falls back to in-memory) ─────

_local_reminders: list = []


def add_reminder(chat_id: str, event_data: dict, reminder_dt: datetime) -> None:
    payload = {
        "chat_id": chat_id,
        "title": event_data["title"],
        "start": event_data["start"],
        "reminder_time": reminder_dt.isoformat(),
        "sent": False,
    }
    if USE_FIRESTORE:
        doc_id = f"{event_data['start']}_{chat_id}".replace(":", "-").replace("+", "p")
        _get_db().collection(FIRESTORE_REMINDERS).document(doc_id).set(payload)
    else:
        payload["_id"] = len(_local_reminders)
        _local_reminders.append(payload)


def get_due_reminders(now: datetime) -> list:
    if USE_FIRESTORE:
        docs = (
            _get_db()
            .collection(FIRESTORE_REMINDERS)
            .where("sent", "==", False)
            .stream()
        )
        due = []
        for doc in docs:
            data = doc.to_dict()
            data["_doc_id"] = doc.id
            if datetime.fromisoformat(data["reminder_time"]) <= now:
                due.append(data)
        return due
    else:
        return [
            r for r in _local_reminders
            if not r["sent"] and datetime.fromisoformat(r["reminder_time"]) <= now
        ]


def mark_reminder_sent(doc_id) -> None:
    if USE_FIRESTORE:
        _get_db().collection(FIRESTORE_REMINDERS).document(doc_id).update({"sent": True})
    else:
        for r in _local_reminders:
            if r.get("_id") == doc_id:
                r["sent"] = True
