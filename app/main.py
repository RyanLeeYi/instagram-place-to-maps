"""Instagram to Maps - 主程式"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from telegram.error import TimedOut, NetworkError

from app.config import settings
from app.database.models import init_db
from app.bot.handlers import PlaceBotHandlers


# 設定日誌
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
    force=True  # 強制覆蓋既有設定
)

# 設定第三方套件的 log level
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# Telegram Bot 應用程式
bot_app: Application = None
handlers: PlaceBotHandlers = None


async def error_handler(update: object, context) -> None:
    """
    處理 Telegram Bot 的錯誤
    
    針對網路超時等暫時性錯誤進行記錄，避免重複處理
    """
    error = context.error
    
    if isinstance(error, TimedOut):
        logger.warning(f"Telegram API 超時: {error}")
    elif isinstance(error, NetworkError):
        logger.warning(f"網路錯誤: {error}")
    else:
        logger.error(f"處理更新時發生錯誤: {error}", exc_info=context.error)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命週期管理"""
    global bot_app, handlers
    
    # 初始化資料庫
    logger.info("初始化資料庫...")
    await init_db()
    
    # 初始化 Bot
    logger.info("初始化 Telegram Bot...")
    handlers = PlaceBotHandlers()
    
    bot_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    
    # 註冊處理器
    bot_app.add_handler(CommandHandler("start", handlers.start_handler))
    bot_app.add_handler(CommandHandler("help", handlers.help_handler))
    bot_app.add_handler(CommandHandler("list", handlers.list_handler))
    bot_app.add_handler(CommandHandler("frames", handlers.frames_handler))
    bot_app.add_handler(CommandHandler("savelist", handlers.savelist_handler))
    bot_app.add_handler(CommandHandler("mychatid", handlers.mychatid_handler))
    bot_app.add_handler(CommandHandler("setup_google", handlers.setup_google_handler))
    bot_app.add_handler(CommandHandler("logout_google", handlers.logout_google_handler))
    bot_app.add_handler(CallbackQueryHandler(handlers.frames_callback_handler, pattern="^frames_"))
    bot_app.add_handler(CallbackQueryHandler(handlers.savelist_callback_handler, pattern="^savelist_"))
    bot_app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handlers.message_handler
    ))
    
    # 註冊錯誤處理器
    bot_app.add_error_handler(error_handler)
    
    await bot_app.initialize()
    
    # 清除 webhook 中的舊訊息，並重新設定 webhook
    try:
        # 先刪除 webhook 並清除所有 pending updates
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("已清除 webhook 舊訊息")
        
        # 如果有設定 webhook URL，重新設定
        if settings.webhook_url:
            webhook_url = f"{settings.webhook_url}/webhook"
            await bot_app.bot.set_webhook(url=webhook_url)
            logger.info(f"已設定 Webhook: {webhook_url}")
            await bot_app.start()
        else:
            # Polling 模式
            logger.info("使用 Polling 模式")
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"設定 webhook 失敗: {e}")
    
    logger.info(" 美食地圖 Bot 已啟動！")
    
    yield
    
    # 關閉
    logger.info("關閉 Bot...")
    if bot_app.updater.running:
        await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()


# 建立 FastAPI 應用
app = FastAPI(
    title="Instagram Food to Maps",
    description="將 Instagram 美食 Reels 轉換為 Google Maps 地點",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """根路徑"""
    return {
        "name": "Instagram Food to Maps",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/health")
async def health():
    """健康檢查"""
    return {"status": "healthy"}


@app.post("/webhook")
async def webhook(request: Request):
    """Telegram Webhook 端點"""
    try:
        data = await request.json()
        
        # 在背景處理訊息，不等待完成就先回應 Telegram（避免 webhook 超時導致重試）
        asyncio.create_task(process_update_in_background(data))
        
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook 處理錯誤: {e}")
        # 即使處理失敗也返回 200，避免 Telegram 重試導致循環
        return {"ok": True}


async def process_update_in_background(data: dict):
    """在背景處理 Telegram 更新"""
    try:
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logger.error(f"背景處理更新失敗: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,  # 使用不同 port
        reload=True
    )
