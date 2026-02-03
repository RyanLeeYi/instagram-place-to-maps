# Instagram Place → Google Maps 專案規格

## 專案概述

將 Instagram 內容（Reels 影片、圖片貼文）中的餐廳/景點/店家資訊自動擷取，並同步到 Google Sheets，可連動 Google My Maps。

## 支援的內容類型

| 類型 | URL 格式 | 處理方式 |
|------|----------|---------|
| Reel | `instagram.com/reel/xxx` | 影片下載 → 語音轉文字 → 視覺分析 |
| Reels | `instagram.com/reels/xxx` | 同上 |
| 貼文（圖片）| `instagram.com/p/xxx` | 圖片下載 → 視覺分析 + 貼文說明 |
| 貼文（影片）| `instagram.com/p/xxx` | 自動偵測並切換到影片流程 |
| IGTV | `instagram.com/tv/xxx` | 影片下載 → 語音轉文字 → 視覺分析 |
| 分享連結 | `instagram.com/share/xxx` | 自動偵測內容類型 |

## 核心功能

### 1. 影片分析
- 接收 Instagram Reels/IGTV 連結
- 下載影片並分析內容
- 使用視覺模型辨識：
  - 店家名稱（招牌、菜單 logo）
  - 食物/景點類型
  - 店家環境特徵
- 使用語音轉文字擷取：
  - 口述的店家名稱
  - 地址資訊
  - 推薦原因

### 2. 圖片貼文分析
- 接收 Instagram 貼文連結
- 下載圖片（支援輪播圖，最多 5 張）
- 擷取貼文說明文字（caption）
- 使用視覺模型分析圖片內容
- 結合說明文字與圖片分析結果

### 3. 地點搜尋與驗證
- 使用 Google Places API (New) 搜尋店家
- 取得正式地址、評分、評論數
- 生成精確的 Google Maps 連結

### 4. Google Sheets 同步
- 自動寫入 Google Sheets
- 可連動 Google My Maps 顯示地點
- 記錄完整資訊（名稱、地址、類型、來源等）

### 5. Telegram Bot 互動
- 接收 IG 連結
- 即時狀態更新
- 回覆結構化結果
- 訊息去重防止重複處理

## 技術架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram Bot                              │
│                  (接收 IG 連結)                               │
│         支援：Reel / 貼文 / IGTV / 分享連結                    │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  Content Processor                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              URL Type Detection                       │   │
│  │     判斷 reel/post/tv/share → 選擇處理流程            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  【影片流程】              │  【圖片貼文流程】              │
│  ┌──────────────┐         │  ┌──────────────┐              │
│  │   Downloader │         │  │ Post Downloader│             │
│  │   (yt-dlp)   │         │  │ (instaloader) │              │
│  └──────────────┘         │  └──────────────┘              │
│         │                  │         │                       │
│         ▼                  │         ▼                       │
│  ┌──────────────┐         │  ┌──────────────┐              │
│  │  Transcriber │         │  │ Caption 擷取  │              │
│  │  (Whisper)   │         │  │ (貼文說明)    │              │
│  └──────────────┘         │  └──────────────┘              │
│         │                  │         │                       │
│         ▼                  │         ▼                       │
│  ┌──────────────┐         │  ┌──────────────┐              │
│  │Visual Analyzer│        │  │Image Analyzer │              │
│  │ (MiniCPM-V)  │         │  │ (MiniCPM-V)   │              │
│  └──────────────┘         │  └──────────────┘              │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                 Place Extractor                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  LLM (Qwen2.5) - 擷取結構化地點資訊                    │   │
│  │  - 地點名稱 (中/英文)                                  │   │
│  │  - 城市/國家                                           │   │
│  │  - 地點類型 (餐廳/咖啡廳/景點)                          │   │
│  │  - 亮點與推薦原因                                      │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                Google Places Service                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Text Search  │  │Place Details │  │ Maps URL Gen │       │
│  │  (New API)   │  │ (地址/評分)   │  │              │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   Output Layer                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   SQLite DB  │  │Google Sheets │  │ Telegram Bot │       │
│  │  (本地儲存)   │  │  (雲端同步)   │  │  (回覆結果)   │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## API 需求

### Google Cloud APIs
1. **Places API (New)** - 搜尋店家
   - Text Search（文字搜尋）
   - 回傳：地址、座標、評分、評論數

### Google Sheets API
- 使用 Service Account 認證
- 自動寫入地點資料
- 可連動 Google My Maps

### 整合方案

#### 目前實作：Google Sheets + My Maps
- Bot 處理完成後自動寫入 Google Sheets
- Google My Maps 匯入 Sheets 資料顯示地圖
- 優點：免費、易於管理、可分享

#### 備選方案
- **KML 匯出**：定期匯出成 KML 檔案
- **Notion 整合**：存入 Notion Database

## 資料模型

### PlaceInfo (目前實作)

```python
@dataclass
class PlaceInfo:
    """地點資訊（LLM 擷取結果）"""
    name: str                    # 地點名稱
    name_en: Optional[str]       # 英文名稱
    city: Optional[str]          # 城市
    country: Optional[str]       # 國家
    place_type: Optional[str]    # 類型 (餐廳/咖啡廳/景點...)
    highlights: Optional[str]    # 亮點/推薦原因
    search_keywords: List[str]   # Google 搜尋關鍵字
    
    # Google Places API 補充
    address: Optional[str]       # 完整地址
    google_place_id: Optional[str]  # Google Place ID
    google_maps_url: Optional[str]  # Google Maps 連結
    rating: Optional[float]      # 評分
    reviews_count: Optional[int] # 評論數
    latitude: Optional[float]    # 緯度
    longitude: Optional[float]   # 經度
```

### ExtractedPlace (資料庫模型)

```python
class ExtractedPlace(Base):
    id: int                      # 主鍵
    instagram_url: str           # IG 來源連結
    place_name: str              # 地點名稱
    place_name_en: Optional[str] # 英文名稱
    city: Optional[str]          # 城市
    country: Optional[str]       # 國家
    place_type: Optional[str]    # 類型
    address: Optional[str]       # 地址
    google_place_id: Optional[str]
    google_maps_url: Optional[str]
    rating: Optional[float]      # 評分
    reviews_count: Optional[int] # 評論數
    highlights: Optional[str]    # 亮點
    raw_transcript: Optional[str]     # 原始語音文字
    raw_visual_description: Optional[str]  # 原始視覺描述
    raw_llm_response: Optional[str]   # 原始 LLM 回應
    created_at: datetime
    updated_at: datetime
```

### 資料庫 Schema

```sql
CREATE TABLE extracted_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_url TEXT NOT NULL UNIQUE,
    place_name TEXT NOT NULL,
    place_name_en TEXT,
    city TEXT,
    country TEXT,
    place_type TEXT,
    address TEXT,
    google_place_id TEXT,
    google_maps_url TEXT,
    rating REAL,
    reviews_count INTEGER,
    highlights TEXT,
    raw_transcript TEXT,
    raw_visual_description TEXT,
    raw_llm_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Prompt 設計

### 地點資訊擷取 Prompt

```
你是一個專業的地點資訊擷取助手。請從以下內容中擷取餐廳/咖啡廳/景點/店家資訊。

【語音內容】（如有）
{transcript}

【畫面/圖片描述】
{visual_description}

請以 JSON 格式回覆：

{
  "found": true/false,
  "confidence": "high/medium/low",
  "place": {
    "name": "地點名稱",
    "name_en": "英文名稱（如有）",
    "city": "城市",
    "country": "國家",
    "place_type": "類型（餐廳/咖啡廳/景點/酒吧...）",
    "highlights": "亮點與推薦原因"
  },
  "search_keywords": ["用於 Google 搜尋的關鍵字"],
  "notes": "其他備註"
}

注意：
1. 如果無法確定是地點相關內容，請設 found 為 false
2. 儘量從畫面中的招牌、菜單擷取正確店名
3. 根據口音、貨幣、環境推測可能的城市/國家
4. 貼文說明可能包含 #hashtag 或 @mentions，注意擷取有用資訊
```

## 專案結構

```
instagram-place-to-maps/
├── app/
│   ├── __init__.py
│   ├── config.py              # 設定管理 (環境變數)
│   ├── main.py                # FastAPI 主程式 + Webhook
│   ├── bot/
│   │   ├── __init__.py
│   │   └── handlers.py        # Telegram Bot 處理邏輯
│   ├── database/
│   │   ├── __init__.py
│   │   └── models.py          # SQLAlchemy 資料模型
│   ├── services/
│   │   ├── __init__.py
│   │   ├── downloader.py      # 影片/圖片下載 (yt-dlp + instaloader)
│   │   ├── transcriber.py     # 語音轉文字 (faster-whisper)
│   │   ├── visual_analyzer.py # 視覺分析 (MiniCPM-V via Ollama)
│   │   ├── place_extractor.py # LLM 擷取地點資訊 (Qwen2.5)
│   │   ├── google_places.py   # Google Places API (New)
│   │   └── google_sheets.py   # Google Sheets 同步
│   └── prompts/
│       └── __init__.py
├── scripts/                   # 測試腳本
├── temp_videos/               # 暫存下載的影片/圖片
├── credentials.json           # Google Service Account
├── cookies.txt                # Instagram cookies (yt-dlp)
├── requirements.txt
├── README.md
├── start.bat                  # Windows 啟動腳本
└── start.ps1                  # PowerShell 啟動腳本
```

## 環境變數

```env
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321  # 允許的 Chat ID

# Webhook 設定
WEBHOOK_URL=https://your-domain.com
WEBHOOK_SECRET=your_webhook_secret

# Whisper 語音轉文字
WHISPER_MODEL_SIZE=base    # tiny/base/small/medium/large
WHISPER_DEVICE=cpu         # cpu/cuda

# Ollama LLM
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b           # 文字 LLM
OLLAMA_VISION_MODEL=minicpm-v     # 視覺 LLM

# Google APIs
GOOGLE_PLACES_API_KEY=your_places_api_key
GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json
GOOGLE_SHEETS_ID=your_spreadsheet_id

# Instagram 下載
YTDLP_COOKIES_FILE=cookies.txt
INSTALOADER_SESSION_DIR=./instaloader_sessions

# 系統設定
TEMP_VIDEO_DIR=./temp_videos
DATABASE_URL=sqlite+aiosqlite:///./places.db
```

## 目前已實作功能 (MVP)

### 內容處理
- ✅ 接收 Instagram Reel 連結 (`/reel/`)
- ✅ 接收 Instagram 貼文連結 (`/p/`)
- ✅ 接收 Instagram IGTV 連結 (`/tv/`)
- ✅ 接收 Instagram 分享連結 (`/share/`)
- ✅ 自動偵測連結類型並選擇處理流程

### 影片處理
- ✅ 使用 yt-dlp 下載影片
- ✅ 使用 faster-whisper 語音轉文字
- ✅ 使用 MiniCPM-V 分析影片畫面

### 圖片處理
- ✅ 使用 instaloader 下載貼文圖片
- ✅ 擷取貼文說明 (caption)
- ✅ 使用 MiniCPM-V 分析圖片內容
- ✅ 結合說明文字與圖片分析

### 地點擷取
- ✅ 使用 Qwen2.5 LLM 擷取結構化地點資訊
- ✅ 使用 Google Places API (New) 搜尋驗證
- ✅ 取得地址、評分、評論數
- ✅ 生成 Google Maps 連結

### 資料儲存
- ✅ 存入本地 SQLite 資料庫
- ✅ 自動同步到 Google Sheets
- ✅ 可連動 Google My Maps

### Telegram Bot
- ✅ Webhook 模式運作
- ✅ 即時狀態更新
- ✅ MarkdownV2 格式回覆
- ✅ 訊息去重防止重複處理

## 待開發功能 (Phase 2)

1. 🔲 支援批次處理多個連結
2. 🔲 匯出 KML 檔案給 Google Earth
3. 🔲 Notion 整合
4. 🔲 重複地點偵測
5. 🔲 依城市/類型分類瀏覽
6. 🔲 支援 IG Story Highlights
