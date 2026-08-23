# Session Handoff
> 最後更新：2026-08-23（brief-me headless session 第三輪）

## 現況

**21 passing、3 failing（F10 / F13 / F22）。** 單元測試 92 條全綠（原 76）。
冒煙測試 `[1] 下載` 仍 FAIL——但**現在它說的是實話**：cookies.txt 沒有 sessionid。

本輪消化收件匣三則答案：F24 簽核、F22 走 agy、「開跑，從 F10 開始」。
F10 開不了（下載被擋），所以順序改成 F24 → F22 → F18 → F13。

## 這個 session 做了

### F24 — passing 並歸檔

`downloader` 在 `__init__` 就用 `_cookies_have_session()` 判定 cookies.txt 有沒有 sessionid；
`_explain_ytdlp_error()` 成為 yt-dlp 錯誤訊息的單一入口，未認證時直接說
「cookies.txt 未含登入憑證（sessionid）」，不再落進 `"not available"` → 「此影片已不存在或無法存取」那個分支。

- 回歸測試 `tests/test_cookies_sessionid.py` 4 條，含反向護欄（有 sessionid 時不得誤報成 cookies 問題）
- `acceptance-verifier` R1-R5 全 pass，並把 `app/` 複製到暫存目錄還原舊邏輯做差異測試，確認新測試對舊版 3/4 會紅

### F18 — passing 並歸檔（程式碼本來就寫好了，缺的是證據）

實打 Google Places API 跑規格指定的查詢：

```
search_place("巫婆水餃店 台北 北投", expected_name="巫婆水餃店")
-> found=True, name='及香手工水餃', match_confidence=LOW, needs_human_check=True
```

5 筆候選裡沒有巫婆水餃，第二筆正是 2026-07-25 誤採的芳芳江蘇水餃——這次沒有被高信心採用。
`acceptance-verifier` R1-R5 全 pass；**R5（Telegram 回覆標示需人工確認）的證據等級是程式碼路徑不是實跑**，
因為端到端跑不了，這點已寫進 evidence，不要當成端到端驗過。

### F22 — 實作完成，等驗收

`app/services/gemini_video.py`（新）：呼叫 `agy` 讀 mp4，回傳店家與 `is_recommended` 旗標。
`place_extractor` 的 prompt 多一個【Gemini 候選】區塊與規則 9，合併去重＋只留「作者在推薦的店家」。
`handlers` 把 Gemini 與 whisper/vision 一起 `gather`，並在回覆標示來源（降級時講出原因）。

**離線回歸兩案例**（原片 DT2w2PVgXo3 本機沒有，見「擋住的」）：

| 案例 | 合併+篩選 | 只用本地（降級） |
|---|---|---|
| bed0c6b4 酒場清志郎（店名只在招牌上） | `['酒場 清治郎']` 正確 | `['臨鶴堀區公有市場內的日式小店']` 逐字稿爛掉後腦補的 |
| f7f8cb59 美軍炸雞（有路過招牌台灣運彩） | `['Padam Padam 1970']`，台灣運彩已濾掉 | `['Padam Padam 1970']` |

**這條沒有改 passing**：acceptance 指名的回歸案例 (a) 是 `DT2w2PVgXo3`（巫婆水餃 / 蔡元益紅茶），
那支影片本機沒有、也下載不下來。上表案例 (a) 是**同結構的替代品**（主角 + 路過招牌），不是規格要的那一支。
cookies 修好之後補跑原片才算數。

**第一次跑案例(b) 是 fail 的**：規則 9 原本寫「判不出來就不要列，寧可漏也不要塞」，
結果逐字稿把「鹽埕」聽成「位遠程序」，LLM 判不出來就把**整份清空**（`found=False`），
比不接 Gemini 還糟。改成「標記為影片主要拍攝對象的候選一律保留，逐字稿沒有那個店名不構成排除理由」之後才過。

### F13 — 程式碼與單元測試完成，狀態仍 failing

`_find_places_by_url()` 用**貼文 ID**比對（不是整條 URL——IG 分享出來每次 `?igsh=` 都不一樣），
命中就回既有紀錄並 return，不重跑 pipeline。5 條單元測試（含「同一支 Reel 帶不同追蹤參數」）。
**沒有改 passing**：acceptance 要的是「連傳兩次」的端到端證據，而現在下載跑不動。

## 下一步（具體到可直接動手）

1. **重匯 cookies.txt**（收件匣有 question，high）。這一件事解鎖 F10 / F13 / F22 的端到端。
   從已登入 IG 的瀏覽器用 cookie 匯出擴充套件重產一份蓋掉 repo 根目錄那份，確認裡面有 `sessionid`。
2. cookies 好了之後：跑冒煙測試 → 傳一則 Reel 給 bot（F10）→ 同一則再傳一次（F13）→ F22 派驗收。
3. F13 的 acceptance 寫「依 docs/telegram-deduplication.md」，但那份文件描述的是 **message_id 去重**
   （已實作）、而且指向不存在的 `app/bot/telegram_handler.py`。收件匣 report 有問你要怎麼處理。

## 踩過的坑（別重蹈）

- **agy 1.1.19 的 `read_file` 權限只認一字不差的絕對路徑**。`SideProject\*`、`temp_videos\*`、
  `*.mp4`、正斜線版本**全部被拒**，只有 `read_file(*)`（等於整台機器）與完整路徑放行。
  因應：固定用 `temp_videos\agy_input.mp4`，settings.json 只登記這一個路徑。
  **上一輪 handoff 寫的「路徑落在 glob 內就好」在 1.1.19 已經不成立**
- **agy 讀不到檔案會編一份像樣的答案**，只有 `status` 說實話。`status != "SUCCESS"` 就整份丟掉，
  連 `response` 都不要看（`tests/test_gemini_video.py` 守著這條）
- **agy「自己改走 shell」的失敗模式沒消失**：本輪總共 8 次呼叫、2 次中招（都是跑 `python -c "import cv2..."`
  被權限引擎擋下），約 25%，比 spike 當初的 20% 還高。降級路徑是常態不是例外
- **agy 耗時比上一輪慢很多**：上一輪 8-12 秒，這次 9-67 秒（同一支影片兩次量到 9.0s 與 67.3s）
- **prompt 寫「判不出來就不要列」會讓 LLM 把整份清空**。要留退路：「如果變成空的，那幾乎一定是判太嚴了」
- **MarkdownV2 連結目的地不要整條套 `escape_markdown`**——它會把網址裡的 `.` 也加反斜線。
  只需跳脫 `)` 與 `\`。（`/list` 指令與多地點回覆有同樣寫法，本輪沒改，屬既有行為）
- **別相信 `feature_list.json` 的 `passing`**；開工先跑 `.\init.ps1`（含冒煙測試）
- **寫入型委派在這個 repo 沒有意義**：`.env`／`cookies.txt`／`credentials.json`／`*.db` 全 gitignored，
  `executor` 一律在從 `origin/main` 開的 worktree 裡跑，一個都拿不到。唯讀的 `acceptance-verifier` 對主工作區跑則完全可用
- **每支影片都 `WhisperTranscriber()` 開新實例會在 GPU 疊載模型直到顯存爆掉**；`handlers.py` 在 `__init__` 建一次
- Google Maps 存入後 `aria-checked` 有數十秒傳播延遲；地點頁「儲存」與「已儲存」兩個 button 並存，要用 `aria-label`

## 擋住的

- **cookies.txt 沒有 sessionid**（8 個 cookie：ps_l / ps_n / ig_nrcb / ig_did / csrftoken / rur / mid / ds_user_id）。
  上一輪判斷「帶不帶 sessionid 都一樣 429」——但那份 cookies 本來就沒有 sessionid，那個對照從來沒成立過。
  要有一份有效憑證才分得出是限流還是純粹沒登入。
- F10 / F13 / F22 的端到端全部等這件事。
