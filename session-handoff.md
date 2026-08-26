# Session Handoff

最後更新：2026-08-26（額度觸頂收工）
HEAD：`f76d0ad`（已 push，工作區乾淨）

## 現況

- **F27.2 passing 並歸檔**（歸檔區 29 條）：24/24 驗收過。#18 log 可觀測性打回一次，修好後 targeted 重驗 pass。單一 commit `16921c3`。
- **F28 實作完成但卡在降級路徑回歸，未驗收**（詳見下）。主檔剩 F27 envelope + F28。
- 測試基準：**170 passed / 2 skipped**（F27.2 後 159 + F28 的 11 條）。
- #11 已於 2026-08-26 經 Ryan 重新簽核：巫婆水餃的 is_physical 不做硬斷言（qwen3:8b 兩版 prompt 共 9 次推論全判 true，能力上限），改驗端到端守門（E 段或 A 段任一道擋住即 pass）；其餘五家仍必須 true。

## F28 唯一擋路的東西：降級路徑（無 agy 候選）schema 崩壞

**症狀**：冒煙 extract 段 found=False。F28 前同材料 3/3 成功，F28 prompt 加長後 0/3。
f22_regression 抓不到——它有 agy 候選段，該情境 schema 正常（兩案例各 3/3 全過）。

**根因（已確認，別重查）**：qwen3:8b 在 F28 加長的 prompt + 無候選段時系統性拋棄指定
schema——自創頂層鍵（「推荐店家」「景点/地点」city+additional_info）、沒有 found 鍵、簡體。

**已試過的（全都別再試）**：純 prompt 工程五版全滅——v2 商品/地點重框、v3 標記非過濾、
v4 精簡欄位+規則13 schema 錨定、v5 開頭格式強制、v6 few-shot 範例。原始輸出樣本在
executor worktree 的 scratchpad（agent-ac5971665a79b6014）。

**有效但還不夠的（已 commit `f76d0ad`）**：
1. `ollama.chat(format="json")`（釘住的 0.3.3 簽章支援 Literal['','json']，不支援 schema dict）——堵住「整段自然語言不給 JSON」模式
2. `_parse` 加 schema 漂移救援：頂層任何「含 name 的 dict 陣列」都當 places 收下
兩者疊加後降級重現 **4/6**（HEAD 前是 0/3）。剩餘失敗是模型回 found=false 或無可救陣列。

**下一步（照序）**：
1. 未驗證的候選修法：**降級模式用短 prompt**——規則 11-13（district/is_physical 詳解）只在
   有 agy 候選時附加，降級模式回到接近 F28 前的短 prompt（已知穩定 3/3）。理由：降級路徑
   本來就「不要求精度」（_reconcile docstring），is_physical 缺省 True 是 fail-open，A 段兜底。
   實作點：EXTRACTION_PROMPT 的欄位描述去掉「見規則 N」引用改自足短句，規則 11-13 抽成
   常數在 extract() 依 gemini_places 有無條件附加。
2. 驗證迴圈：降級重現 3/3（scratchpad `repro_main.py`，指主工作區）→ f22_regression 3
   兩案例各 3/3（confirm 有候選路徑沒被改壞）→ pytest ≥170 → 冒煙七段
3. 全綠後派 acceptance-verifier 驗 F28（注意披露：F28 已是**兩個 commit**（047f789+f76d0ad，
   可能再加一個），#13 rollback 條款會吃 P3，F27.1 有前例）

## 回歸工具

```
scripts\f22_regression.py [次數]   # 快取材料+本地 ollama，一輪 3 約 30-40 分鐘
scripts\smoke_pipeline.py          # 全 pipeline 真跑約 3-4 分鐘，[4] extract 是本次回歸的哨兵
scratchpad repro_main.py           # 降級路徑重現（gemini_places=None），一次 1-3 分鐘
```

## 給下一個 agent 的坑

- worktree 派工：從 origin/main 開、沒有 .venv/.env/f22_fixtures。派工單要附主工作區直譯器
  絕對路徑、`TELEGRAM_BOT_TOKEN=dummy-for-tests`、複製 fixtures、**`OLLAMA_MODEL=qwen3:8b`**
  （worktree 沒 .env 時 Settings 預設 qwen2.5:7b，本機沒裝，會 model not found）
- handlers.py／config.py／place_extractor.py 是 UTF-8 BOM + CRLF，用 Edit 工具改
- `_is_area_name` 的後綴比對是繁體（市場），模型輸出簡體（市场）會漏過濾——降級救援
  路徑實測出現過，暫不修（降級不要求精度），但別當成 bug 重複回報
- F28 的 f22_regression 案例 (a) 已改：巫婆水餃 is_physical 不硬斷言、其餘五家必須 true

## 等 Ryan 的

無（#11 已裁示）。F28 收尾照上面「下一步」走即可。
