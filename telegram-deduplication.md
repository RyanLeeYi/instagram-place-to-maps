# Telegram Bot 重複請求處理機制說明

## 問題背景

Telegram Bot 可能因網路延遲、Webhook 重試、或用戶快速重複發送等情況，收到相同訊息的多次處理請求。若不處理，會導致同一 Instagram 連結被重複下載、轉錄、摘要，浪費資源並產生重複的 Roam Research 筆記。

## 解決方案：多層防護機制

本專案在 `app/bot/telegram_handler.py` 中實現了以下防護：

### 1. 訊息 ID 去重（主要機制）

```python
# 類別初始化時建立記憶體快取
self._processed_message_ids: set[int] = set()

# 處理訊息前檢查
message_id = update.message.message_id
if message_id in self._processed_message_ids:
    logger.debug(f"訊息 ID {message_id} 已處理過，跳過")
    return

# 在處理開始前立即標記（防止併發重入）
self._processed_message_ids.add(message_id)
```

### 2. 記憶體管理

為避免記憶體無限增長，當快取超過 1000 個 ID 時，自動清理較舊的一半：

```python
if len(self._processed_message_ids) > 1000:
    ids_list = sorted(self._processed_message_ids)
    self._processed_message_ids = set(ids_list[500:])
```

### 3. 輔助過濾條件

| 過濾條件 | 程式碼 | 說明 |
|---------|--------|------|
| 忽略 Bot 自己的訊息 | `if update.message.from_user.is_bot: return` | 防止處理 Bot 自己發出的訊息 |
| 忽略回覆訊息 | `if update.message.reply_to_message: return` | 防止 Bot 回覆中的連結被誤認為新連結 |
| 忽略編輯過的訊息 | `if update.edited_message: return` | 編輯訊息會觸發另一個更新事件 |
| 忽略空訊息 | `if not message_text.strip(): return` | 跳過無內容的訊息 |

## 流程圖

```
收到 Telegram Update
        │
        ▼
┌───────────────────────┐
│ 是否為有效訊息？       │──否──→ 忽略
└───────────────────────┘
        │ 是
        ▼
┌───────────────────────┐
│ 是否來自 Bot？         │──是──→ 忽略
└───────────────────────┘
        │ 否
        ▼
┌───────────────────────┐
│ 是否為回覆訊息？       │──是──→ 忽略
└───────────────────────┘
        │ 否
        ▼
┌───────────────────────┐
│ 訊息 ID 是否已處理過？ │──是──→ 忽略（記錄 debug log）
└───────────────────────┘
        │ 否
        ▼
┌───────────────────────┐
│ 標記訊息 ID 為已處理   │
└───────────────────────┘
        │
        ▼
    繼續正常處理流程
```

## 相關程式碼位置

- **主要實作**：`app/bot/telegram_handler.py`
  - `TelegramHandler.__init__()` - 初始化 `_processed_message_ids` 集合
  - `TelegramHandler.handle_message()` - 實作去重邏輯

## 注意事項

1. **記憶體內快取**：這是記憶體內快取，Bot 重啟後會清空。但因 Telegram 的 `message_id` 是遞增的，重啟後不太可能收到舊的重複訊息。

2. **持久化選項**：若需持久化去重，可考慮將處理過的訊息 ID 存入資料庫（目前專案中 `FailedTask` 表有類似概念）。

3. **標記時機**：標記動作發生在**處理開始前**而非完成後，這是為了防止處理過程中因超時等原因觸發 Webhook 重試時的重複處理。

4. **記憶體上限**：快取最多保留 1000 個訊息 ID，超過時自動清理較舊的 500 個，確保記憶體使用量可控。

## 更新紀錄

- **2026-01-21**：實作訊息 ID 去重防止重複處理
