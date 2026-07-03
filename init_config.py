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
