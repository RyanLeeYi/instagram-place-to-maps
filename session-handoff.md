# Session Handoff

最後更新：2026-08-25
HEAD：`ebb8e6b`（已 push，工作區乾淨）

## 現況

`feature_list.json` 只剩 **F27 envelope**（failing，等下一個 slice 送審）。
本 session 通過驗收並歸檔的：**F24、F18、F10、F13、F22、F25、F26、F27.1**（歸檔區 28 條）。

測試 **134 passed / 2 skipped**（2 條 skip 是需要網路的 Threads 轉址實機檢查，
`RUN_NETWORK_TESTS=1` 才跑）。冒煙測試七段全過。

## 兩個回歸工具

```
scripts\f22_regression.py [次數]   # 預設 3；走快取材料，不呼叫 agy、不連網
scripts\agy_reliability.py [次數]  # 需網路與真 agy，很慢；結果寫 docs\agy-reliability.md
```

`f22_regression` 的兩個案例正確答案是 Ryan 2026-08-25 看過影片逐項確認的。

## 等 Ryan 裁示（唯一擋住進度的東西）

**一、Places 那一段（我建議 E → A → D）**

冒煙測試顯示「巫婆水餃店 台北」被比對成「芳芳江蘇水餃」。查證後發現：

- **巫婆水餃根本不在 Google Maps 上**——它是業配的冷凍水餃品牌，沒有實體店。
  四種查法（含加行政區、加地點偏好）18 筆結果沒有一筆是它
- **`needs_human_check` 只寫進回覆文字**，DB／Sheets／Maps 清單全部無條件寫入。
  所以已知比對錯的店家還是會進使用者的地圖清單

三段修法：

- **E**：擷取階段分「實體店家 vs 品牌／商品」，只有實體店家送 Places。
  判斷資訊都在手邊沒人用——說明文寫「冷凍水餃」「贊助」，agy 的理由是「開箱下鍋烹煮」
  （在自己家），對比其他五家都是「入座用餐」「至攤位點餐」
- **A**：信心度 LOW 時當作沒找到，不寫 Maps 清單。不對稱性支持這個做法——
  錯的條目要手動刪，漏掉的隨時能自己加
- **D**：送 Places 的關鍵字用說明文的行政區，不要讓 LLM 猜「台北」。
  實測換成「北投」後候選從中正／中山／士林區變成北投區

**注意**：加了 E 之後，F22 回歸案例的「必須留下六家」含巫婆水餃仍成立
（擷取階段留下它是對的），但它會被標成非實體店家而不進 Maps。F28 的 acceptance
要寫清楚這點，否則兩份規格會互相矛盾。

**二、F27 envelope 的下一個 slice（F27.2）要不要動工**
F27.1 已完成。F27.2 是兩種執行模式（備援鏈／投票）＋ 使用者切換。

## 給下一個 agent 的坑

**agy 仍然不可靠，只是沒那麼糟。** 含重試 5/6（83%），對照上線前單次呼叫約 50%。
案例 (b) 有一次三連敗；另一支 Threads 影片曾四連敗。**重試不是修好，是壓低頻率。**

**agy 對同一支影片會讀出不同的字**（「酒場清**治**郎」vs「清**志**郎」）。
`f22_regression.py` 目前走快取材料所以不受影響；哪天改成真的呼叫 agy，案例 (b) 會隨機變紅。

**讀影片這一格永遠只能用 agy CLI。** `antigravity-sdk-python` 只吃 `GEMINI_API_KEY`
或 Vertex AI 的 ADC，**不沿用 CLI 的訂閱憑證**；Claude 與 Codex 的 CLI/SDK 都不吃 mp4。

**Bash heredoc 會吃掉反斜線跳脫。** 本 session 踩三次：寫進 Python 字串的 `\n` 會變成
真的換行。改檔用 Edit 工具，或先 Write 腳本再執行。
`handlers.py`／`config.py`／`place_extractor.py` 是 UTF-8 with BOM + CRLF，逐行改要保留兩者。

**一個 commit 只放一個 feature。** F26 與 F27.1 被我混在 `03b8b4c`，害 F27.1 的
「rollback：單一 commit revert」直接做不到，驗收判 P3。手動回退清單記在 F27.1 的 evidence 裡。

## 環境落差（worktree 派工必讀）

worker 的 worktree 從 `origin/main` 開，而 `.venv`、`.env`、`temp_videos/f22_fixtures/`
全在 `.gitignore` 內，不會跟過去。派工單要附：

- 主工作區直譯器絕對路徑（worktree 裡沒有 `.venv`）
- `export TELEGRAM_BOT_TOKEN=dummy-for-tests`（否則 `app.config` 在 collection 階段就炸）
- 要跑 `f22_regression.py` 的話，叫 worker 從主工作區複製 `temp_videos/f22_fixtures/*.json`

派工前用 `git rev-parse --short origin/main` 對一下自己的 HEAD——**commit 了但沒 push 的
規格 worker 一樣看不到**。
