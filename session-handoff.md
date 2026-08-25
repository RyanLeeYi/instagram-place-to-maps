# Session Handoff

最後更新：2026-08-25
HEAD：`910843a`（已 push）+ 本次收官 commit

## 現況

**`feature_list.json` 零條 failing。** F22 與 F25 都在本 session 通過驗收並歸檔到
`docs/archive/features.jsonl`（現 25 條）。

測試 119 passed / 2 skipped（那 2 條是需要網路的 Threads 轉址實機檢查，
`RUN_NETWORK_TESTS=1` 才跑）。冒煙測試七段全過。

## 本次做完的事

**F22 — 本地 + Gemini 雙來源合併**

判斷權從 `qwen3:8b` 移到 agy，用程式規則而非提示詞。`place_extractor._reconcile()`：

- agy 標「只是畫面帶到」的候選 → 直接剔除，8b 不能推翻
- agy 標「影片主角」但 8b 漏掉的 → 直接補回
- 區域名（市場／夜市／商圈／老街／周邊結尾）→ 一律不算店家

同時把抽幀描述從**所有**含影片的路徑移除（IG reel、IG 影片貼文、
Threads 單影片、Threads `thread_mixed`）。圖片路徑仍用 `analyze_images`。

**F25 — Threads `/share/` 短連結**

`THREADS_URL_PATTERN` 加上 `share` 路徑段；轉址在 `message_handler` 裡、
**去重查詢之前**執行。順序很重要：去重拿貼文 ID 比對，短連結那串 ID 不是貼文 ID。

## 回歸工具

```
scripts\f22_regression.py [次數]     # 預設 3；走快取材料，不呼叫 agy、不連網
```

材料在 `temp_videos/f22_fixtures/*_materials.json`（gitignored）。
兩個案例的正確答案是 Ryan 2026-08-25 看過影片逐項確認的，不是模型推測。

## 等 Ryan 裁示（四件，都還沒開條目）

1. **Google Places 比對品質** — 冒煙測試顯示「巫婆水餃店 台北」比對到「芳芳江蘇水餃」，
   完全不同家。擷取階段今天調準了，卻在最下游被換掉。優先度我排第一
2. **F26 agy 重試** — 草稿已給 Ryan，未簽核。實測約 16 次呼叫 8 次失敗、六種模式
3. **`scripts/smoke_pipeline.py` 已失真** — 它直接呼叫各 service 繞過 handlers，
   輸出還是「視覺分析 幀數=10」且沒有 agy。綠燈不代表出貨的東西是好的
4. **F27 合併階段可插拔後端（envelope + 4 slice）** — 草稿已給，未簽核。
   Ryan 選了多後端方案；我的意見是它修的是目前不痛的那格，但這是作品集，門面是正當理由

## 給下一個 agent 的三個坑

**agy 不可靠，而且沒有替代品。** 今天累計約 16 次呼叫 8 次失敗，六種模式：
權限被拒、自己跑去開 shell、連線中斷、回應無 JSON、`status=CANCELED`、`status=ERROR`。
`--json-schema` 只改善了其中一種。

**讀影片這一格永遠只能用 agy CLI。** 已查證：`antigravity-sdk-python` 只吃
`GEMINI_API_KEY` 或 Vertex AI 的 ADC，**不沿用 CLI 的訂閱憑證**。Claude 與 Codex
的 CLI/SDK 都不吃 mp4。所以「用訂閱讀影片」全世界只有 agy CLI 一條路。

**Bash heredoc 會吃掉反斜線跳脫。** 本 session 踩兩次：寫進 Python 字串的 `\n`
會變成真的換行導致 SyntaxError。改檔案用 Edit 工具，或先 Write 腳本再執行。
另外 `app/bot/handlers.py` 是 UTF-8 with BOM + CRLF，逐行改時要保留兩者。

## 環境落差（worktree 派工必讀）

worker 的 worktree 從 `origin/main` 開，而 `.venv`、`.env`、
`temp_videos/f22_fixtures/` 全在 `.gitignore` 內，不會跟過去。派工單要附：

- 主工作區直譯器絕對路徑
- `export TELEGRAM_BOT_TOKEN=dummy-for-tests`（否則 `app.config` 在 collection 階段就炸）
