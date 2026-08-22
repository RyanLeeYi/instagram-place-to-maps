# Session Handoff
> 最後更新：2026-08-23（brief-me headless session，正常收工）

## 現況

**18 passing、5 failing（F10 / F13 / F18 / F22 / F23）。**
單元測試 73 條全綠，冒煙測試 7/7 全綠（194 秒）。本輪六條 F11／F12／F14／F15／F16／F17
經 `acceptance-verifier` 逐句驗收全數 PASS，已改 passing 並整條原文歸檔到 `docs/archive/features.jsonl`。

## 這個 session 做了

開工跑 `.\init.ps1` 就抓到**主線壞著**——冒煙測試 `[4] LLM 擷取地點` FAIL。本輪三個 bug 是同一種病的三種長相：**功能壞了但沒有任何東西會紅**。

1. **F4 回歸（已標 passing 卻壞著）**：`place_extractor.py` 傳 `think=True` 給 `ollama.chat()`，
   那是 ollama 0.5+ 的參數，而 `requirements.txt` 釘 `0.3.3`。呼叫包在 try/except 裡 →
   靜默 `found=False`。拿掉即可（實測 qwen3:8b 不傳 think 一樣輸出乾淨 JSON，還省 50 秒）。
2. **F11**：兩個獨立死因。(a) 手動注入 cookies 沒設 `context.username` 與 `X-CSRFToken`——
   前者讓 `save_session_to_file()` 擲 `LoginRequiredException`，**把整段已成功的認證路徑掀掉**，
   程式因此誤報「未認證」；後者讓 `graphql/query` 被回 403。(b) 補完後仍失敗，才是真上游：
   **instaloader 4.15 解不了 Instagram 現行 graphql，4.15.3 可以**，下限已提到 `>=4.15.3`。
3. **F12**：串文作者取 `thread_items[0]`。`@tomny1993/post/DU_IU9eiaC9` 其實是回在
   `@brucechen1110` 串文底下的一則，於是回傳原PO的內容而 **`success=True`**。改以連結上的帳號為準。
4. **F18 的已知瑕疵順手修掉**（F18 仍 failing，差端到端證據）：`name_similarity()` 的包含關係
   寫 `max(0.8, ratio)`，等於「只要包含就是 high」，`ratio` 是死的。實測
   『巫婆水餃店』對『水餃店』甚至『水餃』全都 0.80。改成 `max(SIMILARITY_MEDIUM, ratio)`：
   同一家仍 HIGH，品類詞掉到 MEDIUM，完全抓錯的仍 LOW。
5. **F14–F17**：上一輪留在工作區的未 commit 實作，逐句核對後補上缺的覆蓋
   （`dataclasses.asdict()`、讓 F15 的掃描測試真的會紅）。

## 下一步（具體到可直接動手）

1. **F10 只缺 Ryan 本人動手**：從 Telegram 傳一則 IG Reel 連結給 bot，確認 5 分鐘內收到含地點名稱
   與 Google Maps 連結的回覆、`places` 表有新增。**同一次順便收 F18 最後一項**（低信心要標示需人工確認）。
   agent 做不到這條——bot 收不到自己發的訊息，而偽造 webhook POST 不符合 acceptance 的「從 Telegram 傳」。
2. **F13（去重）相依 F10**，F10 綠了才驗得了。注意 `docs/telegram-deduplication.md` 已過時：
   它寫的 `app/bot/telegram_handler.py` 不存在（實際在 `handlers.py`），而且它描述的是
   **message_id 去重**（已實作），F13 要的是 **URL 去重**（未實作）。動工前先修文件或改 acceptance 指向。
3. **F22 卡在一個只有 Ryan 能做的決定**：走 `agy` subprocess 還是 Gemini API。
   `.env` 目前**沒有** `GEMINI_API_KEY`，選 API 就要先給 key。設計已定，見 `docs/spike-gemini-video.md` 結論一節。
4. **F23** 是驗收時附帶發現的小 bug，尚未簽核。

## 踩過的坑（別重蹈）

- **別相信 `feature_list.json` 的 `passing`**——那只是「某次驗過」。F4 標著 passing 壞了不知多久。
  開工第一件事跑 `.\init.ps1`（含冒煙測試）。
- **`success=True` 不代表拿到對的東西**。F12 那種「挑錯對象」沒有任何訊號，
  只能用**外部真值**驗（Playwright 開實際網頁數則數），不能拿程式自己解析出來的數字當證據。
- **釘住的相依版本要跟程式碼一起看**。`think=True` 與 `ollama==0.3.3`、
  `instaloader>=4.15` 都是「程式碼寫給另一個版本」。`tests/test_ollama_call_contract.py`
  用 AST 掃呼叫點比對安裝版簽章，就是為了讓這類漂移會紅。
- **寫入型委派在這個 repo 沒有意義**：`.env`／`cookies.txt`／`credentials.json`／`browser_state/`／`*.db`
  全部 gitignored，而 `executor` 一律在從 `origin/main` 開的 worktree 跑，**一個都拿不到**。
  唯讀的 `acceptance-verifier` 對著主工作區跑則完全可用（本輪就是這樣驗的）。
- Google Maps 存入後 `aria-checked` 有數十秒傳播延遲；地點頁「儲存」與「已儲存」兩個 button 並存，要用 `aria-label`
- `crv --max-frames N` 會讓場景抽幀退化成等距取樣
- **每支影片都 `WhisperTranscriber()` 開新實例會在 GPU 疊載模型直到顯存爆掉**；`handlers.py` 在 `__init__` 建一次，沒這問題
- `agy` 的工作區來自 `trustedWorkspaces` 不是當前目錄 → 必須絕對路徑 + `--add-dir`
- 使用 CUDA 的 python 行程結束時偶發 exit 127（teardown 崩潰），輸出已正常寫出時可忽略，但別誤判成任務失敗
