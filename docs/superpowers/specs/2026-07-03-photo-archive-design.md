# 家族群組照片自動歸檔 Google Drive — 設計文件

日期：2026-07-03
狀態：已確認

## 背景與目標

家族成員出遊時把照片傳到 LINE 群組，看過一次就沉沒，日後難以回溯。
本功能讓 doublea-bot 在指定群組收到照片時，自動下載並歸檔到 Google Drive，
全程零額外成本（不消耗 LINE push 額度、不超出 Drive/Cloud Run 免費額度）。

## 已知限制（設計前提）

- LINE bot 只能收到「加入群組之後」的訊息，無法回溯歷史照片。
- 照片必須在收到 webhook 時立即下載，LINE Content API 保留期有限。
- LINE 傳照片會壓縮並移除 EXIF（含 GPS），地點資訊必須由人提供。

## 架構決策

- **擴充現有 doublea-bot**（Cloud Run 服務 `doublea-bot`，專案 `doubla-family-todo`），
  不開新 bot。重用 LINE channel、Google OAuth、Firestore、部署管線。
- 新模組 `photo_archive.py`，webhook 注入點在 `main.py`。
- 目標群組由環境變數 `ARCHIVE_GROUP_ID` 指定（單一群組，之後由使用者拉 bot
  進新群組後從 log 取得 ID）。此群組**不是**現有 DoubleA 群組或家族群組。

## 資料流

```
家人傳照片到歸檔群組
  → LINE webhook（ImageMessageContent, chat_id == ARCHIVE_GROUP_ID）
  → 以 message_id 呼叫 LINE Content API 下載原圖（免費）
  → 上傳 Google Drive 對應資料夾
  → 結束（不回覆，安靜歸檔）
```

## 資料夾邏輯（事件模式）

- Drive 根資料夾：`LINE相簿/`
- 預設「月份模式」：照片存 `LINE相簿/YYYY-MM/`
- 群組任何人發「**相簿 <地點>**」（例：相簿 菲律賓薄荷島）：
  - 建立 `LINE相簿/YYYY-MM_<地點>/`，進入「事件模式」
  - bot 用 reply API（免費）回一句確認
- 事件模式下收到照片 → 存事件資料夾
- **自動逾時**：距離上一張照片超過 3 天 → 自動退回月份模式（無需結束指令）
- Firestore 狀態文件（`doublea` collection）：
  `{ mode: "monthly" | "event", event_folder: str, event_folder_id: str, last_photo_at: timestamp }`

## 檔案命名與去重

- 檔名：`{YYYYMMDD-HHMMSS}_{message_id}.jpg`
- message_id 全域唯一 → 天然去重、不會覆蓋

## 權限變更

- 現有 scope `drive.readonly` 需升級為 `drive.file`（僅能存取 bot 自建檔案，最小權限）
- **需重跑 `auth_setup.py` 重新授權**，並更新 Cloud Run 的 `GOOGLE_TOKEN_JSON`
- 此為部署前置條件，需在部署 checklist 明確列出

## 與既有功能的互動

- webhook 白名單（`DOUBLEA_GROUP_ID`）目前會擋掉非 DoubleA 群組的一般訊息；
  需調整：來自 `ARCHIVE_GROUP_ID` 的照片訊息與「相簿」指令放行給歸檔模組，
  其餘訊息仍然略過。
- 媽媽照片功能（`is_mom`）優先序不變，且媽媽不在歸檔群組，無交互作用。
- 早安推播（`load_chat_id`）不受影響：歸檔路徑不呼叫 `save_chat_id`。

## 錯誤處理

- Drive 上傳失敗：重試 3 次，仍失敗記 log（含 message_id，可於保留期內事後補救）
- 下載失敗 / 非圖片訊息：記 log 後略過，不中斷其他事件處理

## 成本

| 項目 | 成本 |
|------|------|
| LINE Content API 下載 | 免費 |
| reply API 確認訊息 | 免費（不佔 push 額度） |
| Google Drive 儲存 | 免費 15GB 額度內 |
| Cloud Run | 既有服務，免費額度內，記憶體維持 512MB |

## 測試

- 單元測試：資料夾命名、事件模式狀態機（進入/逾時退出）、檔名產生
- 整合測試：webhook 注入路徑（歸檔群組照片 → 呼叫歸檔；其他群組 → 不呼叫）
- 驗收：實際群組傳照片 → Drive 出現檔案；發「相簿 X」→ 事件資料夾生效
