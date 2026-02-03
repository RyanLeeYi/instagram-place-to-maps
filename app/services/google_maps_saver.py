"""Google Maps 自動儲存服務 - 使用 Playwright 自動化"""

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional, Literal, List
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    """儲存結果"""
    success: bool
    status: Literal["saved", "already_saved", "failed", "not_logged_in", "disabled"]
    message: str = ""


@dataclass
class ListsResult:
    """清單查詢結果"""
    success: bool
    lists: List[str] = field(default_factory=list)
    message: str = ""


class GoogleMapsSaver:
    """Google Maps 地點儲存服務
    
    使用 Playwright 自動化瀏覽器，將地點儲存到使用者的 Google Maps 清單。
    
    使用流程：
    1. 首次使用時呼叫 interactive_login() 開啟瀏覽器讓使用者登入
    2. 登入成功後自動儲存 cookies
    3. 後續呼叫 save_to_list() 使用 headless 模式自動儲存
    """
    
    GOOGLE_MAPS_URL = "https://www.google.com/maps"
    
    def __init__(self):
        self.auth_file = settings.playwright_state_dir / "google_auth.json"
    
    def is_enabled(self) -> bool:
        """檢查功能是否啟用"""
        return settings.google_maps_save_enabled
    
    def is_logged_in(self) -> bool:
        """檢查是否已有儲存的登入狀態"""
        return self.auth_file.exists()
    
    def _load_cookies(self) -> list:
        """載入已儲存的 cookies"""
        if not self.auth_file.exists():
            return []
        try:
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("cookies", [])
        except Exception as e:
            logger.error(f"載入 cookies 失敗: {e}")
            return []
    
    def _save_cookies(self, cookies: list):
        """儲存 cookies"""
        try:
            with open(self.auth_file, 'w', encoding='utf-8') as f:
                json.dump({"cookies": cookies}, f, indent=2, ensure_ascii=False)
            logger.info(f"Cookies 已儲存至: {self.auth_file}")
        except Exception as e:
            logger.error(f"儲存 cookies 失敗: {e}")
    
    async def _random_delay(self, multiplier: float = 1.0):
        """加入隨機延遲，避免被偵測為機器人"""
        delay = random.uniform(
            settings.playwright_delay_min * multiplier,
            settings.playwright_delay_max * multiplier
        )
        await asyncio.sleep(delay)
    
    async def interactive_login(self) -> SaveResult:
        """開啟可見瀏覽器讓使用者手動登入 Google
        
        使用 FlashSquirrel 的方式：開啟瀏覽器，等待登入，然後儲存 cookies。
        """
        logger.info("開始互動式 Google 登入流程...")
        
        try:
            async with async_playwright() as p:
                # 使用與 FlashSquirrel 相同的方式
                browser = await p.chromium.launch(
                    headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--start-maximized',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-TW',
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                page = await context.new_page()
                
                # 隱藏 WebDriver 標記
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                # 導航至 Google Maps
                logger.info("導航至 Google Maps...")
                await page.goto(self.GOOGLE_MAPS_URL)
                
                # 等待使用者登入（最多等待 5 分鐘）
                logger.info("等待使用者登入 Google 帳戶... (最多 5 分鐘)")
                logger.info("登入完成後，請回到 Google Maps 頁面，確認右上角顯示你的頭像")
                
                try:
                    # 等待出現登入後才有的元素
                    login_selectors = [
                        'button[aria-label*="Google 帳戶"]',
                        'button[aria-label*="Google Account"]',
                        'a[aria-label*="Google 帳戶"]',
                        'img.gb_A',
                        'img.gb_qa',
                    ]
                    
                    selector_string = ', '.join(login_selectors)
                    await page.wait_for_selector(selector_string, timeout=300000)
                    
                    logger.info("偵測到已登入！")
                    
                    # 確保在 Google Maps 頁面
                    current_url = page.url
                    if 'google.com/maps' not in current_url:
                        logger.info("導航回 Google Maps...")
                        await page.goto(self.GOOGLE_MAPS_URL)
                        await asyncio.sleep(2)
                    
                    # 取得並儲存 cookies
                    cookies = await context.cookies()
                    logger.info(f"取得 {len(cookies)} 個 cookies")
                    
                    self._save_cookies(cookies)
                    
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
    
    async def get_saved_lists(self) -> ListsResult:
        """獲取用戶的 Google Maps 已儲存清單

        透過訪問某個地點頁面，點擊儲存按鈕來讀取清單列表
        （因為 /maps/saved URL 已不存在，改用此方法）
        """
        if not self.is_enabled():
            return ListsResult(
                success=False,
                message="Google Maps 自動儲存功能未啟用"
            )

        if not self.is_logged_in():
            return ListsResult(
                success=False,
                message="尚未登入 Google 帳戶，請先執行 /setup_google"
            )

        logger.info("正在獲取 Google Maps 清單...")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                    ]
                )

                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-TW',
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )

                # 載入已儲存的 cookies
                cookies = self._load_cookies()
                if cookies:
                    await context.add_cookies(cookies)

                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                # 使用一個知名地點來打開儲存選單（Google Sydney 作為範例）
                sample_place_url = "https://www.google.com/maps/place/?q=place_id:ChIJN1t_tDeuEmsRUsoyG83frY4"
                logger.info(f"訪問範例地點頁面...")
                await page.goto(sample_place_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                # 找到儲存按鈕
                save_button = await self._find_save_button(page)
                if not save_button:
                    logger.warning("找不到儲存按鈕")
                    await browser.close()
                    return ListsResult(
                        success=False,
                        message="找不到儲存按鈕，可能未正確登入"
                    )

                # 點擊儲存按鈕打開選單
                logger.info("點擊儲存按鈕...")
                await save_button.click()
                await self._random_delay(0.5)

                # 等待選單出現
                try:
                    await page.wait_for_selector('[role="menu"]', timeout=5000)
                except PlaywrightTimeout:
                    logger.warning("儲存選單未出現")
                    await browser.close()
                    return ListsResult(
                        success=False,
                        message="無法打開儲存選單"
                    )

                await asyncio.sleep(1)

                # 讀取清單項目
                lists = []

                def is_valid_list_name(name: str) -> bool:
                    """檢查是否為有效的清單名稱（過濾圖示字符和系統文字）"""
                    if not name or len(name) > 50:
                        return False

                    # 過濾純圖示字符（Unicode 私有區域或特殊符號）
                    # Google 圖示字體通常在 U+E000-U+F8FF (Private Use Area) 或特殊字符範圍
                    filtered_name = ''.join(
                        c for c in name
                        if not (0xE000 <= ord(c) <= 0xF8FF)  # Private Use Area
                        and not (0xF000 <= ord(c) <= 0xFFFF)  # Supplementary Private Use
                        and ord(c) >= 32  # 過濾控制字符
                    ).strip()

                    if not filtered_name or len(filtered_name) < 1:
                        return False

                    # 過濾系統文字
                    skip_words = ['新增清單', '新清單', 'New list', '建立新清單', '儲存至清單中', 'Save to list']
                    if any(sw.lower() == filtered_name.lower() for sw in skip_words):
                        return False

                    return True

                def clean_list_name(name: str) -> str:
                    """清理清單名稱，移除圖示字符"""
                    return ''.join(
                        c for c in name
                        if not (0xE000 <= ord(c) <= 0xF8FF)
                        and not (0xF000 <= ord(c) <= 0xFFFF)
                        and ord(c) >= 32
                    ).strip()

                # 尋找 menuitemradio 或 menuitemcheckbox 項目
                menu_items = await page.query_selector_all('[role="menu"] [role="menuitemradio"], [role="menu"] [role="menuitemcheckbox"]')
                logger.info(f"找到 {len(menu_items)} 個選單項目")

                for item in menu_items:
                    try:
                        text = await item.inner_text()
                        lines = text.strip().split('\n')
                        if lines:
                            # 嘗試每一行找有效的清單名稱
                            for line in lines:
                                name = clean_list_name(line)
                                if is_valid_list_name(name) and name not in lists:
                                    lists.append(name)
                                    logger.info(f"找到清單: {name}")
                                    break  # 只取第一個有效名稱
                    except Exception as e:
                        continue

                # 如果上面沒找到，嘗試遍歷所有選單子元素
                if not lists:
                    menu = await page.query_selector('[role="menu"]')
                    if menu:
                        all_items = await menu.query_selector_all('*')
                        for item in all_items:
                            try:
                                text = await item.inner_text()
                                # 只取單行且長度合適的文字
                                if text and '\n' not in text:
                                    name = clean_list_name(text)
                                    if is_valid_list_name(name) and name not in lists:
                                        lists.append(name)
                            except:
                                continue

                # 按 Escape 關閉選單
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.5)

                await browser.close()

                if lists:
                    logger.info(f"找到 {len(lists)} 個清單: {lists}")
                    return ListsResult(
                        success=True,
                        lists=lists,
                        message=f"找到 {len(lists)} 個清單"
                    )
                else:
                    logger.warning("未找到任何清單")
                    return ListsResult(
                        success=False,
                        lists=[],
                        message="未找到任何清單，請確認已在 Google Maps 建立清單"
                    )

        except Exception as e:
            logger.exception(f"獲取清單失敗: {e}")
            return ListsResult(
                success=False,
                message=f"獲取清單失敗: {str(e)}"
            )
    
    async def save_to_list(
        self, 
        place_id: str, 
        list_name: Optional[str] = None
    ) -> SaveResult:
        """將地點儲存到 Google Maps 清單"""
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
        
        # 優先使用 runtime_settings 的設定，再使用參數，最後使用 .env 預設值
        from app.config import runtime_settings
        if list_name is None:
            list_name = runtime_settings.google_maps_list
        place_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        
        logger.info(f"儲存地點 {place_id} 至清單「{list_name}」...")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    locale='zh-TW',
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                # 載入已儲存的 cookies
                cookies = self._load_cookies()
                if cookies:
                    await context.add_cookies(cookies)
                    logger.info(f"已載入 {len(cookies)} 個 cookies")
                
                page = await context.new_page()
                
                # 隱藏 WebDriver 標記
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                # 導航至地點頁面
                logger.info(f"導航至地點頁面: {place_url}")
                await page.goto(place_url, wait_until='domcontentloaded')
                await self._random_delay()
                
                # 等待頁面載入
                await asyncio.sleep(5)
                
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
            menu_selector = '[role="menu"]'
            await page.wait_for_selector(menu_selector, timeout=5000)
            await self._random_delay(0.5)

            # 方法1: 遍歷選單中所有元素，找到包含清單名稱的項目
            items = await page.query_selector_all(f'{menu_selector} *')
            logger.info(f"選單中有 {len(items)} 個元素")

            list_item = None
            for item in items:
                try:
                    text = await item.inner_text()
                    # 檢查是否包含清單名稱，且文字長度合理（排除整個選單）
                    if list_name in text and len(text) < 100:
                        # 取得第一行作為清單名稱
                        first_line = text.split('\n')[0].strip()
                        if first_line == list_name:
                            # 檢查是否已勾選
                            is_checked = await item.evaluate("""el => {
                                const parent = el.closest('[role="menuitemcheckbox"], [role="menuitemradio"], [role="option"]');
                                return parent ? parent.getAttribute('aria-checked') === 'true' : false;
                            }""")

                            if is_checked:
                                logger.info(f"地點已在清單「{list_name}」中")
                                return SaveResult(
                                    success=True,
                                    status="already_saved",
                                    message=f"此地點已在「{list_name}」清單中"
                                )

                            list_item = item
                            logger.info(f"找到清單: {first_line}")
                            break
                except:
                    continue

            if list_item:
                # 點擊清單項目
                logger.info(f"點擊清單: {list_name}")
                try:
                    # 使用 JavaScript 點擊，避免 Playwright 等待導航超時
                    await list_item.evaluate('el => el.click()')
                except Exception as click_err:
                    logger.warning(f"JS 點擊失敗，嘗試強制點擊: {click_err}")
                    try:
                        await list_item.click(force=True, timeout=5000)
                    except Exception as force_err:
                        logger.warning(f"強制點擊也失敗: {force_err}")
                        # 最後嘗試：找到父元素點擊
                        await list_item.evaluate('el => { const p = el.closest("[role]"); if(p) p.click(); else el.click(); }')

                await self._random_delay(1.5)

                return SaveResult(
                    success=True,
                    status="saved",
                    message=f"已儲存至「{list_name}」"
                )

            # 方法2: 嘗試使用 role 選擇器
            role_selectors = [
                f'{menu_selector} [role="menuitemcheckbox"]',
                f'{menu_selector} [role="menuitemradio"]',
                f'{menu_selector} [role="option"]',
            ]

            for role_selector in role_selectors:
                items = await page.query_selector_all(role_selector)
                for item in items:
                    try:
                        text = await item.inner_text()
                        first_line = text.split('\n')[0].strip()
                        if first_line == list_name:
                            is_checked = await item.get_attribute('aria-checked')
                            if is_checked == 'true':
                                logger.info(f"地點已在清單「{list_name}」中")
                                return SaveResult(
                                    success=True,
                                    status="already_saved",
                                    message=f"此地點已在「{list_name}」清單中"
                                )

                            logger.info(f"點擊清單: {list_name}")
                            try:
                                await item.evaluate('el => el.click()')
                            except:
                                await item.click(force=True, timeout=5000)
                            await self._random_delay(1.5)

                            return SaveResult(
                                success=True,
                                status="saved",
                                message=f"已儲存至「{list_name}」"
                            )
                    except:
                        continue

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
        """清除已儲存的登入狀態"""
        try:
            if self.auth_file.exists():
                self.auth_file.unlink()
                logger.info("已清除 Google 登入狀態")
                return True
            return False
        except Exception as e:
            logger.error(f"清除 session 失敗: {e}")
            return False


# 建立全域實例
google_maps_saver = GoogleMapsSaver()
