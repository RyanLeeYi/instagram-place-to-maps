# instagram-place-to-maps

IG / Threads 美食貼文 → 本地 AI pipeline 擷取地點 → Google Places 驗證 → 存進 Google Maps 清單 / Sheets / SQLite，介面是 Telegram bot。

## 啟動與驗證

- 環境恢復：`.\init.ps1`（建 venv、裝依賴、檢查 Ollama 模型與 Playwright）
- 啟動服務：`.\start.ps1`（FastAPI webhook，:8080）；正式運行由 mission-control 服務 `place-to-maps` 管理
- 冒煙測試：`.\.venv\Scripts\python.exe scripts\smoke_pipeline.py`（唯讀，不寫 Maps/Sheets/DB）
- **宣告任何功能完成前，必須先跑過冒煙測試並貼出輸出**

⚠️ 單元測試只有 `tests/test_maps_result.py`（歷史遺留：這個 repo 長期零測試）。新功能照 TDD 補測試，冒煙測試不能取代單元測試。

## 專案結構與邊界

- `app/main.py` — FastAPI + Telegram webhook 進入點
- `app/bot/handlers.py` — 對話流程編排（唯一允許串接多個 service 的地方）
- `app/services/` — 單一職責的外部整合，**service 之間不得互相 import**
- `app/database/` — SQLAlchemy models 與 session；**service 層不得直接寫 DB**，由 handlers 負責
- `app/config.py` — 所有 env 讀取的唯一入口，其他模組不得直接讀 `os.environ`

詳見 `docs/ARCHITECTURE.md`。

## 工作規則

1. 一次只做一個 feature（看 `feature_list.json`，挑第一個 failing）
2. feature 狀態只能 failing → passing，且必須附驗證證據（測試輸出／log 片段）
3. 不做 feature_list 之外的事；發現該做的新事項 → 先加進 list 標 failing，不直接做
4. session 結束前更新 `session-handoff.md`
5. 收工時檢查 `git status` + 未推 commit：有改動就 commit 並 push（remote：https://github.com/RyanLeeYi/instagram-place-to-maps）
6. 寫進檔案的文件長度配合任務所需，不要用填充章節、樣板套話灌水

## 安全紅線

`cookies.txt`、`credentials.json`、`browser_state/`、`.env` 一律不得進 git，log 不得印出 bot token 或 API key。
