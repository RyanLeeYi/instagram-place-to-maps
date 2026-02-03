"""Telegram Bot 處理器"""

import asyncio
import logging
import re
from typing import Optional, Set

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TimedOut, NetworkError
from telegram.ext import ContextTypes

from app.config import settings
from app.services.downloader import InstagramDownloader
from app.services.transcriber import WhisperTranscriber
from app.services.visual_analyzer import VideoVisualAnalyzer
from app.services.place_extractor import PlaceExtractor, PlaceInfo, ExtractionResult
from app.services.google_places import GooglePlacesService
from app.services.google_sheets import GoogleSheetsService
from app.services.google_maps_saver import google_maps_saver, SaveResult
from app.database.models import Place, async_session


logger = logging.getLogger(__name__)


async def safe_edit_message(message: Message, text: str, max_retries: int = 2, **kwargs) -> bool:
    """
    安全地編輯訊息，帶有重試機制
    
    Args:
        message: 要編輯的訊息
        text: 新的文字內容
        max_retries: 最大重試次數
        **kwargs: 其他傳給 edit_text 的參數
        
    Returns:
        bool: 是否成功
    """
    for attempt in range(max_retries + 1):
        try:
            await message.edit_text(text, **kwargs)
            return True
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries:
                logger.warning(f"編輯訊息超時，重試 {attempt + 1}/{max_retries}...")
                await asyncio.sleep(1)  # 等待 1 秒後重試
            else:
                logger.error(f"編輯訊息失敗（已重試 {max_retries} 次）: {e}")
                return False
        except Exception as e:
            logger.error(f"編輯訊息發生錯誤: {e}")
            return False
    return False


def escape_markdown(text: str) -> str:
    """跳脫 Markdown 特殊字元"""
    if not text:
        return ""
    # 跳脫 Markdown 特殊字元
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


class PlaceBotHandlers:
    """探索地圖 Bot 處理器"""
    
    # Instagram URL 正則 - 支援 reel, reels, p (貼文), tv (IGTV)
    INSTAGRAM_URL_PATTERN = re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)"
    )
    
    # Reel/影片專用 pattern
    INSTAGRAM_REEL_PATTERN = re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:reel|reels|tv)/([A-Za-z0-9_-]+)"
    )
    
    # 貼文專用 pattern
    INSTAGRAM_POST_PATTERN = re.compile(
        r"https?://(?:www\.)?instagram\.com/p/([A-Za-z0-9_-]+)"
    )
    
    # 也支援分享連結格式 (短網址)
    INSTAGRAM_SHARE_PATTERN = re.compile(
        r"https?://(?:www\.)?instagram\.com/share/([A-Za-z0-9_-]+)"
    )
    
    # 正在處理中的訊息 ID（用於去重 - 處理中）
    _processing_messages: Set[int] = set()
    
    # 已處理過的訊息 ID（用於去重 - 已完成）
    _processed_message_ids: Set[int] = set()
    
    # 記憶體快取上限
    _MAX_PROCESSED_IDS = 1000
    
    def __init__(self):
        self.downloader = InstagramDownloader()
        self.transcriber = WhisperTranscriber()
        self.visual_analyzer = VideoVisualAnalyzer()
        self.place_extractor = PlaceExtractor()
        self.places_service = GooglePlacesService()
        self.sheets_service = GoogleSheetsService()
    
    def _is_authorized(self, chat_id: int) -> bool:
        """檢查是否為授權用戶"""
        allowed_ids = settings.allowed_chat_ids
        if not allowed_ids:
            return True  # 未設定則允許所有人
        return str(chat_id) in allowed_ids
    
    def _get_url_type(self, url: str) -> str:
        """
        判斷 Instagram URL 類型
        
        Returns:
            "reel" - Reel/影片
            "post" - 貼文（可能是圖片或影片）
            "share" - 分享連結
            "unknown" - 未知
        """
        if self.INSTAGRAM_REEL_PATTERN.match(url):
            return "reel"
        elif self.INSTAGRAM_POST_PATTERN.match(url):
            return "post"
        elif self.INSTAGRAM_SHARE_PATTERN.match(url):
            return "share"
        return "unknown"
    
    def _extract_ig_url(self, text: str) -> Optional[str]:
        """從訊息中擷取 Instagram URL"""
        # 先嘗試標準格式
        match = self.INSTAGRAM_URL_PATTERN.search(text)
        if match:
            return match.group(0)
        
        # 嘗試分享連結格式
        match = self.INSTAGRAM_SHARE_PATTERN.search(text)
        if match:
            return match.group(0)
        
        return None
    
    def _extract_account_name(self, url: str) -> Optional[str]:
        """從 URL 擷取帳號名稱（需要實際下載後才能取得）"""
        # 暫時回傳 None，實際帳號在下載時取得
        return None
    
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /start 指令"""
        chat_id = update.effective_chat.id
        
        if not self._is_authorized(chat_id):
            await update.message.reply_text(" 未授權的使用者")
            return
        
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
/frames - 切換分析幀數模式
/savelist - 切換 Google Maps 儲存清單
/setup\_google - 設定 Google Maps 自動儲存
/logout\_google - 清除 Google 登入狀態
/mychatid - 查詢你的 Chat ID
/help - 使用說明"""
        
        await update.message.reply_text(welcome_message, parse_mode="Markdown")
    
    async def mychatid_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /mychatid 指令 - 顯示用戶的 Chat ID"""
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        message = f"🆔 *你的 Chat ID：* `{chat_id}`\n"
        if user:
            message += f"👤 *使用者：* {escape_markdown(user.full_name)}\n"
            if user.username:
                message += f"📛 *Username：* @{escape_markdown(user.username)}\n"
        
        message += "\n請將 Chat ID 提供給 Bot 管理員以取得使用權限。"
        
        await update.message.reply_text(message, parse_mode="MarkdownV2")
    
    async def frames_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        處理 /frames 指令 - 切換影片分析幀數間隔
        
        用法：
            /frames - 顯示當前設定
            /frames auto - 自動模式（根據影片長度決定 8-10 幀）
            /frames fast - 快速模式（每 3 秒一幀）
            /frames normal - 標準模式（每 2 秒一幀）
            /frames detailed - 詳細模式（每 1 秒一幀）
            /frames 1.5 - 自訂間隔（秒）
        """
        from app.config import runtime_settings
        
        chat_id = update.effective_chat.id
        
        if not self._is_authorized(chat_id):
            await update.message.reply_text("❌ 未授權的使用者")
            return
        
        args = context.args
        
        if not args:
            # 顯示當前設定
            current_mode = runtime_settings.get_current_mode()
            current_mode_key = current_mode.lower()
            
            if runtime_settings.use_auto_mode:
                mode_desc = "根據影片長度自動決定 8\\-10 幀"
            else:
                interval = runtime_settings.frame_interval_seconds
                mode_desc = f"每 `{interval}` 秒截取一幀"
            
            # 建立 inline keyboard（標記目前選中的模式）
            keyboard = [
                [
                    InlineKeyboardButton("🤖 Auto" + (" ✓" if current_mode_key == "auto" else ""), callback_data="frames_auto"),
                    InlineKeyboardButton("⚡ Fast" + (" ✓" if current_mode_key == "fast" else ""), callback_data="frames_fast"),
                ],
                [
                    InlineKeyboardButton("📊 Normal" + (" ✓" if current_mode_key == "normal" else ""), callback_data="frames_normal"),
                    InlineKeyboardButton("🔍 Detailed" + (" ✓" if current_mode_key == "detailed" else ""), callback_data="frames_detailed"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = f"""⚙️ *影片分析幀數設定*

📊 *目前模式：* `{current_mode}`
⏱️ *說明：* {mode_desc}

💡 點擊下方按鈕快速切換模式，或輸入 `/frames 1\\.5` 自訂間隔"""
            
            await update.message.reply_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
            return
        
        mode = args[0].lower()
        
        if runtime_settings.set_frame_interval(mode):
            current_mode = runtime_settings.get_current_mode()
            
            if mode == "auto":
                await update.message.reply_text(
                    f"✅ 已切換至 *{current_mode}* 模式\n"
                    f"📊 根據影片長度自動決定 8-10 幀",
                    parse_mode="Markdown"
                )
            else:
                interval = runtime_settings.frame_interval_seconds
                await update.message.reply_text(
                    f"✅ 已切換至 *{current_mode}* 模式\n"
                    f"⏱️ 每 `{interval}` 秒截取一幀",
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                "❌ 無效的模式\n\n"
                "可用選項：`auto`、`fast`、`normal`、`detailed` 或 `0.5-10` 之間的數字",
                parse_mode="Markdown"
            )
    
    async def frames_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /frames inline keyboard 按鈕點擊"""
        from app.config import runtime_settings
        
        query = update.callback_query
        await query.answer()  # 確認收到 callback
        
        chat_id = update.effective_chat.id
        if not self._is_authorized(chat_id):
            await query.edit_message_text("❌ 未授權的使用者")
            return
        
        # 解析 callback data: "frames_auto", "frames_fast", etc.
        if not query.data.startswith("frames_"):
            return
        
        mode = query.data.replace("frames_", "")
        
        if runtime_settings.set_frame_interval(mode):
            current_mode = runtime_settings.get_current_mode()
            
            # 重新建立 keyboard 以更新顯示
            keyboard = [
                [
                    InlineKeyboardButton("🤖 Auto" + (" ✓" if mode == "auto" else ""), callback_data="frames_auto"),
                    InlineKeyboardButton("⚡ Fast" + (" ✓" if mode == "fast" else ""), callback_data="frames_fast"),
                ],
                [
                    InlineKeyboardButton("📊 Normal" + (" ✓" if mode == "normal" else ""), callback_data="frames_normal"),
                    InlineKeyboardButton("🔍 Detailed" + (" ✓" if mode == "detailed" else ""), callback_data="frames_detailed"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if mode == "auto":
                mode_desc = "根據影片長度自動決定 8\\-10 幀"
            else:
                interval = runtime_settings.frame_interval_seconds
                mode_desc = f"每 `{interval}` 秒截取一幀"
            
            message = f"""⚙️ *影片分析幀數設定*

📊 *目前模式：* `{current_mode}`
⏱️ *說明：* {mode_desc}

✅ 已切換至 *{current_mode}* 模式"""
            
            await query.edit_message_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
        else:
            await query.edit_message_text("❌ 切換失敗")
    
    async def savelist_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        處理 /savelist 指令 - 設定 Google Maps 儲存清單
        
        自動讀取用戶的 Google Maps 清單，以按鈕形式顯示供選擇
        """
        from app.config import runtime_settings, settings
        
        chat_id = update.effective_chat.id
        
        if not self._is_authorized(chat_id):
            await update.message.reply_text("❌ 未授權的使用者")
            return
        
        # 顯示載入中訊息
        loading_msg = await update.message.reply_text("⏳ 正在讀取 Google Maps 清單...")
        
        # 獲取用戶的清單
        result = await google_maps_saver.get_saved_lists()
        
        current_list = runtime_settings.google_maps_list
        
        if not result.success or not result.lists:
            # 無法獲取清單，顯示錯誤訊息
            keyboard = [
                [InlineKeyboardButton("🔄 重新讀取", callback_data="savelist_refresh")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            error_msg = result.message if result.message else "無法讀取清單"
            message = f"""📋 *Google Maps 儲存清單設定*

📍 *目前清單：* `{self._escape_markdown(current_list)}`

⚠️ {self._escape_markdown(error_msg)}

請確認：
• 已執行 `/setup_google` 登入 Google 帳戶
• Google Maps 中有建立至少一個清單"""
            
            await loading_msg.edit_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
            return
        
        # 建立清單按鈕（每行2個）
        keyboard = []
        row = []
        for list_name in result.lists:
            # 標記目前選中的清單
            display_name = f"✓ {list_name}" if list_name == current_list else list_name
            # callback_data 有長度限制，使用索引
            callback_data = f"savelist_select_{result.lists.index(list_name)}"
            row.append(InlineKeyboardButton(display_name, callback_data=callback_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        # 添加重新讀取按鈕
        keyboard.append([InlineKeyboardButton("🔄 重新讀取", callback_data="savelist_refresh")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # 儲存清單到 context 供 callback 使用
        context.user_data['saved_lists'] = result.lists
        
        message = f"""📋 *Google Maps 儲存清單設定*

📍 *目前清單：* `{self._escape_markdown(current_list)}`

請選擇要儲存地點的目標清單："""
        
        await loading_msg.edit_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
    
    async def savelist_callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /savelist inline keyboard 按鈕點擊"""
        from app.config import runtime_settings
        
        query = update.callback_query
        
        chat_id = update.effective_chat.id
        if not self._is_authorized(chat_id):
            await query.answer("❌ 未授權的使用者")
            return
        
        if query.data == "savelist_refresh":
            # 重新讀取清單
            await query.answer("正在重新讀取...")
            await query.edit_message_text("⏳ 正在重新讀取 Google Maps 清單...")
            
            result = await google_maps_saver.get_saved_lists()
            current_list = runtime_settings.google_maps_list
            
            if not result.success or not result.lists:
                keyboard = [
                    [InlineKeyboardButton("🔄 重新讀取", callback_data="savelist_refresh")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                error_msg = result.message if result.message else "無法讀取清單"
                message = f"""📋 *Google Maps 儲存清單設定*

📍 *目前清單：* `{self._escape_markdown(current_list)}`

⚠️ {self._escape_markdown(error_msg)}"""
                
                await query.edit_message_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
                return
            
            # 建立清單按鈕
            keyboard = []
            row = []
            for list_name in result.lists:
                display_name = f"✓ {list_name}" if list_name == current_list else list_name
                callback_data = f"savelist_select_{result.lists.index(list_name)}"
                row.append(InlineKeyboardButton(display_name, callback_data=callback_data))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([InlineKeyboardButton("🔄 重新讀取", callback_data="savelist_refresh")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            context.user_data['saved_lists'] = result.lists
            
            message = f"""📋 *Google Maps 儲存清單設定*

📍 *目前清單：* `{self._escape_markdown(current_list)}`

請選擇要儲存地點的目標清單："""
            
            await query.edit_message_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
            
        elif query.data.startswith("savelist_select_"):
            # 選擇清單
            try:
                index = int(query.data.replace("savelist_select_", ""))
                saved_lists = context.user_data.get('saved_lists', [])
                
                if 0 <= index < len(saved_lists):
                    selected_list = saved_lists[index]
                    runtime_settings.set_google_maps_list(selected_list)
                    
                    await query.answer(f"✅ 已選擇「{selected_list}」")
                    
                    # 更新按鈕顯示
                    keyboard = []
                    row = []
                    for i, list_name in enumerate(saved_lists):
                        display_name = f"✓ {list_name}" if list_name == selected_list else list_name
                        callback_data = f"savelist_select_{i}"
                        row.append(InlineKeyboardButton(display_name, callback_data=callback_data))
                        if len(row) == 2:
                            keyboard.append(row)
                            row = []
                    if row:
                        keyboard.append(row)
                    keyboard.append([InlineKeyboardButton("🔄 重新讀取", callback_data="savelist_refresh")])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    message = f"""📋 *Google Maps 儲存清單設定*

📍 *目前清單：* `{self._escape_markdown(selected_list)}`

✅ 已切換至「{self._escape_markdown(selected_list)}」清單"""
                    
                    await query.edit_message_text(message, parse_mode="MarkdownV2", reply_markup=reply_markup)
                else:
                    await query.answer("❌ 無效的選擇，請重新讀取")
            except (ValueError, IndexError):
                await query.answer("❌ 發生錯誤，請重新讀取")
    
    def _escape_markdown(self, text: str) -> str:
        """轉義 MarkdownV2 特殊字元"""
        escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text
    
    async def help_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /help 指令"""
        help_message = """✨ *使用說明*

*支援的連結格式：*
 https://instagram.com/reel/xxx
 https://instagram.com/reels/xxx
 https://instagram.com/p/xxx（影片貼文）

*處理流程：*
1. 下載影片
2. 語音轉文字
3. 畫面分析
4. 擷取店家資訊
5. 搜尋 Google Maps

*設定指令：*
• `/frames` - 設定影片分析幀數
• `/savelist` - 設定 Google Maps 儲存清單

*注意事項：*
 處理時間約 1-3 分鐘
 結果準確度取決於影片內容清晰度
 建議傳送有明確店名的美食介紹影片"""
        
        await update.message.reply_text(help_message, parse_mode="Markdown")
    
    async def list_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理 /list 指令 - 列出已儲存的地點"""
        chat_id = update.effective_chat.id
        
        if not self._is_authorized(chat_id):
            return
        
        async with async_session() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(Place)
                .where(Place.telegram_chat_id == str(chat_id))
                .order_by(Place.created_at.desc())
                .limit(10)
            )
            places = result.scalars().all()
        
        if not places:
            await update.message.reply_text("💭 尚未儲存任何地點")
            return
        
        message = "📍 *最近儲存的地點：*\n\n"
        for i, place in enumerate(places, 1):
            place_types = ", ".join(place.get_place_types()) if place.get_place_types() else ""
            safe_name = escape_markdown(place.name)
            safe_city = escape_markdown(place.city or "")
            safe_types = escape_markdown(place_types)
            safe_maps_url = escape_markdown(place.google_maps_url or "")
            
            message += f"{i}\\. *{safe_name}*"
            if safe_city:
                message += f" ({safe_city})"
            if safe_types:
                message += f"\n    {safe_types}"
            if safe_maps_url:
                message += f"\n    [Google Maps]({safe_maps_url})"
            message += "\n\n"
        
        await update.message.reply_text(message, parse_mode="MarkdownV2", disable_web_page_preview=True)
    
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
                "如需重新登入，請先執行 /logout\\_google",
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
                f"✅ {escape_markdown(result.message)}\n\n"
                f"現在處理的地點將自動儲存至「{escape_markdown(settings.google_maps_default_list)}」清單。",
                parse_mode="MarkdownV2"
            )
        else:
            await status_message.edit_text(f"❌ {escape_markdown(result.message)}", parse_mode="MarkdownV2")

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
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """處理一般訊息（包含 IG 連結）"""
        # === 輔助過濾條件 ===
        
        # 忽略沒有訊息的更新
        if not update.message:
            return
        
        # 忽略編輯過的訊息（編輯會觸發另一個更新事件）
        if update.edited_message:
            return
        
        # 忽略 Bot 自己的訊息
        if update.message.from_user and update.message.from_user.is_bot:
            logger.debug("忽略 Bot 自己的訊息")
            return
        
        # 忽略回覆訊息（防止 Bot 回覆中的連結被誤認為新連結）
        if update.message.reply_to_message:
            logger.debug("忽略回覆訊息")
            return
        
        chat_id = update.effective_chat.id
        message_id = update.message.message_id
        message_text = update.message.text or ""
        
        # 忽略空訊息
        if not message_text.strip():
            return
        
        # === 訊息 ID 去重機制 ===
        
        # 檢查是否已處理過（永久去重）
        if message_id in self._processed_message_ids:
            logger.debug(f"訊息 ID {message_id} 已處理過，跳過")
            return
        
        # 檢查是否正在處理中（併發去重）
        if message_id in self._processing_messages:
            logger.info(f"訊息 {message_id} 已在處理中，跳過重複請求")
            return
        
        # 在處理開始前立即標記（防止併發重入）
        self._processed_message_ids.add(message_id)
        self._processing_messages.add(message_id)
        
        # 記憶體管理：超過上限時清理較舊的一半
        if len(self._processed_message_ids) > self._MAX_PROCESSED_IDS:
            ids_list = sorted(self._processed_message_ids)
            self._processed_message_ids = set(ids_list[self._MAX_PROCESSED_IDS // 2:])
            logger.info(f"已清理舊的訊息 ID 快取，目前數量: {len(self._processed_message_ids)}")
        
        logger.info(f"開始處理訊息 {message_id}")
        
        if not self._is_authorized(chat_id):
            await update.message.reply_text("⛔ 未授權的使用者")
            self._processing_messages.discard(message_id)
            return
        
        # 擷取 IG 連結
        ig_url = self._extract_ig_url(message_text)
        if not ig_url:
            await update.message.reply_text(
                "❌ 請傳送有效的 Instagram 連結\n"
                "支援格式：\n"
                "• instagram.com/reel/xxx\n"
                "• instagram.com/p/xxx"
            )
            self._processing_messages.discard(message_id)
            return
        
        # 判斷 URL 類型
        url_type = self._get_url_type(ig_url)
        logger.info(f"訊息 {message_id} 包含 IG 連結: {ig_url} (類型: {url_type})")
        
        # 開始處理
        status_message = await update.message.reply_text("⏳ 正在處理...")
        
        try:
            # 用於追蹤內容類型
            is_image_post = False
            post_result = None
            download_result = None
            transcript = ""
            visual_description = ""
            post_caption = ""
            source_title = ""
            
            if url_type == "post":
                # === 貼文處理流程 ===
                # 先嘗試下載圖片（因為 /p/ 大多是圖片貼文）
                await safe_edit_message(status_message, "🖼️ 正在下載貼文...")
                post_result = await self.downloader.download_post(ig_url)
                
                if post_result.success:
                    # 圖片貼文
                    is_image_post = True
                    post_caption = post_result.caption or ""
                    source_title = post_result.title or ""
                    
                    # 並行分析圖片（使用 analyze_images 方法）
                    await safe_edit_message(status_message, "🔍 正在分析圖片...")
                    images_to_analyze = post_result.image_paths[:5]  # 最多分析 5 張
                    images_result = await self.visual_analyzer.analyze_images(images_to_analyze)
                    
                    visual_description = images_result.overall_visual_summary if images_result.success else ""
                    
                    # 貼文說明文字作為主要文字來源
                    transcript = post_caption
                    
                    # 將貼文說明也加入視覺描述，確保 LLM 可以參考
                    if post_caption:
                        visual_description = f"【貼文說明】\n{post_caption}\n\n【圖片內容】\n{visual_description}" if visual_description else f"【貼文說明】\n{post_caption}"
                    
                elif post_result.content_type == "reel":
                    # 其實是影片貼文，切換到影片流程
                    logger.info("貼文為影片，切換到影片處理流程")
                    await safe_edit_message(status_message, "🎬 偵測為影片貼文，正在下載...")
                    download_result = await self.downloader.download(ig_url)
                    
                    if not download_result.success:
                        await safe_edit_message(status_message, f"❌ 下載失敗：{download_result.error_message}")
                        return
                else:
                    await safe_edit_message(status_message, f"❌ 下載失敗：{post_result.error_message}")
                    return
                    
            else:
                # === Reel/影片處理流程 ===
                await safe_edit_message(status_message, "🎬 正在下載影片...")
                download_result = await self.downloader.download(ig_url)
                
                if not download_result.success:
                    # 影片下載失敗，嘗試作為圖片貼文處理（可能是分享連結）
                    logger.info("影片下載失敗，嘗試作為圖片貼文處理...")
                    await safe_edit_message(status_message, "🖼️ 正在嘗試其他方式...")
                    
                    post_result = await self.downloader.download_post(ig_url)
                    
                    if post_result.success:
                        is_image_post = True
                        post_caption = post_result.caption or ""
                        source_title = post_result.title or ""
                        
                        # 並行分析圖片（使用 analyze_images 方法）
                        await safe_edit_message(status_message, "🔍 正在分析圖片...")
                        images_to_analyze = post_result.image_paths[:5]
                        images_result = await self.visual_analyzer.analyze_images(images_to_analyze)
                        
                        visual_description = images_result.overall_visual_summary if images_result.success else ""
                        transcript = post_caption
                        
                        # 將貼文說明也加入視覺描述
                        if post_caption:
                            visual_description = f"【貼文說明】\n{post_caption}\n\n【圖片內容】\n{visual_description}" if visual_description else f"【貼文說明】\n{post_caption}"
                    else:
                        await safe_edit_message(status_message, f"❌ 下載失敗：{download_result.error_message}")
                        return
            
            # 如果是影片且成功下載
            if download_result and download_result.success:
                source_title = download_result.title or ""
                # 取得影片說明文（caption）
                video_caption = download_result.caption or ""
                if video_caption:
                    logger.info(f"取得影片說明文，長度: {len(video_caption)} 字元")
                
                # 語音轉文字 + 視覺分析（並行處理）
                await safe_edit_message(status_message, "🎤👁️ 正在分析語音與畫面...")
                
                # 建立並行任務
                transcript_task = asyncio.create_task(
                    self.transcriber.transcribe(download_result.audio_path)
                )
                visual_task = asyncio.create_task(
                    self.visual_analyzer.analyze(download_result.video_path)
                )
                
                # 等待兩個任務完成
                transcript_result, visual_result = await asyncio.gather(
                    transcript_task, visual_task
                )
                
                transcript = transcript_result.transcript if transcript_result.success else ""
                visual_description = visual_result.overall_visual_summary if visual_result.success else ""
                # 將影片說明文設為 post_caption 供後續使用
                post_caption = video_caption
            
            # 4. 擷取地點資訊
            await safe_edit_message(status_message, "🔍 正在擷取地點資訊...")
            extraction_result = await self.place_extractor.extract(
                transcript=transcript,
                visual_description=visual_description,
                ig_account=source_title,
                caption=post_caption  # 傳入貼文/影片說明文
            )
            
            if not extraction_result.found:
                await safe_edit_message(
                    status_message,
                    "❓ 無法辨識為餐廳/景點相關內容\n\n"
                    f"📝 備註：{extraction_result.notes or '無法從內容中擷取地點資訊'}"
                )
                return
            
            # 5. 處理每個地點
            place_count = extraction_result.place_count
            await safe_edit_message(status_message, f"🗺️ 找到 {place_count} 個地點，正在搜尋 Google Maps...")
            
            # 儲存處理結果
            processed_places = []
            
            # 準備所有地點的搜尋查詢
            async def search_place_with_info(place_info: PlaceInfo):
                """搜尋單一地點並回傳 (place_info, place_result) 元組"""
                search_query = place_info.search_keywords[0] if place_info.search_keywords else place_info.name
                if place_info.city and place_info.name:
                    search_query = f"{place_info.name} {place_info.city}"
                place_result = await self.places_service.search_place(search_query)
                return (place_info, place_result)
            
            # 並行搜尋所有地點的 Google Maps
            search_tasks = [
                search_place_with_info(place_info)
                for place_info in extraction_result.places
            ]
            search_results = await asyncio.gather(*search_tasks)
            
            # 依序處理搜尋結果（儲存資料庫、同步 Sheets）
            for place_info, place_result in search_results:
                
                # 儲存到資料庫
                async with async_session() as session:
                    new_place = Place(
                        name=place_info.name or "未知地點",
                        name_en=place_info.name_en,
                        address=place_result.address if place_result.found else place_info.address,
                        city=place_info.city,
                        country=place_info.country,
                        latitude=place_result.latitude,
                        longitude=place_result.longitude,
                        google_place_id=place_result.place_id,
                        google_maps_url=place_result.google_maps_url,
                        source_url=ig_url,
                        source_account=source_title,
                        telegram_chat_id=str(chat_id),
                        recommendation=place_info.recommendation,
                        confidence=place_info.confidence,
                        status="confirmed" if place_result.found else "pending"
                    )
                    new_place.set_place_types(place_info.place_type)
                    new_place.set_highlights(place_info.highlights)
                    new_place.set_tags(place_info.tags)
                    
                    session.add(new_place)
                    await session.commit()
                
                # 同步到 Google Sheets
                if self.sheets_service.is_configured():
                    await self.sheets_service.add_place(
                        name=place_info.name,
                        address=place_result.address if place_result.found else place_info.address,
                        city=place_info.city,
                        country=place_info.country,
                        place_types=place_info.place_type,
                        highlights=place_info.highlights,
                        price_range=place_info.price_range,
                        recommendation=place_info.recommendation,
                        google_maps_url=place_result.google_maps_url,
                        source_url=ig_url
                    )
                
                # 記錄處理結果
                processed_places.append({
                    "place_info": place_info,
                    "place_result": place_result
                })
            
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
            
            # 8. 回覆結果
            if place_count == 1:
                # 單一地點：使用原有格式
                place_info = processed_places[0]["place_info"]
                place_result = processed_places[0]["place_result"]
                
                confidence_emoji = {"high": "✅", "medium": "🟡", "low": "🟠"}.get(place_info.confidence, "")
                display_address = place_result.address or place_info.address
                
                safe_name = escape_markdown(place_info.name or "未知")
                safe_name_en = escape_markdown(place_info.name_en) if place_info.name_en else ""
                safe_city = escape_markdown(place_info.city or "未知")
                safe_country = escape_markdown(place_info.country or "")
                safe_types = escape_markdown(", ".join(place_info.place_type)) if place_info.place_type else "未分類"
                safe_highlights = escape_markdown(", ".join(place_info.highlights)) if place_info.highlights else ""
                safe_price = escape_markdown(place_info.price_range) if place_info.price_range else ""
                safe_recommendation = escape_markdown(place_info.recommendation) if place_info.recommendation else ""
                safe_address = escape_markdown(display_address) if display_address else ""
                safe_maps_url = escape_markdown(place_result.google_maps_url) if place_result.google_maps_url else ""
                safe_confidence = escape_markdown(place_info.confidence) if place_info.confidence else "low"
                
                lines = ["✨ *擷取完成！*", ""]
                lines.append(f"🏪 *地點名稱：* {safe_name}")
                if safe_name_en:
                    lines.append(f"🆎 *英文名：* {safe_name_en}")
                lines.append(f"📍 *地區：* {safe_city}, {safe_country}")
                lines.append(f"🏷️ *類型：* {safe_types}")
                if safe_highlights:
                    lines.append(f"⭐ *亮點：* {safe_highlights}")
                if safe_price:
                    lines.append(f"💰 *價位：* {safe_price}")
                if safe_recommendation:
                    lines.append(f"💬 *推薦原因：* {safe_recommendation}")
                lines.append("")
                lines.append(f"{confidence_emoji} *辨識信心度：* {safe_confidence}")
                lines.append("")
                lines.append(f"🗺️ *Google Maps：*")
                lines.append(safe_maps_url)
                lines.append("")
                if place_result.rating:
                    lines.append(f"⭐ 評分：{escape_markdown(str(place_result.rating))} \\({place_result.user_ratings_total} 則評論\\)")
                if safe_address:
                    lines.append(f"🏠 地址：{safe_address}")
                
                # 顯示 Maps 儲存狀態
                if maps_save_results:
                    save_result = maps_save_results[0]["result"]
                    if save_result.status == "saved":
                        lines.append(f"💾 已儲存至「{escape_markdown(settings.google_maps_default_list)}」")
                    elif save_result.status == "already_saved":
                        lines.append(f"ℹ️ 已在「{escape_markdown(settings.google_maps_default_list)}」清單中")
                    elif save_result.status == "failed":
                        lines.append(f"⚠️ 儲存失敗：{escape_markdown(save_result.message)}")
            else:
                # 多個地點：使用精簡格式
                lines = [f"✨ *擷取完成！找到 {place_count} 個地點*", ""]
                
                for idx, item in enumerate(processed_places, 1):
                    place_info = item["place_info"]
                    place_result = item["place_result"]
                    
                    confidence_emoji = {"high": "✅", "medium": "🟡", "low": "🟠"}.get(place_info.confidence, "")
                    safe_name = escape_markdown(place_info.name or "未知")
                    safe_city = escape_markdown(place_info.city or "")
                    safe_types = escape_markdown(", ".join(place_info.place_type[:2])) if place_info.place_type else ""
                    safe_maps_url = escape_markdown(place_result.google_maps_url) if place_result.google_maps_url else ""
                    
                    lines.append(f"*{idx}\\. {safe_name}* {confidence_emoji}")
                    if safe_city:
                        lines.append(f"   📍 {safe_city}")
                    if safe_types:
                        lines.append(f"   🏷️ {safe_types}")
                    if place_result.rating:
                        lines.append(f"   ⭐ {escape_markdown(str(place_result.rating))}")
                    if safe_maps_url:
                        lines.append(f"   🗺️ {safe_maps_url}")
                    
                    # 在多地點迴圈中，找到對應的儲存結果
                    for save_item in maps_save_results:
                        if save_item["place_name"] == place_info.name:
                            sr = save_item["result"]
                            if sr.status == "saved":
                                lines.append(f"   💾 已儲存")
                            elif sr.status == "already_saved":
                                lines.append(f"   ℹ️ 已在清單中")
                            break
                    
                    lines.append("")
            
            if self.sheets_service.is_configured():
                lines.append("📊 已同步到 Google Sheets")
            
            result_message = "\n".join(lines)
            
            await safe_edit_message(
                status_message, result_message, 
                parse_mode="MarkdownV2", disable_web_page_preview=True
            )
            
            # 清理暫存檔案
            if download_result:
                if download_result.video_path and download_result.video_path.exists():
                    download_result.video_path.unlink()
                if download_result.audio_path and download_result.audio_path.exists():
                    download_result.audio_path.unlink()
            
            if post_result and post_result.image_paths:
                await self.downloader.cleanup_post_images(post_result.image_paths)
                
        except Exception as e:
            logger.exception(f"處理失敗: {e}")
            # 使用安全編輯，避免二次超時
            await safe_edit_message(status_message, f"❌ 處理失敗：{str(e)[:100]}")
        
        finally:
            # 處理完成，從處理中佇列移除（但保留在已處理集合中防止重複）
            self._processing_messages.discard(message_id)
            logger.info(f"訊息 {message_id} 處理完成")
