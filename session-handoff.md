# Session Handoff
> 最後更新：2026-07-26 07:30（因 Claude 額度達收工線而中止）

## 這個 session 做了

- F8/F9/F19 完成並已 commit（15e0008）：Maps 存入修復（根因是 Google session 過期）、錯誤欄位統一、crv 抽幀 spike
- **F20 → passing**：Whisper 改 CUDA，短音檔 2.1x、長音檔 3.0x，品質不降反升（float16 > int8）。`.env` 已切 `cuda`，CUDA 失敗會自動退回 CPU
- **F21 → passing**：Gemini（agy）影片理解 spike，報告 `docs/spike-gemini-video.md`
- **F18 實作完成但仍 failing**（差 Telegram 端到端證據）
- 新開 **F22**：本地 + Gemini 雙來源合併與推薦店家篩選（Ryan 已拍板方向）

## 做到一半 / 已知未修

- **F18**：`match_confidence` 已實作實測，Telegram 低信心標示已寫進 `handlers.py`，但未經真實 Telegram 驗證 → 跟 F10 一起收
- **F18 後續瑕疵**：相似度算法會把「巫婆水餃店 → 水餃店」判成 high（子字串包含關係給分過寬），實務上那不是同一家。要收緊（例如包含關係需長度比例達門檻）
- **F22 未動工**：設計已定（見 `docs/spike-gemini-video.md` 結論一節）
- F10–F17 未做（端到端、IG 圖文、Threads、去重、登入狀態偵測、review 的 3 個 LOW）

## 下一步（具體到可直接動手）

1. **F10 端到端**：Ryan 從 Telegram 傳一則 reel，順便收 F18 最後一項
2. **F22**：照報告的設計實作合併 + 推薦篩選；Gemini 側失敗必須降級為只用本地
3. 決定 F22 用 `agy` subprocess 還是 Gemini API（報告有比較：agy 走既有訂閱額度但工具選擇不確定、5 支失敗 1 支；API 行為可預測但要 key）

## 踩過的坑（別重蹈）

- Google Maps 存入後 `aria-checked` 有數十秒傳播延遲；地點頁「儲存」與「已儲存」兩個 button 並存，要用 `aria-label`
- `crv --max-frames N` 會讓場景抽幀退化成等距取樣
- **每支影片都 `WhisperTranscriber()` 開新實例會在 GPU 疊載模型直到顯存爆掉**（spike 腳本踩過，exit 127）；`handlers.py` 是在 `__init__` 建一次，沒這問題
- `agy` 的工作區來自 `trustedWorkspaces` 不是當前目錄 → 必須絕對路徑 + `--add-dir`
- 使用 CUDA 的 python 行程結束時偶發 exit 127（teardown 崩潰），輸出已正常寫出時可忽略，但別誤判成任務失敗
