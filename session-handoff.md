# Session Handoff

最後更新：2026-08-31
狀態：**F27.4 依裁示縮成兩家、驗收 8 pass / 1 fail（已修）/ 1 unverified / 1 untestable，仍是 failing**。
唯一擋著收官的是 acceptance #9「rollback 單一 commit」，那要 Ryan 給 force-push 授權才動得了，已投 brief-me。

## 現況

- **2026-08-31 插隊收官 F29**（Telegram `/mergebackends` 運行中切換合併後端鏈，持久化到 runtime_settings.json）：一輪驗收 10/10 過、已歸檔。測試基準升為 **221 passed / 2 skipped**。合法名稱單一來源是 `merge_backends.py:SUPPORTED_MERGE_BACKENDS`。F27.2「鏈只讀 env」的決定已被推翻，`PlaceExtractor.extract()` 每次讀鏈、字串沒變不重建。**服務需經 mission-control 重啟才載入**；重啟前先殺殘留的舊 uvicorn（08-31 09:43 撞埠 10048 三次）。同日 `/start`／`/help` 補列 `/mergemode`。
- F27.4 狀態不變（下文 08-28 內容仍有效）。

- 歸檔區 31 條。主檔 failing 兩條：**F27 envelope** 與 **F27.4**。
- 測試基準：**212 passed / 2 skipped**（F27.4 動工前 188/2；上一輪三家版是 220/2，
  砍掉 agy-api 帶走 8 條參數化測試是裁示的直接後果，不是回歸）。
- f22_regression 3：兩案例各 **3/3**。smoke_pipeline 七段 **全 PASS**（142s）。

## 本輪做了什麼

消化 brief-me 答案 `3d5f020a`——Ryan 選了三個選項裡的**「砍掉 agy-api，F27.4 縮成兩家」**
（不是原本建議的「維持現況」）。

- 規格同輪改成兩家（claude-api / codex-api）並重新簽核，決議理由寫進 acceptance 原文。
  F27 envelope 的 outcome 也一併改：agy 只有 CLI 版，沒有 SDK 版。
- 移除 `AgyApiMergeBackend`、`get_backend` 的 agy-api 分支、`config.py` 的
  `GEMINI_API_KEY` 與 `GEMINI_API_MODEL` 兩個欄位、`.env.example` 條目、
  `requirements.txt` 的 google-genai 註解、測試的 agy 參數化項目。
- `get_backend("agy-api")` 現在跟任何不認得的名字一樣丟 `UnsupportedMergeBackendError`，
  並有專門的參數化測試釘住。
- 合併階段要用 Gemini 就走 F27.3 的 `agy` CLI（訂閱憑證）。理由與未來重新加回的
  入場條件寫在 vault `DECISIONS.md` D10 的「結果」段。

## 逐條狀態（acceptance-verifier fresh context 判定）

| # | 內容 | 判定 |
|---|---|---|
| scope | 不動讀影片那格 / _reconcile / F22 標準 | PASS（三項皆未列在變更檔案裡） |
| 1 | 兩個 key 欄位、留空即停用、不洩漏 key | PASS |
| 2 | get_backend 認兩個 -api 名稱、agy-api 無可用路徑 | PASS |
| 3 | requirements.txt 釘兩個 SDK、init.ps1 未改 | PASS |
| 4 | 逾時、401/429/5xx → MergeFailure | PASS |
| 5 | JSON 解析共用不複製 | PASS（該檔 0 筆 `json.loads`／`re.search`） |
| 6 | 兩後端各以假 client 測四種情境 | PASS |
| 7 | 真 API 冒煙 | **FAIL → 已修**：兩把 key 都沒設，全標未驗；驗收者抓到的是 handoff 本身沒同步，就是這份檔案，已改寫 |
| 8 | pytest 不低於基準、f22 各 3/3 | PASS（212/2 >= 188/2） |
| 9 | rollback 單一 commit | **unverified，卡住**，見下 |
| 10 | 停止條件 | untestable（流程條款） |

## 第 9 條為什麼卡住（**已投 brief-me，等 Ryan 選**）

F27.4 現在橫跨**兩個 commit**：`7f1d80c`（三家版，已 push 到 origin/main）加上本次縮減。
裁示是在第一個 commit 推上去之後才到的，所以「單一 commit」在不 force-push 的前提下做不到。

實際驗過的事實：兩個 commit 一起 revert 之後，工作樹回到 `aec720a`（F27.3 收官點）——
**可回復性本身沒有問題，不成立的只有「單一」這個字**。

三個選項已投進 brief-me。**沒有自己改 acceptance 讓它變綠**——第 8 條的測試數是裁示的
必然後果（沒有別的可能），改它只是記錄事實；第 9 條有 force-push 這條現成的替代路，
那是授權問題，不是我的判斷。

## 證據

```
pytest                    212 passed / 2 skipped（基準 188/2）
f22_regression.py 3       (a) 北投市場 3/3；(b) 酒場 清志郎 3/3
                          驗收者另獨立跑 1 輪抽驗，兩案例皆 PASS
smoke_pipeline.py         142s，七段全 PASS
                          （上一輪的 sheets_auth 503 這次沒再出現，確認是暫時性）
零金鑰降級實跑            notes = claude-api：未設定 ANTHROPIC_API_KEY；
                          codex-api：未設定 OPENAI_API_KEY
agy-api 已無路徑          get_backend('agy-api') -> UnsupportedMergeBackendError
                          「不支援的合併後端 'agy-api'；目前支援 'ollama'、'agy'、
                          'claude'、'codex'、'claude-api'、'codex-api'」
釘版未受影響              fastapi 0.115.0 / starlette 0.38.6 /
                          pydantic 2.8.2 / httpx 0.27.0（與 HEAD 一致）
```

### 砍成兩家之後測試仍然咬得住（三個 mutation）

| 改壞什麼 | 結果 |
|---|---|
| `_redact` 變 no-op | 2 failed |
| 拿掉缺 key 的守門 | 3 failed |
| 改用自己複製的簡化解析 | 3 failed（含「共用 parse_merge_response」那條） |

砍掉三分之一的參數化項目沒有讓套件變成空綠燈。

## 給下一個 agent 的坑

- worktree 從 **origin/main** 開，沒有 `.venv`／`.env`／`f22_fixtures`／**`cookies.txt`**。
  派工單要附主工作區直譯器絕對路徑、`TELEGRAM_BOT_TOKEN=dummy-for-tests`、
  **`OLLAMA_MODEL=qwen3:8b`**、複製 fixtures。冒煙一律主工作區跑
  （`downloader.py:103` 的 `cookies.txt` 是相對 cwd 路徑）。
- `handlers.py`／`config.py`／`place_extractor.py` 是 UTF-8 BOM + CRLF，
  **`requirements.txt` 與 `.env.example` 也是**。Edit 工具會把整檔轉成 LF——
  改完一定回頭數 `\r\n` 與 `\n` 的數量是否相等。
- `sheets_auth` 偶爾吃 Google 的 `APIError: [503]`，單獨重試
  `GoogleSheetsService()._get_worksheet()` 即可確認是不是暫時性錯誤。
- 哪天真的要加回 Gemini SDK：先跑 `pip install --dry-run` 看它會動到哪些既有釘版
  （`google-antigravity` 會拉 starlette 1.x 撞死 fastapi；`google-genai` 會動
  pydantic 與 httpx），裝完一定重跑完整測試套件與冒煙。判準寫在 vault DECISIONS D10。

## 已知瑕疵（不要當新 bug 重複回報）

- 鏈全敗時 notes 會出現重複的後端名（`claude-api：claude-api：未設定 ...`）。
  來源是 `_failure_notes` 加一次前綴、後端自己的訊息也帶一次。**CLI 版（F27.3）
  早就是這樣**，屬跨 slice 的既有外觀問題，留給之後的清理條目。
- `_is_area_name` 的後綴比對是繁體，模型偶爾輸出簡體會漏過濾。
- f22 案例 (a) 偶爾把「大溪家鄉臭豆腐」輸出成繁簡兩筆同店重複（本輪 3 次裡中 2 次，
  仍判 PASS，因為驗收標準看的是六家有沒有到齊）。

## 等 Ryan 的

1. **F27.4 的 rollback 怎麼算**（brief-me 新卡）——這條決定 F27.4 能不能改 passing。
   選項：授權 force-push 壓成單一 commit／接受兩個 commit 並改 acceptance 第 9 條／維持 failing。
2. 要不要 squash F28 那三個 commit（需 force-push 授權）。與第 1 點是同一個授權問題，
   可以一起答。
3. **F27 envelope 可以收了**：F27.1-F27.4 四個 slice 的實作都完成，只等 F27.4 改 passing。

## 工具

```
scripts\f22_regression.py [次數]   # 快取材料+本地 ollama，一輪 3 約 30-40 分鐘
scripts\smoke_pipeline.py          # 全 pipeline 真跑約 2-4 分鐘
```
