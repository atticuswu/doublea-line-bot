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
