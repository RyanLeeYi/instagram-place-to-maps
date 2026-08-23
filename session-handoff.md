# Session Handoff
> 最後更新：2026-08-23（brief-me headless session 第二輪，正常收工）

## 現況

**19 passing、4 failing（F10 / F13 / F18 / F22）＋ 新開 F24（未簽核）。**
單元測試 76 條全綠（原 73）。**冒煙測試目前 FAIL 在 `[1] 下載`，原因是 Instagram 對本機 IP 回 429**，
與本輪改動無關，詳見下方「擋住的」。

本輪消化 brief-me 收件匣的兩則答案：F23 簽核（已完成並歸檔）、F22 的反問（已查證，等你拍板）。

## 這個 session 做了

### F23 — 已 passing 並歸檔

`cleanup_post_images()` 只對 `image_paths[0].parent` 做 `rmdir`，跨目錄呼叫時其餘目錄清空卻留著。
改成收集所有 parent 去重後逐一處理（`dict.fromkeys` 保序去重，一行）。

- 回歸測試 `tests/test_cleanup_post_images.py`：**先確認對舊版實作會紅**，再確認新版綠
- `acceptance-verifier` fresh context 逐條驗收：R1／R2 pass ＋ 3 項邊界檢查，
  它自己另外寫了 `manual_repro.py` 與差異測試（把 `app/` 複製到暫存目錄改回舊邏輯）證明測試不是空綠燈
- 順手清掉 `temp_videos/` 底下 3 個舊的空目錄（`threads_41438d5d`／`bbd9fddf`／`bc62e7dc`）

### F22 — 查證完成，等你拍板（收件匣有新 question）

你的反問是「有 any cli 可以直接用它分析」。查證結果：

**1. `gemini` CLI（`@google/gemini-cli` 0.31.0，已安裝）現在不能用。**
用你的 oauth-personal 憑證跑會直接被擋：

```
IneligibleTierError: This client is no longer supported for Gemini Code Assist
for individuals. To continue using Gemini, please migrate to the Antigravity
suite of products
```

Google 已經把個人免費層收掉、導去 Antigravity。所以「另一個免費 CLI」這條路不存在——
**`agy` 就是那個 CLI**。給 API key 的話 `gemini` CLI 能跑，但那只是在 API 外面多包一層 agent 決策，比直接呼叫 API 差。

**2. `agy` 1.1.12 headless 讀 mp4 是可行的，但有三個必要條件。**
測試對象 `temp_videos/f7f8cb59_video.mp4`（Padam Padam 美軍炸雞那支）：

| 設定 | n | 結果 |
|---|---|---|
| `--mode plan` | 3 | 0 成功。模型第一步就去下 shell 指令（`Test-Path`、`ffprobe`、`git status`），被權限引擎擋掉 |
| 目錄不在 `permissions.allow` 的 glob 內 | 3 | 0 成功。`read_file` 被拒 → 退回 shell → 也被拒 |
| **不加 `--mode plan`、路徑正確** | 4 | **4 成功**，每次約 8 秒，答案正確（美軍炸雞 / Padam Padam），含指定 `名稱\|sign` 格式那次 |
| **再加 `--json-schema`** | 2 | **2 成功**，8.8／11.9 秒，答案正確；JSON 物件**接在敘述文字後面**，不是整包 JSON |

正確配方下總計 **6/6 成功**，沒有一次踩到 spike 當初 1/5 的「它自己改走 shell」。

所以可用的配方是：**不要 `--mode plan`；影片路徑要落在 `~/.gemini/antigravity-cli/settings.json` 的
`permissions.allow` glob（目前是 `read_file(C:\Users\user\OneDrive\Desktop\SideProject\*)`）內；
用 `--print='...'` 把 prompt 貼在旗標上**（go 風格 flag，寫成 `--print --mode plan` 會把 `--mode` 當成 prompt 吃掉）。

**3. 這輪最該記的一件事：agy 讀不到檔案時不會停，會編一個像樣的答案出來。**
我有一批測試因為自己的 shell 變數沒展開，傳了字面 `$v.mp4` 進去。agy 讀檔失敗後**沒有中止**，
而是回了「酒場清志郎、植地、玩藝樹」——那是**別支影片**的店名，而且出現在 `docs/spike-gemini-video.md` 裡。
唯一的訊號是 JSON 的 `"status": "ERROR"`；`response` 欄位看起來完全正常。

> **接進 pipeline 時，必須先檢查 `status == "SUCCESS"` 才准用 `response`。**
> 只 parse `response` 的話，拿到的會是 schema 合法、內容捏造的資料——就是這個 repo 一直在踩的那種失敗。

### F24 — 新開，未簽核

`cookies.txt` 目前只有 8 個 cookie、**不含 `sessionid`**（等於未登入），
而 `instaloader_session/session-j113251106` 裡的 `sessionid` 還在。兩份憑證來源不一致，而且沒有任何訊號。
F24 要修的是「未登入卻不吭聲」，不是今天的 429。

## 下一步（具體到可直接動手）

1. **F22 等你回收件匣的 question**：走 agy（照上面的配方，零額外花費）還是 Gemini API（要 key）。
   選 agy 的話 `app/services/gemini_video.py` 還沒建立，設計照 `docs/spike-gemini-video.md` 結論一節。
2. **F10 只缺你本人動手**：從 Telegram 傳一則 IG Reel 連結給 bot。
   **但要先等 429 退掉**——現在下載一定失敗。同一次順便收 F18 最後一項（低信心標示需人工確認；
   程式碼已實作，見 `google_places.py` 的 `MatchConfidence` 與 `handlers.py:1154`，差的只是端到端證據）。
3. **F13（URL 去重）相依 F10**。注意 `docs/telegram-deduplication.md` 已過時：
   它寫的 `app/bot/telegram_handler.py` 不存在（實際在 `handlers.py`），而且描述的是 message_id 去重（已實作），
   F13 要的是 URL 去重（未實作）。動工前先修文件或改 acceptance 指向。
4. **F24** 等簽核。

## 踩過的坑（別重蹈）

- **agy 讀檔失敗會編答案，只有 `status` 欄位會說實話**（本輪新增，見上）
- **agy 的 flag 是 go 風格**：`--print` 會吃掉下一個 argument。一定要 `--print='<prompt>'`。
  我第一次的「permission denied」誤判成 agy 亂跑 shell，其實是 prompt 變成字面 `--mode`
- **`--mode plan` 反而讓 agy 去下 shell 指令**（大概是「唯讀模式 → 先勘查」），3/3 失敗。別用
- **`gemini` CLI 的個人免費層已被 Google 收掉**，oauth 憑證直接 `IneligibleTierError`
- **別相信 `feature_list.json` 的 `passing`**——那只是「某次驗過」。開工第一件事跑 `.\init.ps1`（含冒煙測試）
- **`success=True` 不代表拿到對的東西**。`success`／`status` 這類欄位要真的去讀
- **寫入型委派在這個 repo 沒有意義**：`.env`／`cookies.txt`／`credentials.json`／`browser_state/`／`*.db`
  全部 gitignored，而 `executor` 一律在從 `origin/main` 開的 worktree 裡跑，**一個都拿不到**。
  唯讀的 `acceptance-verifier` 對著主工作區跑則完全可用（F23 就是這樣驗的）
- Google Maps 存入後 `aria-checked` 有數十秒傳播延遲；地點頁「儲存」與「已儲存」兩個 button 並存，要用 `aria-label`
- `crv --max-frames N` 會讓場景抽幀退化成等距取樣
- **每支影片都 `WhisperTranscriber()` 開新實例會在 GPU 疊載模型直到顯存爆掉**；`handlers.py` 在 `__init__` 建一次，沒這問題
- 使用 CUDA 的 python 行程結束時偶發 exit 127（teardown 崩潰），輸出已正常寫出時可忽略，但別誤判成任務失敗

## 擋住的

- **Instagram 對本機 IP 回 429**（`Too Many Requests`）。實測帶 `sessionid` 與不帶都一樣 429，
  所以不是憑證死掉，是被限流。冒煙測試 `[1] 下載` 因此 FAIL，F10／F13／F18 的端到端驗證全部要等它退掉。
- F22 等你在收件匣拍板。
