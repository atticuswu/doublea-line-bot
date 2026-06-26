"""
執行一次即可：在本機取得 Google OAuth 授權，產生可上傳到 Railway 的 token。

使用方式：
1. 先把 credentials.json 放在這個資料夾
2. uv run auth_setup.py
3. 瀏覽器會自動開啟，用 atticus.wu@gmail.com 登入並授權
4. 把輸出的 base64 字串貼到 Railway 的 GOOGLE_TOKEN_JSON 環境變數
"""

import base64
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }

    # Save locally for reference
    with open("token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    encoded = base64.b64encode(json.dumps(token_data).encode()).decode()

    print("\n✅ 授權成功！")
    print("\n" + "=" * 60)
    print("GOOGLE_TOKEN_JSON（貼到 Railway 環境變數）：")
    print("=" * 60)
    print(encoded)
    print("=" * 60 + "\n")
    print("⚠️  token.json 和 credentials.json 不要 commit 到 git！")


if __name__ == "__main__":
    main()
