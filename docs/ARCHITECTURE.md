# ARCHITECTURE

## 資料流

```
Telegram 訊息（IG / Threads 連結）
  → app/main.py   FastAPI webhook 收 update
  → app/bot/handlers.py   判斷連結型別、編排以下 service
      ├─ downloader        yt-dlp（影音）／instaloader（圖文）／Googlebot SSR（Threads）
      ├─ transcriber       faster-whisper 本地轉錄
      ├─ visual_analyzer   ffmpeg 抽幀 → Ollama vision 逐幀描述
      ├─ place_extractor   Ollama LLM 產出結構化 PlaceInfo（可多筆）
      ├─ google_places     Places API 驗證地址／評分／place_id
      ├─ google_sheets     gspread 追加一列
      └─ google_maps_saver Playwright headless 存進 Maps 清單
  → app/database/models.py   寫入 SQLite places 表
  → Telegram 回覆
```

## 各層職責

| 層 | 檔案 | 職責 |
|----|------|------|
| 進入點 | `app/main.py` | FastAPI 生命週期、webhook 路由、Telegram Application 組裝、`/health` |
| 編排 | `app/bot/handlers.py` | 對話流程、進度訊息、錯誤回覆、串接 service、寫 DB |
| 整合 | `app/services/*.py` | 每支一個外部依賴，回傳自己的 dataclass 結果物件 |
| 資料 | `app/database/models.py` | SQLAlchemy async model 與 session |
| 設定 | `app/config.py` | 讀 `.env` 的唯一入口（pydantic settings）＋ `runtime_settings.json` 執行期設定 |

## 邊界規則

1. **service 之間不得互相 import** — 需要串接就回到 `handlers.py`
2. **service 不得寫 DB** — service 只回傳結果物件，持久化由 handlers 負責
3. **只有 `app/config.py` 讀 env** — 其他模組一律從 `settings` 取值，不直接碰 `os.environ`
4. **失敗要能診斷** — 每個 service 的結果物件都要能用**同一個欄位名** `error_message` 問出失敗原因，終局失敗路徑必須 `logger.error`
   `ListsResult`／`SaveResult` 的儲存欄位是 `message`（成功訊息也用它），另以 `error_message` property 對外統一：失敗回 message、成功回 None。新增 service 請沿用 `error_message` 這個名字，別再開新命名

## 外部依賴與憑證

| 依賴 | 憑證／狀態檔 | 失效徵兆 |
|------|--------------|----------|
| Instagram 下載 | `cookies.txt` | yt-dlp 回 empty media response（也可能是 yt-dlp 版本太舊） |
| Google Places / Sheets | `.env` 的 API key、`credentials.json` | 401／quota 錯誤 |
| Google Maps 存清單 | `browser_state/google_auth.json` | 找不到儲存選單、清單讀回空陣列 |
| Ollama | 本機 :11434 | 連線被拒；模型未 pull |

憑證全部在 `.gitignore` 內，不得進 git。

## 已知脆弱點

- **Playwright 存 Maps** 依賴 Google 的 DOM 與登入 session，UI 改版或 session 過期就壞，且屬於可選功能（主流程不受影響）→ 必須自己有告警，否則靜默壞掉沒人知道
- **instaloader** 受上游 IG 改版影響（參考姊妹專案 instagram-reels-summarizer 的 instaloader#2710）
- **單元測試覆蓋率極低** — 只有 `tests/test_maps_result.py`（結果物件的錯誤欄位一致性），service 層與 handlers 完全沒有測試；端到端靠 `scripts/smoke_pipeline.py`。新功能要照 TDD 補上
