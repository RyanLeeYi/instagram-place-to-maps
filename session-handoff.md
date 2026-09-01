# Session Handoff

最後更新：2026-09-01（brief-me 無人看管）
狀態：**冒煙測試 7/7 全段通過（91 秒）**。feature_list.json 仍是空的，需要 Ryan 決定下一題。

## 本輪唯一的事：修好視覺分析（收件匣答案 6952ea17）

Ryan 選的是「重新下載 minicpm-v:8b」，前提是「最可能是模型檔壞掉」。
**這個前提被證偽了，所以沒有下載任何東西**——查下去發現模型從頭到尾都是好的，
壞的是 ollama 行程本身。

### 根因（與模型完全無關）

`ollama.exe serve`（pid 8048，8/31 13:51 起）在 **8/31 19:37 變成孤兒行程**：
它的父行程是舊的 mission-control（pid 10560），中台在 19:37:15 重啟時**沒有帶走也沒有殺掉**
既有的 ollama，新的中台行程沒有接管它。於是 ollama 的 stdout 管線**讀取端死了**，
緩衝區填滿後就再也寫不出去。

`ollama` 載模型時會把 runner 子行程的 stdout 接到自己這條管線上。
`minicpm-v:8b` 走的是舊版 llama.cpp runner，光 `clip_model_loader` 就要印約 450 行
tensor 明細（>50 KB）——**寫滿死管線就永久卡住**，runner 永遠不會回報就緒，
表面症狀就是 `timed out waiting for llama runner to start - progress 0.00`。
`qwen3-vl:8b` 走新引擎、stdout 安靜得多，所以擠得過去——這就是「換個模型就好」的假象來源。

### 證據鏈（每一條都實跑過）

| 主張 | 證據 |
|---|---|
| 模型檔沒壞 | 兩個大 blob 的 sha256 與 manifest digest **逐字元相符**（4429406528 / 1044425152 bytes） |
| 重抓不會有任何差別 | 拉 registry 上游 manifest 比對，**六個 layer 的 digest 與 size 全部相同**——上游給的就是本機這份 |
| 不是模型的問題 | 另起一個 ollama 實例（port 11435、stdout 導到真檔案），同一份模型檔 **9 秒載入並正常回答** |
| 舊行程確實是孤兒 | `ParentProcessId=10560`，該行程已不存在 |
| 時間點吻合 | 中台 ollama log 最後一筆停在 08-31 19:36:55，新中台行程建立於 19:37:15 |
| 修好了 | 重啟後同一支 API 呼叫 **200 秒無回應 → 9 秒回答** |

### 做了什麼

1. 殺掉孤兒 ollama（pid 8048）→ **中台自動重啟**（新 pid 34100，父行程是活的 mission-control）。
2. 清掉診斷用的第二實例殘留行程；現在 11434 只有中台那一個 ollama，11435 已釋放。
3. 確認中台 `logs/ollama/out.log` 恢復寫入（有當下時間戳）＝管線活著。

**`.env` 一字未動**，`OLLAMA_VISION_MODEL=minicpm-v:8b` 維持原樣，辨識品質零變動。
**零位元組下載**（原方案要抓 5.5 GB）。

## 冒煙測試輸出

```
[3] 視覺分析      success=True 幀數=10        <- 上一輪就是掛在這裡
[4] LLM 擷取地點  found=True 地點數=7
===== 冒煙測試結果（91s）=====
PASS download / transcribe / visual / extract / places_api / sheets_auth / maps_login
全段通過
```

第一次跑時 `sheets_auth` 吃到 Google `APIError: [503]`，單獨重試 `_get_worksheet()` 一次就成功
（`工作表1`, 1683 列），確認是暫時性錯誤；第二次整支重跑 7/7。
上一次全綠是 08-28 的 142 秒，這次 91 秒。

## 給中台的一條回報（不是這個 repo 的事，沒有自己動手）

**mission-control 重啟時不會處理既有的受管服務行程。** 這次 ollama 活下來但變孤兒，
表面上健康檢查照過（`/api/tags` 秒回），實際上任何 stdout 話多的模型都會卡死——
**這是一個健康檢查看不見的故障**。同型風險適用於所有受管服務。
修法方向（中台側）：啟動時偵測既有行程並接管或殺掉，不要放著不管。已在 brief-me report 提出。

## 等 Ryan 的

1. **F32 要不要立項**（brief-me 卡 9cd95119，仍 pending）——feature_list 是空的，
   沒有這個決定下一場沒事做。**注意：那張卡當初寫「對照實驗要等影像模型修好之後再跑才公平」，
   現在條件已經滿足了。**
2. `runtime_settings.json` 要不要 `git rm --cached` ＋ gitignore（08-31 起掛著）。
   它現在仍是 modified（Ryan 自己按按鈕設的 `merge_backends: "agy,ollama"`），本輪沒有動它。

## 環境提醒（沿用）

- worktree 從 **origin/main** 開，沒有 `.venv`／`.env`／`f22_fixtures`／**`cookies.txt`**。
  派工單要附主工作區直譯器絕對路徑、`TELEGRAM_BOT_TOKEN=dummy-for-tests`、
  **`OLLAMA_MODEL=qwen3:8b`**、複製 fixtures。冒煙一律主工作區跑
  （`downloader.py:103` 的 `cookies.txt` 是相對 cwd 路徑）。
- 冒煙測試要 `python -u` ＋ `tee`，否則 pipe 會整段吞掉輸出，卡住跟慢跑分不出來。
  輸出有中文，加 `PYTHONIOENCODING=utf-8` 才不會變亂碼。
- `handlers.py`／`config.py`／`place_extractor.py` 是 UTF-8 BOM + CRLF，
  **`requirements.txt` 與 `.env.example` 也是**。Edit 工具會把整檔轉成 LF——
  改完一定回頭數 CR 與 LF 的數量是否相等。
- `sheets_auth` 偶爾吃 Google 的 `APIError: [503]`，單獨重試
  `GoogleSheetsService()._get_worksheet()` 即可確認是不是暫時性錯誤。
- 服務要經 mission-control 重啟才載入新程式；重啟前先殺殘留的舊 uvicorn（撞埠 10048）。
- harness hook 會擋強制推送，regex 連 `--force-with-lease` 一起攔，沒有授權旁路。
  同一個 guard 也會攔命令字串裡出現那串指令名的任何 Bash 呼叫。

## 既有瑕疵（不要當新 bug 重複回報）

- 鏈全敗時 notes 會出現重複的後端名（`claude-api：claude-api：未設定 ...`）。
- `_is_area_name` 的後綴比對是繁體，模型偶爾輸出簡體會漏過濾。
- f22 案例 (a) 偶爾把「大溪家鄉臭豆腐」輸出成繁簡兩筆同店重複。
- 殘留目錄 `.claude/worktrees/agent-a3301d24e20fdb248/`（OneDrive 鎖住刪不掉）可手動刪；
  本機另有一批 `worktree-agent-*` 分支與 `backup/pre-squash-f27-4` 可清。
