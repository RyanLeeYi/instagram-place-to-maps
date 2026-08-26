# Session Handoff

最後更新：2026-08-26
狀態：**F28 passing 並歸檔**，工作區乾淨。主檔只剩 F27 envelope。

## 現況

- 歸檔區 29 條。主檔 failing 只有 **F27 envelope**（合併後端可插拔，底下 F27.3/F27.4 的 slice 條目還沒開）。
- 測試基準：**170 passed / 2 skipped**。
- F28 三段修法（E 實體分類、A 低信心守門、D 行政區查準）已上線，橫跨三個 commit：
  `047f789`（主體）、`f76d0ad`（format=json + schema 漂移救援）、`1614619`（規則 11-13 條件化）。

## F28 收下的兩筆技術債（驗收 fail、主 session DEFER）

1. **#13 單一 commit rollback 不成立**（P3）。三個 commit 在 `merge_backends.py`／`place_extractor.py`
   有檔案交集，單獨 revert `047f789` 會衝突。要修＝squash 三者並 force-push，需 Ryan 授權。
   F27.1 有同型前例。
2. **#14 停止條件**（P4）。驗收者依 `place_extractor.py:141-144` 的註解（prompt 工程 v2-v6 五版）
   判定超過「同一阻塞失敗兩次即停」。流程條款、非成品屬性，不開修復重驗迴圈。

## 降級路徑那件事的結論（別再重查）

qwen3:8b 在「無 Gemini 候選」的降級路徑遇到加長 prompt 會整組拋棄指定 JSON schema
（自創頂層鍵、丟掉 found、輸出簡體）。**純 prompt 工程五版（v2-v6）全滅，不要再試措辭。**

有效的是這三層疊加：
1. `ollama.chat(format="json")`（釘住的 0.3.3 只支援 `Literal['','json']`，不吃 schema dict）
2. `_parse` 的 schema 漂移救援：頂層任何「含 name 的 dict 陣列」都當 places 收下
3. **規則 11-13 條件化**——只在有 Gemini 候選時附加，降級模式回到 F28 前的短 prompt

降級重現：0/3 → 4/6（前兩層）→ **3/3**（加上第三層）。

## 已知但未處理的小瑕疵（不要當新 bug 重複回報）

- `_is_area_name` 的後綴比對是繁體（市場），模型偶爾輸出簡體（市场）會漏過濾。
- f22 案例 (a) 偶爾（3 次中 1 次）把「大溪家鄉臭豆腐」輸出成繁簡兩筆同店重複。
  兩者都在降級／救援路徑，規格沒要求零簡體殘留，暫不修。

## 工具

```
scripts\f22_regression.py [次數]   # 快取材料+本地 ollama，一輪 3 約 30-40 分鐘
scripts\smoke_pipeline.py          # 全 pipeline 真跑約 3-4 分鐘
scratchpad repro_main.py           # 降級路徑重現（gemini_places=None），一次 1-3 分鐘
```

## 給下一個 agent 的坑

- worktree 從 **origin/main** 開，沒有 `.venv`／`.env`／`f22_fixtures`／**`cookies.txt`**。
  派工單要附主工作區直譯器絕對路徑、`TELEGRAM_BOT_TOKEN=dummy-for-tests`、
  **`OLLAMA_MODEL=qwen3:8b`**（沒 .env 時 Settings 預設 qwen2.5:7b，本機沒裝）、複製 fixtures。
  **冒煙測試不要派進 worktree**——`downloader.py:103` 的 `cookies.txt` 是相對 cwd 路徑，
  worktree 沒有，而登入憑證不該複製來複製去。冒煙一律主工作區跑。
- `handlers.py`／`config.py`／`place_extractor.py` 是 UTF-8 BOM + CRLF，用 Edit 工具改。
- `sheets_auth` 偶爾吃 Google 的 `APIError: [503]`，單獨重試
  `GoogleSheetsService()._get_worksheet()` 即可確認是不是暫時性錯誤。

## 等 Ryan 的

- 要不要 squash F28 那三個 commit（需 force-push 授權）。不做也可以，債已記在上面。
- F27.3／F27.4 的 slice 要不要開，還是 F27 envelope 就此收攤。
