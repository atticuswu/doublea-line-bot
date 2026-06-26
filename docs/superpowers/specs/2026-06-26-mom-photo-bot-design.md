# Mom Photo Bot — Design Spec

**Date:** 2026-06-26  
**Status:** Approved  
**Project:** DoubleA family LINE bot (`~/Documents/daily-photo-post/line-bot/`)

---

## 1. 目標

當媽媽在家庭 LINE 群組發送任何訊息時，bot 自動回傳一張女兒的照片。純雲端，不依賴本機電腦。

---

## 2. 架構

擴充現有 DoubleA bot，新增 `mom_photo.py` 模組。不建立新的 LINE channel，不新增 Cloud Run 服務。

```
媽媽在群組發任何訊息（文字/圖片/影片/連結）
    → DoubleA Webhook（Cloud Run）
        → mom_photo.py：發話者是 MOM_USER_ID？
            → 是，且冷卻期內（< 1 小時）→ 靜音，不回應
            → 是，且冷卻期外 → 從 Google Drive 資料夾取下一張照片
                → Cloud Run /photo/{file_id} 串流圖片
                → LINE reply image message 給群組
                → 更新冷卻時間戳記 + 輪播進度
            → 否 → 走原本 DoubleA handler（行事曆/待辦）
```

---

## 3. 照片儲存

- **位置：** Google Drive 資料夾（由 Atticus 指定資料夾 ID）
- **上傳方式：** 手機直接拖入，家人也可投稿
- **格式：** JPG / PNG（HEIC 轉換由程式處理）
- **輪播邏輯：** 洗牌後依序發送，跑完一輪重新洗牌（不重複）
- **狀態持久化：** Drive 資料夾內的 `_state.json`（記錄洗牌順序 + 當前 index），Cloud Run 重啟後輪播進度不遺失

---

## 4. LINE 圖片代理

LINE 伺服器需要公開 HTTPS URL 才能顯示圖片。解法：Cloud Run 開 `GET /photo/{file_id}` endpoint，收到請求時從 Google Drive 下載並串流回去。Atticus 不需要操作任何額外設定，只要指定 Drive 資料夾即可。

---

## 5. 冷卻機制

- **冷卻時間：** 1 小時
- **實作：** 記憶體內的 `last_sent_at` timestamp
- **重啟行為：** Cloud Run 重啟後冷卻重置（可接受，最多多送一張照片）
- **連續發文：** 媽媽連傳多張圖或訊息，只回一次

---

## 6. 環境變數

| 變數 | 說明 |
|------|------|
| `MOM_USER_ID` | 媽媽的 LINE userId |
| `DAUGHTER_PHOTOS_FOLDER_ID` | Google Drive 資料夾 ID |
| `MOM_PHOTO_COOLDOWN_SEC` | 冷卻秒數（預設 3600） |

`MOM_USER_ID` 取得方式：部署後媽媽發一則訊息，從 Cloud Run log 讀取她的 userId。

---

## 7. Google Drive 授權

沿用 DoubleA bot 現有的 Google 服務帳號（`credentials.json` 已在 repo），只需把 Drive 資料夾分享給該服務帳號 email。

---

## 8. 新增檔案

- `mom_photo.py` — 主模組（Drive 存取、輪播邏輯、冷卻判斷）
- `main.py` — 加入 `/photo/{file_id}` endpoint + 在 webhook handler 注入 mom_photo 邏輯
- `requirements.txt` — 加入 `google-api-python-client`（若未有）

---

## 9. 不在範圍內

- Threads、Substack 等發布平台
- 主動 push（全程用 reply，不耗 200 則 push 額度）
- 媽媽的指令解析（她不下指令）
