# Spike：Gemini（agy）影片理解 vs 本地 pipeline

> 2026-07-26 ｜ F21 ｜ 結論：**兩邊都跑、合併候選、再篩推薦店家**（不是二選一）

## 動機

本地 pipeline 把影片壓成 10 幀靜態描述 + 逐字稿，時序與「畫面-語音」的對應會遺失。Gemini 能原生讀影片（畫面 + 聲音一次處理），值得比較。

工具：Antigravity CLI（`agy` 1.1.7，模型 `gemini-3.6-flash-medium`）。它的 `view_file` 原生解析 mp4，不需自行抽幀。

## 實驗

5 支實際處理過的 IG reel，各跑一次本地 pipeline（faster-whisper + minicpm-v + qwen3）與 agy，結果送 Google Places 驗證。

| Reel | 本地 | agy |
|---|---|---|
| DT2w2PVgXo3（北投市場） | 巫婆水餃店、北投市場、北投中繼市場｜160s | 海鮮拉麵清燉豬腳、阿宗蚵仔煎、大溪家鄉臭豆腐、高媽媽傳統米食、蔡元益紅茶｜44s |
| DbBDplQS71J | Padam Padam 1970 美軍炸雞｜150s | 美軍炸雞｜8s |
| DVBXYSZEl6k | 秋紅肚房、尖蚪、寶村柑仔店｜148s | **失敗**（權限）｜12s |
| DU-UDRwEpHI | 湘帥客棧｜120s | 湘帥客棧｜9s |
| DVA8jTOkStE | 富士山的豬、國家攝影文化中心、金磚樂高、三刻甜點｜166s | 富士山的豬、國家攝影文化中心、金磚屋、三刻｜14s |

**總耗時：743s → 88s（8.5x）**

另有一支單獨測試（高雄鹽埕「酒場清志郎」）：本地完全沒抓到店名（whisper 把地點聽成「位遠程序公有市場」），agy 正確讀出店名、行政區與帳號，經 Places 驗證確有此店（評分 4.6）。

## 三個發現

### 1. 兩邊的強項互補，這是本次最重要的結論

- **本地強在「聽人說了什麼」**：DT2w2PVgXo3 的逐字稿明確推薦「巫婆水餃」，本地抓到、agy 完全沒抓到
- **Gemini 強在「看招牌寫了什麼」**：同一支影片 agy 讀出整排市場招牌；酒場清志郎那支的店名只存在於招牌，只有 agy 拿得到

所以「換掉本地」會失去語音線索，「不用 Gemini」會失去招牌線索。

### 2. agy 的可靠性不足以直接接進服務

5 支裡 1 支失敗——同樣的 prompt 與設定，它**自己決定改走 shell**（`Step_RunCommand`）而被權限規則擋下，其餘 4 支都正常用 `view_file`。這種工具選擇的不確定性在互動式開發沒問題，在 webhook 服務裡會變成隨機失敗。

其他操作面問題：
- 工作區取自 `trustedWorkspaces` 而非當前目錄，**必須用絕對路徑 + `--add-dir`**
- headless 模式無法互動授權，要在 `~/.gemini/antigravity-cli/settings.json` 寫 `permissions.allow`（語法 `read_file(<glob>)`、`command(<glob>)`）
- 每次呼叫有數秒的 CLI 啟動成本

若要正式接入，應改用 Gemini API（Files API + 結構化輸出），行為可預測、無 agent 決策層。代價是需要 API key 與計費，而 agy 走的是既有的 Antigravity 訂閱額度。

### 3. agy 傾向過度擷取

DT2w2PVgXo3 回了 5 個名字，其中「海鮮拉麵清燉豬腳」是菜單橫幅、不是店家。Ryan 的需求是**只存影片推薦的店家**，所以這些路過招牌屬於噪音。

## 結論與建議設計

Ryan 決策（2026-07-26）：**兩邊都跑、合併結果**，且只保留「影片推薦的店家」。

兩者相加會放大噪音，所以合併之後必須有一道篩選：

```
本地：whisper 逐字稿 + minicpm-v 幀描述 ──┐
                                        ├─→ 候選店名集合 → 去重
Gemini：影片原生理解（招牌 OCR 為主）──┘        ↓
                          LLM 依逐字稿/caption 判定「哪些是作者在推薦的」
                                              ↓
                                   Places 驗證（F18 信心度）
```

關鍵是最後那道判定：招牌來源的候選若同時是主角（酒場清志郎）要留下，路過的紅茶攤要濾掉。判定依據是逐字稿與 caption 有沒有在講它。

實作注意事項：
- Gemini 那側失敗（權限、逾時、額度）必須降級成「只用本地結果」，不能讓整支 pipeline 掛掉——5 支失敗 1 支不是例外情況
- 影片會上傳雲端，這推翻 DECISIONS 的 D1（本地優先），需要在 DECISIONS 補一條說明代價
- 端到端時間預期仍由本地視覺分析主導（約 150s），除非之後把本地視覺那段也砍掉

## 限制

n=5（加單獨測試 1 支）。店家型態偏台灣小吃／市場，未涵蓋連鎖店、無招牌店、外語招牌。判定「哪個答案正確」由 Ryan 人工認定，非盲測。

---

## 2026-08-23 追記｜正式接入（F22）時實測到的三件事

接入版本是 agy **1.1.19**（spike 當時 1.1.7、上一輪查證是 1.1.12）。

### 1. `read_file` 的權限比對只認一字不差的絕對路徑

`permissions.allow` 裡的萬用字元在 Windows 路徑上**完全不生效**。實測全部被拒：

| 寫法 | 結果 |
|---|---|
| `read_file(C:\...\SideProject\*)` | 拒 |
| `read_file(C:\...\SideProject\**)` | 拒 |
| `read_file(C:/.../SideProject/**)` | 拒 |
| `read_file(C:\...\temp_videos\*)` | 拒 |
| `read_file(C:\...\temp_videos\*.mp4)` | 拒 |
| `read_file(*)` | **放行**（等於整台機器可讀） |
| `read_file(C:\...\temp_videos\f7f8cb59_video.mp4)`（完整路徑） | **放行** |

所以上一輪handoff寫的「路徑落在 `SideProject\*` glob 內就可以」在 1.1.19 已經不成立——
那份設定現在一個檔案都放行不了。

**因應**：`GeminiVideoExtractor` 固定用 `temp_videos\agy_input.mp4` 這個檔名，
每次呼叫前把當前影片複製過去，`settings.json` 只登記這一個完整路徑。
權限面最小，也不受 glob 語法漂移影響。代價是同時只能處理一支影片（已加 lock）。

### 2. 「它自己改走 shell」的失敗模式還在，比例約 1/4

4 支影片實測 3 成功、1 失敗。失敗那支 agy 決定跑 `python -c "import cv2..."` 去讀影片長度，
被權限引擎擋下 → `status=ERROR`。這正是 spike 當初 5 支失敗 1 支的同一個東西，
**沒有因為版本更新而消失**，所以降級路徑是常態不是例外。

### 3. 耗時比上一輪慢

上一輪量到 8-12 秒；這次 9-67 秒（同一支影片 `f7f8cb59` 分別量到 9.0s 與 67.3s）。
timeout 因此設 240 秒。

### 接入後的判讀規則（不變，且更重要）

`status != "SUCCESS"` 就整份丟掉，**連 `response` 都不要看**。
回歸測試在 `tests/test_gemini_video.py`。
