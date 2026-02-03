# Playwright Google Maps Saver 實作計畫

## 專案概述

整合 Playwright 自動化瀏覽器功能，讓系統在取得 Google Places 資訊後，能自動將地點儲存到使用者的 Google Maps 清單中（如「想去」清單）。

### 目前系統流程

```
Instagram URL → 下載內容 → AI 分析 → 擷取地點 → Google Places API 搜尋 → 產生 Maps 連結 → 回傳 Telegram
```

### 新增功能後流程

```
Instagram URL → 下載內容 → AI 分析 → 擷取地點 → Google Places API 搜尋 → 產生 Maps 連結 → [Playwright 儲存至清單] → 回傳 Telegram（含儲存狀態）
```

---

## 實作步驟

### Step 1: 新增 Playwright 依賴

**檔案：** `requirements.txt`

**變更：** 在檔案末尾新增

```txt
# Browser Automation
playwright>=1.40.0
```

---

### Step 2: 更新啟動腳本

#### 2.1 修改 `start.ps1`

**位置：** 在步驟 4 (檢查虛擬環境) 之後、步驟 5 之前插入新步驟

**新增內容：**

```powershell
# 步驟 4.5: 檢查 Playwright 瀏覽器
Write-Host "[4.5/6] Checking Playwright browsers..." -ForegroundColor Yellow
$playwrightCheck = & ".\.venv\Scripts\python.exe" -c "from playwright.sync_api import sync_playwright; print('ok')" 2>&1
if ($playwrightCheck -ne "ok") {
    Write-Host "      Installing Playwright browsers..." -ForegroundColor Gray
    & ".\.venv\Scripts\playwright.exe" install chromium
}
Write-Host "      Playwright OK" -ForegroundColor Green
```

**注意：** 同時更新步驟編號 `[4/5]` → `[4/6]`，`[5/5]` → `[6/6]`

---

### Step 3: 新增設定項目

**檔案：** `app/config.py`

**變更：** 在 `Settings` 類別中新增以下欄位

```python
# Google Maps 自動儲存設定
google_maps_save_enabled: bool = Field(default=False, env="GOOGLE_MAPS_SAVE_ENABLED")
google_maps_default_list: str = Field(default="想去", env="GOOGLE_MAPS_DEFAULT_LIST")
playwright_state_path: str = Field(default="./browser_state", env="PLAYWRIGHT_STATE_PATH")
playwright_delay_min: float = Field(default=2.0, env="PLAYWRIGHT_DELAY_MIN")
playwright_delay_max: float = Field(default=5.0, env="PLAYWRIGHT_DELAY_MAX")
```

**新增 property 方法：**

```python
@property
def playwright_state_dir(self) -> Path:
    """取得 Playwright 瀏覽器狀態目錄路徑"""
    path = Path(self.playwright_state_path)
    path.mkdir(parents=True, exist_ok=True)
    return path
```

---

### Step 4: 建立 Google Maps Saver 服務

**檔案：** `app/services/google_maps_saver.py` （新建）

**完整實作：**

```python
"""Google Maps 自動儲存服務 - 使用 Playwright 自動化"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional, Literal
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    """儲存結果"""
    success: bool
    status: Literal["saved", "already_saved", "failed", "not_logged_in", "disabled"]
    message: str = ""


class GoogleMapsSaver:
    """Google Maps 地點儲存服務
    
    使用 Playwright 自動化瀏覽器，將地點儲存到使用者的 Google Maps 清單。
    
    使用流程：
    1. 首次使用時呼叫 interactive_login() 開啟瀏覽器讓使用者登入
    2. 登入成功後自動儲存 session state
    3. 後續呼叫 save_to_list() 使用 headless 模式自動儲存
    """
    
    GOOGLE_MAPS_URL = "https://www.google.com/maps"
    LOGIN_CHECK_URL = "https://www.google.com/maps/@0,0,2z"
    
    def __init__(self):
        self.state_path = settings.playwright_state_dir / "google_state.json"
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
    
    def is_enabled(self) -> bool:
        """檢查功能是否啟用"""
        return settings.google_maps_save_enabled
    
    def is_logged_in(self) -> bool:
        """檢查是否已有儲存的登入狀態"""
        return self.state_path.exists()
    
    async def _random_delay(self, multiplier: float = 1.0):
        """加入隨機延遲，避免被偵測為機器人"""
        delay = random.uniform(
            settings.playwright_delay_min * multiplier,
            settings.playwright_delay_max * multiplier
        )
        await asyncio.sleep(delay)
    
    async def interactive_login(self) -> SaveResult:
        """開啟可見瀏覽器讓使用者手動登入 Google
        
        流程：
        1. 開啟 Chromium 瀏覽器（非 headless）
        2. 導航至 Google Maps
        3. 等待使用者手動登入
        4. 偵測登入成功後儲存 session state
        5. 關閉瀏覽器
        
        Returns:
            SaveResult: 登入結果
        """
        logger.info("開始互動式 Google 登入流程...")
        
        try:
            async with async_playwright() as p:
                # 開啟可見瀏覽器
                browser = await p.chromium.launch(
                    headless=False,
                    args=['--start-maximized']
                )
                
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-TW'
                )
                
                page = await context.new_page()
                
                # 導航至 Google Maps
                logger.info("導航至 Google Maps...")
                await page.goto(self.GOOGLE_MAPS_URL)
                await self._random_delay()
                
                # 等待使用者登入（最多等待 5 分鐘）
                logger.info("等待使用者登入 Google 帳戶... (最多 5 分鐘)")
                
                try:
                    # 等待出現登入後才有的元素（例如頭像按鈕）
                    # Google Maps 登入後會顯示頭像按鈕
                    await page.wait_for_selector(
                        'button[aria-label*="Google 帳戶"], button[aria-label*="Google Account"], img[aria-label*="Google 帳戶"]',
                        timeout=300000  # 5 分鐘
                    )
                    
                    logger.info("偵測到已登入！儲存 session state...")
                    await self._random_delay(0.5)
                    
                    # 儲存 session state
                    await context.storage_state(path=str(self.state_path))
                    logger.info(f"Session state 已儲存至: {self.state_path}")
                    
                    await browser.close()
                    
                    return SaveResult(
                        success=True,
                        status="saved",
                        message="Google 帳戶登入成功！已儲存登入狀態。"
                    )
                    
                except PlaywrightTimeout:
                    logger.warning("等待登入超時")
                    await browser.close()
                    return SaveResult(
                        success=False,
                        status="failed",
                        message="登入超時，請在 5 分鐘內完成登入。"
                    )
                    
        except Exception as e:
            logger.exception(f"互動式登入失敗: {e}")
            return SaveResult(
                success=False,
                status="failed",
                message=f"登入失敗: {str(e)}"
            )
    
    async def save_to_list(
        self, 
        place_id: str, 
        list_name: Optional[str] = None
    ) -> SaveResult:
        """將地點儲存到 Google Maps 清單
        
        Args:
            place_id: Google Place ID
            list_name: 清單名稱，預設使用設定中的 google_maps_default_list
            
        Returns:
            SaveResult: 儲存結果
        """
        if not self.is_enabled():
            return SaveResult(
                success=False,
                status="disabled",
                message="Google Maps 自動儲存功能未啟用"
            )
        
        if not self.is_logged_in():
            return SaveResult(
                success=False,
                status="not_logged_in",
                message="尚未登入 Google 帳戶，請先執行 /setup_google"
            )
        
        list_name = list_name or settings.google_maps_default_list
        place_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        
        logger.info(f"儲存地點 {place_id} 至清單「{list_name}」...")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                
                # 載入已儲存的 session state
                context = await browser.new_context(
                    storage_state=str(self.state_path),
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-TW'
                )
                
                page = await context.new_page()
                
                # 導航至地點頁面
                logger.info(f"導航至地點頁面: {place_url}")
                await page.goto(place_url)
                await self._random_delay()
                
                # 等待頁面載入
                await page.wait_for_load_state('networkidle')
                await self._random_delay(0.5)
                
                # 點擊「儲存」按鈕
                save_button = await self._find_save_button(page)
                if not save_button:
                    await browser.close()
                    return SaveResult(
                        success=False,
                        status="failed",
                        message="找不到儲存按鈕"
                    )
                
                await save_button.click()
                await self._random_delay()
                
                # 選擇或建立清單
                result = await self._select_or_create_list(page, list_name)
                
                await browser.close()
                return result
                
        except Exception as e:
            logger.exception(f"儲存地點失敗: {e}")
            return SaveResult(
                success=False,
                status="failed",
                message=f"儲存失敗: {str(e)}"
            )
    
    async def _find_save_button(self, page: Page):
        """尋找儲存按鈕"""
        # 嘗試多種可能的選擇器
        selectors = [
            'button[aria-label*="儲存"]',
            'button[aria-label*="Save"]',
            'button[data-value="儲存"]',
            'button[data-value="Save"]',
            '[aria-label*="儲存到清單"]',
            '[aria-label*="Save to list"]',
        ]
        
        for selector in selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=5000)
                if button:
                    return button
            except PlaywrightTimeout:
                continue
        
        return None
    
    async def _select_or_create_list(self, page: Page, list_name: str) -> SaveResult:
        """選擇或建立清單"""
        try:
            # 等待清單選項出現
            await page.wait_for_selector('[role="menu"], [role="listbox"]', timeout=5000)
            await self._random_delay(0.5)
            
            # 嘗試找到指定的清單
            list_item = await page.query_selector(f'text="{list_name}"')
            
            if list_item:
                # 檢查是否已勾選（已儲存過）
                parent = await list_item.evaluate_handle('el => el.closest("[role=menuitemcheckbox], [role=option]")')
                if parent:
                    is_checked = await parent.evaluate('el => el.getAttribute("aria-checked") === "true"')
                    if is_checked:
                        return SaveResult(
                            success=True,
                            status="already_saved",
                            message=f"此地點已在「{list_name}」清單中"
                        )
                
                # 點擊選擇清單
                await list_item.click()
                await self._random_delay()
                
                return SaveResult(
                    success=True,
                    status="saved",
                    message=f"已儲存至「{list_name}」"
                )
            else:
                # 清單不存在，嘗試建立新清單
                logger.info(f"清單「{list_name}」不存在，嘗試建立...")
                
                new_list_button = await page.query_selector('text="新增清單", text="New list"')
                if new_list_button:
                    await new_list_button.click()
                    await self._random_delay()
                    
                    # 輸入清單名稱
                    name_input = await page.wait_for_selector('input[aria-label*="名稱"], input[aria-label*="Name"]', timeout=3000)
                    if name_input:
                        await name_input.fill(list_name)
                        await self._random_delay(0.5)
                        
                        # 點擊建立/儲存按鈕
                        create_button = await page.query_selector('button:has-text("建立"), button:has-text("Create"), button:has-text("儲存"), button:has-text("Save")')
                        if create_button:
                            await create_button.click()
                            await self._random_delay()
                            
                            return SaveResult(
                                success=True,
                                status="saved",
                                message=f"已建立清單「{list_name}」並儲存"
                            )
                
                return SaveResult(
                    success=False,
                    status="failed",
                    message=f"找不到清單「{list_name}」且無法建立新清單"
                )
                
        except PlaywrightTimeout:
            return SaveResult(
                success=False,
                status="failed",
                message="操作超時"
            )
        except Exception as e:
            return SaveResult(
                success=False,
                status="failed",
                message=f"選擇清單失敗: {str(e)}"
            )
    
    async def clear_session(self) -> bool:
        """清除已儲存的 session state"""
        try:
            if self.state_path.exists():
                self.state_path.unlink()
                logger.info("已清除 Google 登入狀態")
                return True
            return False
        except Exception as e:
            logger.error(f"清除 session 失敗: {e}")
            return False


# 建立全域實例
google_maps_saver = GoogleMapsSaver()
```

---

### Step 5: 更新 services `__init__.py`

**檔案：** `app/services/__init__.py`

**內容：**

```python
"""Services 模組"""

from app.services.google_maps_saver import GoogleMapsSaver, google_maps_saver, SaveResult

__all__ = [
    "GoogleMapsSaver",
    "google_maps_saver", 
    "SaveResult",
]
```

---

### Step 6: 整合至 handlers.py

#### 6.1 新增 import

**檔案：** `app/bot/handlers.py`

**位置：** 在現有 import 區塊末尾新增

```python
from app.services.google_maps_saver import google_maps_saver, SaveResult
```

#### 6.2 新增 `/setup_google` 指令處理器

**位置：** 在 `help_handler` 方法之後新增

```python
async def setup_google_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理 /setup_google 指令 - 設定 Google Maps 自動儲存"""
    chat_id = update.effective_chat.id
    
    if not self._is_authorized(chat_id):
        await update.message.reply_text("⛔ 未授權的使用者")
        return
    
    if not google_maps_saver.is_enabled():
        await update.message.reply_text(
            "⚠️ Google Maps 自動儲存功能未啟用\n\n"
            "請在 .env 中設定：\n"
            "`GOOGLE_MAPS_SAVE_ENABLED=true`",
            parse_mode="Markdown"
        )
        return
    
    if google_maps_saver.is_logged_in():
        await update.message.reply_text(
            "✅ 已登入 Google 帳戶\n\n"
            "如需重新登入，請先執行 /logout_google",
            parse_mode="Markdown"
        )
        return
    
    status_message = await update.message.reply_text(
        "🔐 正在開啟瀏覽器...\n\n"
        "請在彈出的瀏覽器視窗中登入 Google 帳戶。\n"
        "登入成功後將自動儲存登入狀態。\n\n"
        "⏱️ 請在 5 分鐘內完成登入。"
    )
    
    # 執行互動式登入
    result = await google_maps_saver.interactive_login()
    
    if result.success:
        await status_message.edit_text(
            f"✅ {result.message}\n\n"
            f"現在處理的地點將自動儲存至「{settings.google_maps_default_list}」清單。"
        )
    else:
        await status_message.edit_text(f"❌ {result.message}")

async def logout_google_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理 /logout_google 指令 - 清除 Google 登入狀態"""
    chat_id = update.effective_chat.id
    
    if not self._is_authorized(chat_id):
        await update.message.reply_text("⛔ 未授權的使用者")
        return
    
    if await google_maps_saver.clear_session():
        await update.message.reply_text("✅ 已清除 Google 登入狀態")
    else:
        await update.message.reply_text("ℹ️ 沒有已儲存的登入狀態")
```

#### 6.3 更新 `/start` 歡迎訊息

**位置：** `start_handler` 方法中的 `welcome_message`

**修改：** 在指令列表中新增

```python
welcome_message = """🗺️ **探索地圖 Bot**

歡迎使用！傳送 Instagram Reels 連結給我，我會：

1. 分析影片內容
2. 擷取餐廳/景點/店家資訊
3. 提供 Google Maps 連結
4. 自動儲存至你的 Maps 清單 ✨

**使用方式：**
直接貼上 IG Reels 連結即可

**指令：**
/start - 顯示說明
/list - 查看已儲存的地點
/setup_google - 設定 Google Maps 自動儲存
/logout_google - 清除 Google 登入狀態
/mychatid - 查詢你的 Chat ID
/help - 使用說明"""
```

#### 6.4 在地點處理流程中加入自動儲存

**位置：** `message_handler` 方法中，在 `# 8. 回覆結果` 之前（約第 530 行附近）

**新增：**

```python
# 7.5 自動儲存至 Google Maps
maps_save_results = []
if google_maps_saver.is_enabled() and google_maps_saver.is_logged_in():
    for item in processed_places:
        place_result = item["place_result"]
        if place_result.place_id:
            save_result = await google_maps_saver.save_to_list(place_result.place_id)
            maps_save_results.append({
                "place_name": item["place_info"].name,
                "result": save_result
            })
```

#### 6.5 更新回應訊息格式

**位置：** 在建立回應訊息的邏輯中（單一地點和多地點兩處）

**單一地點（約第 560 行）：** 在 `if safe_address:` 之後新增

```python
# 顯示 Maps 儲存狀態
if maps_save_results:
    save_result = maps_save_results[0]["result"]
    if save_result.status == "saved":
        lines.append(f"💾 已儲存至「{escape_markdown(settings.google_maps_default_list)}」")
    elif save_result.status == "already_saved":
        lines.append(f"ℹ️ 已在「{escape_markdown(settings.google_maps_default_list)}」清單中")
    elif save_result.status == "failed":
        lines.append(f"⚠️ 儲存失敗：{escape_markdown(save_result.message)}")
```

**多地點（約第 595 行）：** 在每個地點的區塊末尾新增儲存狀態

```python
# 在多地點迴圈中，找到對應的儲存結果
for save_item in maps_save_results:
    if save_item["place_name"] == place_info.name:
        sr = save_item["result"]
        if sr.status == "saved":
            lines.append(f"   💾 已儲存")
        elif sr.status == "already_saved":
            lines.append(f"   ℹ️ 已在清單中")
        break
```

---

### Step 7: 註冊新指令

**檔案：** `app/main.py` 或 Bot 初始化位置

**找到註冊指令的位置，新增：**

```python
application.add_handler(CommandHandler("setup_google", handlers.setup_google_handler))
application.add_handler(CommandHandler("logout_google", handlers.logout_google_handler))
```

---

### Step 8: 更新 .env.example

**檔案：** `.env.example`（如果存在）或在 `.env` 中新增註解

```env
# ===== Google Maps 自動儲存設定 =====
# 是否啟用自動儲存至 Google Maps 清單
GOOGLE_MAPS_SAVE_ENABLED=false

# 預設儲存的清單名稱
GOOGLE_MAPS_DEFAULT_LIST=想去

# Playwright 瀏覽器狀態儲存路徑
PLAYWRIGHT_STATE_PATH=./browser_state

# 自動化操作延遲（秒），用於避免被偵測
PLAYWRIGHT_DELAY_MIN=2.0
PLAYWRIGHT_DELAY_MAX=5.0
```

---

## 檔案變更總覽

| 檔案 | 操作 | 說明 |
|------|------|------|
| `requirements.txt` | 修改 | 新增 `playwright>=1.40.0` |
| `start.ps1` | 修改 | 新增 Playwright 安裝檢查步驟 |
| `app/config.py` | 修改 | 新增 5 個設定欄位 + 1 個 property |
| `app/services/google_maps_saver.py` | **新建** | 完整的 GoogleMapsSaver 類別 |
| `app/services/__init__.py` | 修改 | 匯出新服務 |
| `app/bot/handlers.py` | 修改 | 新增 2 個指令處理器 + 整合儲存邏輯 |
| `app/main.py` | 修改 | 註冊新指令 |
| `.env` / `.env.example` | 修改 | 新增設定項目 |

---

## 測試計畫

### 測試案例

1. **首次設定流程**
   - 執行 `/setup_google`
   - 確認瀏覽器開啟
   - 手動登入 Google
   - 確認登入成功訊息
   - 確認 `browser_state/google_state.json` 已建立

2. **自動儲存功能**
   - 設定 `GOOGLE_MAPS_SAVE_ENABLED=true`
   - 傳送 Instagram 連結
   - 確認處理完成後顯示儲存狀態
   - 開啟 Google Maps 確認地點已在清單中

3. **重複儲存處理**
   - 傳送已處理過的地點
   - 確認顯示「已在清單中」

4. **登出流程**
   - 執行 `/logout_google`
   - 確認登入狀態已清除
   - 再次執行 `/setup_google` 可重新登入

5. **功能停用時**
   - 設定 `GOOGLE_MAPS_SAVE_ENABLED=false`
   - 確認不會嘗試儲存
   - 確認回應訊息不顯示儲存狀態

---

## 注意事項

### Google 登入安全

- Google 可能偵測到自動化登入並要求額外驗證
- 建議使用應用程式專用密碼或信任的裝置
- 首次登入後 session 通常可維持數週

### 速率限制

- 每次操作之間有 2-5 秒隨機延遲
- 避免短時間內大量儲存操作
- 如被封鎖，需等待數小時後重試

### UI 變更處理

- Google Maps 介面可能更新
- 選擇器需要定期維護
- 建議加入 fallback 選擇器

### Headless 模式注意

- 某些情況下 Google 可能偵測 headless 模式
- 如遇問題可嘗試設定 `headless=False` 進行除錯

---

## 依賴版本

```
playwright>=1.40.0
```

執行安裝：
```bash
pip install playwright
playwright install chromium
```

---

## 相關文件

- [Playwright Python 文件](https://playwright.dev/python/)
- [Google Maps 網頁版](https://www.google.com/maps)
- 現有程式碼：`app/services/google_places.py`（Google Places API 服務）
