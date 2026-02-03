# Instagram Food to Maps   

將 Instagram 美食 Reels 自動擷取餐廳資訊，並提供 Google Maps 連結。

## 功能

-  下載 Instagram Reels 影片
-  語音轉文字 (Whisper)
-  畫面視覺分析 (MiniCPM-V)
-  智慧擷取店家資訊 (Qwen2.5)
-  Google Maps 地點搜尋
-  本地資料庫儲存

## 快速開始

### 1. 環境設定

```powershell
# 複製專案
cd instagram-food-to-maps

# 建立虛擬環境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 安裝依賴
pip install -r requirements.txt
```

### 2. 設定環境變數

```powershell
# 複製範例檔案
cp .env.example .env

# 編輯 .env
notepad .env
```

**必要設定：**
- `TELEGRAM_BOT_TOKEN` - 從 @BotFather 取得
- `TELEGRAM_ALLOWED_CHAT_IDS` - 你的 Chat ID

**可選設定：**
- `GOOGLE_PLACES_API_KEY` - Google Places API Key（可取得精確地點資訊）
- `WEBHOOK_URL` - Webhook 網址（例如：https://food.yourdomain.com）

### 3. 啟動

```powershell
.\start.ps1

# 或手動啟動
uvicorn app.main:app --port 8001 --reload
```

### 4. 設定 Tunnel（如使用 Webhook）

```powershell
# 如果有自訂域名
cloudflared tunnel run my-projects

# 或使用臨時 tunnel
cloudflared tunnel --url http://localhost:8001
```

## 使用方式

1. 在 Telegram 找到你的 Bot
2. 傳送 `/start` 開始
3. 貼上 Instagram 美食 Reels 連結
4. 等待分析結果
5. 點擊 Google Maps 連結加入你的清單

## Bot 指令

| 指令 | 說明 |
|------|------|
| `/start` | 顯示歡迎訊息 |
| `/help` | 使用說明 |
| `/list` | 查看已儲存的地點 |

## 專案結構

```
instagram-food-to-maps/
 app/
    __init__.py
    config.py              # 設定檔
    main.py                # FastAPI 主程式
    bot/
       handlers.py        # Telegram Bot 處理器
    database/
       models.py          # 資料庫模型
    services/
        downloader.py      # 影片下載
        transcriber.py     # 語音轉文字
        visual_analyzer.py # 視覺分析
        food_extractor.py  # 美食資訊擷取
        google_places.py   # Google Places API
 .env.example
 requirements.txt
 start.ps1
```

## 技術架構

```
IG Reels URL
    
    

  Downloader  yt-dlp

    
    
                      
  
 Transcriber     Visual     
  (Whisper)      Analyzer   
  
                      
    
             
    
     Food Extractor   Qwen2.5 LLM
     (店家資訊擷取)   
    
             
             
    
     Google Places   
     (地點搜尋驗證)   
    
             
             
    
     Telegram Reply  
     + Google Maps   
    
```

## 注意事項

- 處理時間約 1-3 分鐘（取決於影片長度）
- 辨識準確度取決於影片中店家資訊的清晰度
- 建議使用有明確店名的美食介紹影片
- Google Places API 有免費額度限制

## 相關專案

- [instagram-reels-summarizer](../instagram-reels-summarizer) - IG Reels 摘要筆記工具
