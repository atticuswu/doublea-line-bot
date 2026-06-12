# DoubleA Family Bot

一個部署在 Google Cloud Run 上的 LINE 群組機器人，自動管理家庭行事曆與待辦清單。

---

## 功能概覽

- **智能分類**：直接說出事情，Bot 自動判斷要加入行事曆還是待辦清單
- **行事曆**：建立 Google Calendar 事件、邀請指定人員、附上分享連結
- **待辦清單**：記錄到 Google Tasks，支援完成標記
- **每日早安摘要**：每天 8:00 推送今日行事曆 + 待辦清單
- **活動前提醒**：活動開始前 2 小時自動提醒
- **分享連結**：每個行事曆事件自動附帶「任何人點擊即可加入」的連結，不需對方 email

---

## 技術架構

| 元件 | 技術 |
|------|------|
| 訊息接收 | LINE Messaging API（Webhook） |
| 智能分類 | Gemini 2.5 Flash（JSON mode） |
| 行事曆 | Google Calendar API |
| 待辦事項 | Google Tasks API |
| 狀態儲存 | Google Cloud Firestore |
| 提醒排程 | Google Cloud Scheduler |
| 後端 | Python + FastAPI，部署於 Cloud Run |

---

## 事前準備

1. **LINE Developer 帳號**（免費）
2. **Google Cloud 帳號**（需要信用卡，費用約 $0.5 USD/月）
3. **本機環境**：Python 3.11+、`gcloud` CLI、git

---

## 完整安裝步驟

### Step 1｜LINE Developer 設定

#### 1-1 建立 Channel

1. 前往 [developers.line.biz](https://developers.line.biz/) 用 LINE 帳號登入
2. **Create a new provider** → 輸入名稱（如 `My Family`）
3. **Create a new channel** → 選 **Messaging API**
4. 填寫基本資料後建立

#### 1-2 取得憑證

在 Channel 設定頁面：

- **Basic settings** → 複製 `Channel secret`
- **Messaging API** → 點 **Issue** 產生 `Channel access token (long-lived)`

#### 1-3 開啟加入群組功能

**Messaging API** → **Allow bot to join group chats** → 開啟

---

### Step 2｜Google Cloud 設定

#### 2-1 建立 GCP 專案並啟用 API

```bash
# 登入 gcloud
gcloud auth login

# 建立專案（替換 YOUR_PROJECT_ID）
gcloud projects create YOUR_PROJECT_ID --name="DoubleA Bot"
gcloud config set project YOUR_PROJECT_ID

# 連結帳單（必須，才能使用 Cloud Run）
# 前往 console.cloud.google.com/billing 手動設定

# 啟用所有需要的 API
gcloud services enable \
  calendar-json.googleapis.com \
  tasks.googleapis.com \
  firestore.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  generativelanguage.googleapis.com \
  --project=YOUR_PROJECT_ID
```

#### 2-2 建立 Firestore 資料庫

```bash
gcloud firestore databases create \
  --location=asia-east1 \
  --project=YOUR_PROJECT_ID
```

#### 2-3 設定 Cloud Run 服務帳號權限

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/cloudbuild.builds.builder"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/storage.admin"
```

#### 2-4 建立 OAuth 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials**
2. **Create Credentials** → **OAuth client ID**
3. 若需先設定同意畫面：
   - **OAuth consent screen** → External → 填好基本資料
   - Scopes：加入 `Google Calendar API` 和 `Google Tasks API`
   - **⚠️ 重要：完成後點「發布應用程式」發布到正式版，否則 token 7 天後會失效**
4. 回到 Credentials → **Create Credentials** → **OAuth client ID**
   - Application type：**Desktop app**
5. 下載 JSON → 改名為 `credentials.json` → 放到專案資料夾

#### 2-5 本機授權（只做一次）

> ⚠️ 必須在 OAuth App 發布到正式版之後才執行，否則 7 天後 token 會過期

```bash
cd line-bot
python3 auth_setup.py
```

瀏覽器開啟後用你的 Google 帳號登入並允許授權。
完成後終端機會輸出一段 base64 字串，複製備用（Step 4 要用）。

---

### Step 3｜設定環境變數

複製 `.env.example` 為 `.env`：

```bash
cp .env.example .env
```

編輯 `.env`，填入所有值：

```env
LINE_CHANNEL_SECRET=你的_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=你的_channel_access_token
GEMINI_API_KEY=你的_gemini_api_key
GOOGLE_TOKEN_JSON=auth_setup.py_輸出的_base64_字串
```

> **取得 Gemini API Key**：前往 [Google AI Studio](https://aistudio.google.com/app/apikey) 建立

---

### Step 4｜部署到 Google Cloud Run

```bash
# 首次部署
gcloud run deploy doublea-bot \
  --source . \
  --region asia-east1 \
  --project YOUR_PROJECT_ID \
  --set-env-vars "LINE_CHANNEL_SECRET=XXX,LINE_CHANNEL_ACCESS_TOKEN=XXX,GEMINI_API_KEY=XXX,GOOGLE_TOKEN_JSON=XXX" \
  --min-instances 1 \
  --allow-unauthenticated

# 取得服務 URL
gcloud run services describe doublea-bot \
  --region asia-east1 \
  --project YOUR_PROJECT_ID \
  --format="value(status.url)"
```

你的 Webhook URL 是：`https://YOUR_SERVICE_URL/webhook`

---

### Step 5｜設定 Cloud Scheduler

```bash
SERVICE_URL="https://YOUR_SERVICE_URL"

# 每天 08:00 早安摘要
gcloud scheduler jobs create http doublea-morning-briefing \
  --schedule="0 8 * * *" \
  --time-zone="Asia/Taipei" \
  --uri="${SERVICE_URL}/morning-briefing" \
  --http-method=POST \
  --location=asia-east1 \
  --project=YOUR_PROJECT_ID

# 每 15 分鐘檢查活動前提醒
gcloud scheduler jobs create http doublea-check-reminders \
  --schedule="*/15 * * * *" \
  --time-zone="Asia/Taipei" \
  --uri="${SERVICE_URL}/check-reminders" \
  --http-method=POST \
  --location=asia-east1 \
  --project=YOUR_PROJECT_ID
```

---

### Step 6｜設定 LINE Webhook

1. 回到 LINE Developers → Messaging API 頁面
2. **Webhook URL** → 填入 `https://YOUR_SERVICE_URL/webhook`
3. 點 **Verify** → 顯示 `Success`
4. 開啟 **Use webhook** 開關
5. 關閉 **Auto-reply messages**（否則會干擾 Bot 回覆）

---

### Step 7｜加入群組

1. LINE Developers → **Messaging API** → 用 QR Code 加 Bot 為好友
2. 建立 LINE 群組，把 Bot 加進群組
3. 發測試訊息：「明天下午3點看牙醫」
4. Bot 應回覆行事曆確認訊息

---

## 環境變數說明

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_SECRET` | LINE Channel secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel access token |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GOOGLE_TOKEN_JSON` | Google OAuth token（base64，由 auth_setup.py 產生） |

---

## 更新部署

修改程式碼後，重新部署只需：

```bash
gcloud run deploy doublea-bot --source . --region asia-east1 --project YOUR_PROJECT_ID
```

---

## 排除問題

### 行事曆寫入失敗

通常是 OAuth token 過期。重新執行授權：

```bash
python3 auth_setup.py
# 取得新的 base64 字串後更新 Cloud Run
gcloud run services update doublea-bot \
  --region=asia-east1 \
  --project=YOUR_PROJECT_ID \
  --update-env-vars="GOOGLE_TOKEN_JSON=新的字串"
```

> ⚠️ 如果 OAuth App 未發布到「正式版」，token 每 7 天就會過期。請到 GCP Console → APIs & Services → OAuth consent screen → 點「發布應用程式」

### Webhook Verify 失敗

確認 Cloud Run 服務正在運行：

```bash
curl https://YOUR_SERVICE_URL/health
# 應回傳 {"status":"ok","bot":"DoubleA","env":"cloud"}
```

### Bot 在群組沒有回應

- 確認 LINE Developers 的 Webhook 已開啟
- 確認 Bot 已加入群組（不只是好友）
- 查看 Cloud Run logs：`gcloud logging read 'resource.type="cloud_run_revision"' --project=YOUR_PROJECT_ID --limit=20`

---

## 使用說明

詳見 `LINE_使用說明.txt`，可直接貼到 LINE 群組的記事本供所有成員參考。

---

## 安全注意事項

以下檔案包含敏感資訊，**絕對不能** commit 到 git：

- `.env`（API keys）
- `credentials.json`（Google OAuth client credentials）
- `token.json`（Google OAuth token）
- `chat_state.json`（LINE 群組 ID）

這些都已列在 `.gitignore` 中。
