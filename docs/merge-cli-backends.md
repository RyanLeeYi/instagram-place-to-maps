# F27.3：三家 CLI 版合併後端

`MERGE_BACKENDS` 鏈上可用的三個 CLI 後端，皆走已登入的訂閱 CLI（subprocess），
不吃 API key。實作見 `app/services/merge_cli_backends.py`。

## 旗標組合

| 後端 | 指令 | 外層信封成功判準 |
|---|---|---|
| agy | `agy --output-format json --print <prompt>` | `status == "SUCCESS"` |
| claude | `claude -p --output-format json <prompt>` | `is_error == false` |
| codex | `codex exec --json --sandbox read-only --skip-git-repo-check <prompt>` | 事件串流裡出現 `type: "turn.completed"`（非 `turn.failed`） |

三家共用 `MERGE_CLI_TIMEOUT`（預設 240 秒）；逾時就 kill 子行程並轉
`MergeFailure`。內層文字（agy 的 `response`、claude 的 `result`、codex
`agent_message` item 的 `text`）一律丟給 `merge_backends.parse_merge_response`
解析，不另外重寫一份 JSON 解析邏輯。

## codex 的輸出形狀（JSONL，不是單一 JSON 物件）

`codex exec --json` 一行一個事件。實測會出現：

```
{"type":"thread.started","thread_id":"..."}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"..."}}   // 可能只是警告
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"..."}}
{"type":"turn.completed"}
```

失敗時：

```
{"type":"error","message":"..."}
{"type":"turn.failed","error":{"message":"..."}}
```

`item.completed`（`item.type == "error"`）中途出現不代表整輪失敗——實測過
model metadata 找不到、skill 描述被截短這兩種警告都長這樣，但輪次仍會走到
`turn.completed`。真正的判準是有沒有等到終局的 `turn.completed`／`turn.failed`。

## 已知限制（2026-08-26）

這台機器登入的 ChatGPT 帳號呼叫 `codex exec` 時，不論指定哪個 `-m` model
（試過 `gpt-5.6-sol`（帳號的預設值）、`gpt-5`、`gpt-5-codex`、
`gpt-5.1-codex`、`gpt-5.1-codex-max`）都被伺服器拒絕：

```
{"type":"error","status":400,"error":{"type":"invalid_request_error",
 "message":"The 'xxx' model is not supported when using Codex with a ChatGPT account."}}
```

`codex login status` 顯示已登入，`codex doctor` 顯示 auth／reachability 都
正常，所以判斷是這個 ChatGPT 帳號的方案本身不含 Codex CLI 的模型存取權，
不是本次實作的 bug。`CodexMergeBackend` 的成功路徑（`agent_message` item
的欄位名稱）因此沒能用真的成功回應驗證，是照協定裡已驗證的失敗分支命名
慣例（`item.type`／`message`）與 codex.exe 二進位內的事件字串類推寫的，
並用 `text`/`content`/`message` 多個候選欄位名容錯。帳號一旦能跑通，補一次
真實成功樣本到這份文件、並視情況調整欄位名稱。

失敗、逾時、CLI 不存在三種情況的判斷邏輯（外層先驗、逾時 kill、找不到
執行檔轉 MergeFailure）不受此限制影響，三家共用同一段 `_run_cli_subprocess`，
且已用假 subprocess 覆蓋測試。
