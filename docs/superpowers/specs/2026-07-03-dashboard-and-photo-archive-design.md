# DoubleA Bot 功能 Dashboard + 照片自動歸檔 — 整合設計文件

日期：2026-07-03
狀態：已確認
取代：`2026-07-03-photo-archive-design.md`（照片歸檔部分併入本文件，群組綁定方式由環境變數改為 Firestore config）

## 背景與目標

doublea-bot 已有 3 個功能（待辦+早安推播、媽媽照片回覆、即將新增的照片歸檔），
分別對應不同群組，目前靠環境變數硬編碼綁定（`DOUBLEA_GROUP_ID`、`MOM_USER_ID`），
每次調整都要改 code + 重新部署。未來功能會增加到 5–8 個，需要：

1. **Dashboard**：一頁看清「功能 × 群組 × 開關」矩陣，網頁上直接調整，立即生效
2. **照片歸檔**：指定群組的照片自動存 Google Drive（含出遊事件資料夾）
3. 全程零額外成本

## 一、資料模型（Firestore）

`doublea/config` 文件：

```
{
  features: {
    todo:          { enabled: true,  chat_ids: ["C6d18ccad490e34abb7cafd01676b0502"] },
    mom_photo:     { enabled: true,  chat_ids: [] },   # 以 sender user_id 判斷，不限群組
    photo_archive: { enabled: false, chat_ids: [] },
  },
  known_chats: {
    "<chat_id>": { name: "<群組名稱>", type: "group" | "room" | "user", note: "<手動備註>" },
  },
  mom_user_id: "Uc6f4be62dce38f87dbab76bc3d26ecc1",
}
```

- `chat_ids` 為陣列，一個功能可綁多個群組；一個群組可掛多個功能
- webhook 每次收到事件即登記 chat_id 到 `known_chats`（group 透過 API 抓名稱；
  room 拿不到名稱，留空由使用者在 dashboard 備註）
- config 讀取加 60 秒記憶體快取，避免每則訊息都讀 Firestore

## 二、Webhook 路由重構

以統一路由取代現有硬編碼判斷（`is_mom` env var、`DOUBLEA_GROUP_ID` 白名單）：

```
收到事件 → 登記 known_chats
  → mom_photo 啟用 且 sender == mom_user_id？ → 回女兒照片（既有邏輯不變）
  → photo_archive 啟用 且 chat_id ∈ 綁定清單 且（圖片訊息 或「相簿」指令）？ → 歸檔
  → todo 啟用 且 chat_id ∈ 綁定清單 且 文字訊息？ → process_message
  → 其他 → 略過
```

- 環境變數 `DOUBLEA_GROUP_ID`、`MOM_USER_ID` 退役；部署時執行一次性初始化腳本，
  把現有綁定寫入 config，行為無縫接續
- `save_chat_id` / `load_chat_id`（早安推播目標）改由 `features.todo.chat_ids[0]` 提供，
  webhook 不再有覆寫目標群組的副作用

## 三、Dashboard

- 自訂網域：`familybot.atticus.tw`（Cloud Run domain mapping，asia-east1 支援，
  免費 + 自動 TLS）。DNS 加 CNAME `familybot → ghs.googlehosted.com`，
  需先在 Search Console 驗證 atticus.tw 所有權
- LINE webhook 繼續使用原 `run.app` 網址，兩網址並存
- 路徑：`GET /admin?token=<ADMIN_TOKEN>`（env var 長隨機字串；錯誤 token → 403）
- 單一 HTML 頁由 FastAPI 直接回傳（無前端框架、無 build）：
  - 功能矩陣：每功能一列，綁定群組（checkbox 多選，選項來自 known_chats）+ 開關
  - 已知群組清單：可編輯備註
- `POST /admin/config`（同 token 驗證）寫回 Firestore，立即生效（快取 60 秒內失效）

## 四、照片歸檔

### 已知限制（設計前提）

- bot 只能收到加入群組之後的訊息，無法回溯歷史照片
- 照片必須在 webhook 當下下載（LINE Content API 保留期有限）
- LINE 會壓縮照片並移除 EXIF/GPS，地點須由人提供

### 資料流

```
綁定群組收到照片 → Content API 以 message_id 下載原圖（免費）
  → 上傳 Drive 對應資料夾 → 結束（安靜歸檔，不回覆）
```

### 資料夾邏輯（事件模式）

- Drive 根資料夾：`LINE相簿/`
- 預設月份模式：`LINE相簿/YYYY-MM/`
- 群組任何人發「**相簿 <地點>**」→ 建 `LINE相簿/YYYY-MM_<地點>/` 進入事件模式，
  bot 以 reply API（免費）回覆確認
- 事件模式下照片存事件資料夾；距上一張照片超過 3 天自動退回月份模式
- 事件狀態存 Firestore：`{ mode, event_folder, event_folder_id, last_photo_at }`

### 檔名與去重

`{YYYYMMDD-HHMMSS}_{message_id}.jpg` — message_id 全域唯一，天然去重

### 權限

- scope 由 `drive.readonly` 升級為 `drive.file`（僅存取 bot 自建檔案）
- **需重跑 `auth_setup.py` 重新授權**並更新 Cloud Run `GOOGLE_TOKEN_JSON`
  （部署 checklist 必列，部署前明確提醒使用者操作）

### 錯誤處理

- Drive 上傳失敗：重試 3 次，仍失敗記 log（含 message_id 供保留期內補救）
- 下載失敗 / 非預期訊息型別：記 log 略過，不中斷其他事件

## 五、成本

| 項目 | 成本 |
|------|------|
| Dashboard（既有 Cloud Run endpoint） | 0 |
| 自訂網域 domain mapping + TLS | 0 |
| Firestore 讀寫（含 60s 快取） | 免費額度內 |
| LINE Content API 下載 / reply API | 免費 |
| Google Drive 儲存 | 免費 15GB 額度內 |
| Cloud Run（記憶體維持 512MB） | 免費額度內 |

## 六、風險與遷移

- 最大風險：路由重構動到現有三條功能路徑。遷移順序：先寫入 config →
  再切換程式讀 config → 驗證三功能正常 → 移除舊環境變數
- `ADMIN_TOKEN` 外洩：可改設定但不能讀照片/發訊息，風險可接受，token 可隨時更換
- 單元測試覆蓋：路由分派（三功能 × 開/關 × 綁定內/外群組）、事件模式狀態機、
  資料夾與檔名產生、admin token 驗證

## 七、驗收

1. Dashboard 開啟顯示功能矩陣，關閉 todo 後在 DoubleA 群組發文字不再回應，開啟後恢復
2. 拉 bot 進新群組 → 群組出現在 known_chats → dashboard 綁定 photo_archive 並啟用
3. 群組傳照片 → Drive `LINE相簿/YYYY-MM/` 出現檔案
4. 發「相簿 菲律賓薄荷島」→ bot 回確認 → 後續照片進事件資料夾
5. 媽媽照片、早安推播行為與現況一致
