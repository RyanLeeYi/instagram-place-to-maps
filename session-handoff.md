# Session Handoff
> 最後更新：2026-07-25 22:10

## 這個 session 做了

- 荒廢 5 個月後的全功能冒煙測試：F1–F7 全綠
- 追補 harness L1+L2：`CLAUDE.md`、`init.ps1`、`feature_list.json`、`.claude/settings.json`、`session-handoff.md`、`docs/ARCHITECTURE.md`、`scripts/smoke_pipeline.py`
- **F8 修復 → passing**：根因是 Google session 過期（不是 UI 改版、不是 selector 過時）。跑 `interactive_login()` 重登即恢復，前後筆數差 999→1,000 驗證通過
- **F9 修復 → passing**：`SaveResult`/`ListsResult` 加 `error_message` property，4 個終局失敗路徑 warning→error 並補上「session 可能失效」提示；新增 `tests/test_maps_result.py`（9 passed）；`pytest` 補進 requirements.txt
- `.gitignore` 修正：原本整個 `docs/` 被排除，`ARCHITECTURE.md` 進不了 git，改成 `docs/*` + `!docs/ARCHITECTURE.md`

## 做到一半 / 已知未修

- **F14（新增）**：`is_logged_in()` 只檢查 `auth_file.exists()`，session 死了半年仍回報 True — 這是 F8 壞掉沒被察覺的第二層原因
- F10–F13 未驗證：Telegram 端到端、IG 圖文貼文（instaloader 有上游風險）、Threads 串文、重複 URL 去重
- 本 session 未跑 code review：Codex 額度用盡（7/29 07:26 恢復），內建 `/code-review` 只能由使用者手動觸發

## 下一步（具體到可直接動手）

1. 補跑 code review（`/codex-review` 額度恢復後，或請 Ryan 手動跑 `/code-review`）
2. 做 F14：`is_logged_in()` 改成能反映真實 session，或另加 `verify_session()`；這樣 session 再過期時會明確報「要重登」而不是靜默失敗
3. F10 端到端：Ryan 從 Telegram 傳一則 reel 連結，看完整鏈路與 DB 寫入

## 踩過的坑（別重蹈）

- Google Maps 存入後，該地點選單的 `aria-checked` 有**數十秒傳播延遲**，剛存完立刻查會看到 false，別因此判定失敗
- 地點頁 DOM 裡「儲存」與「已儲存」兩個 button 同時存在，用文字比對會抓錯，要用 `aria-label`
