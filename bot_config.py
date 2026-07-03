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
