"""應用程式設定模組"""

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """應用程式設定"""

    # Telegram
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_ids: str = Field(default="", env="TELEGRAM_ALLOWED_CHAT_IDS")

    # Whisper 本地模型設定
    whisper_model_size: str = Field(default="base", env="WHISPER_MODEL_SIZE")
    whisper_device: str = Field(default="cpu", env="WHISPER_DEVICE")

    # Ollama 本地 LLM 設定
    ollama_host: str = Field(default="http://localhost:11434", env="OLLAMA_HOST")
    ollama_model: str = Field(default="qwen2.5:7b", env="OLLAMA_MODEL")
    ollama_vision_model: str = Field(default="minicpm-v", env="OLLAMA_VISION_MODEL")

    # Google Places API
    google_places_api_key: str = Field(default="", env="GOOGLE_PLACES_API_KEY")

    # Google Sheets 設定
    google_credentials_path: str = Field(default="./credentials.json", env="GOOGLE_CREDENTIALS_PATH")
    google_sheets_id: str = Field(default="", env="GOOGLE_SHEETS_ID")

    # Webhook 設定
    webhook_url: str = Field(default="", env="WEBHOOK_URL")

    # 系統設定
    temp_video_dir: str = Field(default="./temp_videos", env="TEMP_VIDEO_DIR")
    instaloader_session_dir: str = Field(default="./instaloader_session", env="INSTALOADER_SESSION_DIR")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./food_places.db", env="DATABASE_URL"
    )

    # Google Maps 自動儲存設定
    google_maps_save_enabled: bool = Field(default=False, env="GOOGLE_MAPS_SAVE_ENABLED")
    google_maps_default_list: str = Field(default="想去", env="GOOGLE_MAPS_DEFAULT_LIST")
    playwright_state_path: str = Field(default="./browser_state", env="PLAYWRIGHT_STATE_PATH")
    playwright_delay_min: float = Field(default=2.0, env="PLAYWRIGHT_DELAY_MIN")
    playwright_delay_max: float = Field(default=5.0, env="PLAYWRIGHT_DELAY_MAX")

    @property
    def allowed_chat_ids(self) -> List[str]:
        """解析允許的 chat_id 列表"""
        if not self.telegram_allowed_chat_ids:
            return []
        return [
            chat_id.strip()
            for chat_id in self.telegram_allowed_chat_ids.split(",")
            if chat_id.strip()
        ]

    @property
    def temp_video_path(self) -> Path:
        """取得暫存影片目錄路徑"""
        path = Path(self.temp_video_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def instaloader_session_path(self) -> Path:
        """取得 Instaloader session 目錄路徑"""
        path = Path(self.instaloader_session_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def playwright_state_dir(self) -> Path:
        """取得 Playwright 瀏覽器狀態目錄路徑"""
        path = Path(self.playwright_state_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 建立全域設定實例
settings = Settings()


class RuntimeSettings:
    """
    運行時可調整的設定

    這些設定可以在 Bot 運行中透過命令動態調整
    設定會持久化到檔案，Bot 重啟後仍有效
    """

    # 預設選項（類常數）
    FRAME_INTERVAL_OPTIONS = {
        "auto": None,     # 自動模式：根據影片長度決定 8-10 幀
        "fast": 3.0,      # 快速模式：每 3 秒一幀
        "normal": 2.0,    # 標準模式：每 2 秒一幀
        "detailed": 1.0,  # 詳細模式：每 1 秒一幀
    }

    def __init__(self):
        """初始化運行時設定"""
        import json
        import logging

        self._logger = logging.getLogger(__name__)

        # 實例變數（使用底線前綴）
        self._frame_interval_seconds: float = 2.0
        self._google_maps_list: str = None
        self._use_auto_mode: bool = False

        # 設定檔路徑
        self._settings_file = Path("./runtime_settings.json")

        # 載入已儲存的設定
        self._load_settings()

    def _load_settings(self):
        """從檔案載入設定"""
        import json

        if self._settings_file.exists():
            try:
                with open(self._settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._frame_interval_seconds = data.get('frame_interval_seconds', 2.0)
                    self._google_maps_list = data.get('google_maps_list', None)
                    self._use_auto_mode = data.get('use_auto_mode', False)
                    self._logger.info(f"已載入運行時設定: google_maps_list={self._google_maps_list}")
            except Exception as e:
                self._logger.warning(f"載入運行時設定失敗: {e}")

    def _save_settings(self):
        """儲存設定到檔案"""
        import json

        try:
            data = {
                'frame_interval_seconds': self._frame_interval_seconds,
                'google_maps_list': self._google_maps_list,
                'use_auto_mode': self._use_auto_mode,
            }
            with open(self._settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._logger.info(f"已儲存運行時設定: google_maps_list={self._google_maps_list}")
        except Exception as e:
            self._logger.warning(f"儲存運行時設定失敗: {e}")

    @property
    def frame_interval_seconds(self) -> float:
        """取得幀數間隔秒數"""
        return self._frame_interval_seconds

    @property
    def use_auto_mode(self) -> bool:
        """取得是否使用自動模式"""
        return self._use_auto_mode

    def set_frame_interval(self, mode: str) -> bool:
        """
        設定幀數間隔模式

        Args:
            mode: auto, fast, normal, detailed 或數字（秒數）

        Returns:
            bool: 是否設定成功
        """
        if mode == "auto":
            self._use_auto_mode = True
            self._save_settings()
            return True

        if mode in self.FRAME_INTERVAL_OPTIONS and mode != "auto":
            self._use_auto_mode = False
            self._frame_interval_seconds = self.FRAME_INTERVAL_OPTIONS[mode]
            self._save_settings()
            return True

        # 嘗試解析為數字
        try:
            value = float(mode)
            if 0.5 <= value <= 10.0:
                self._use_auto_mode = False
                self._frame_interval_seconds = value
                self._save_settings()
                return True
        except ValueError:
            pass

        return False

    def get_current_mode(self) -> str:
        """取得目前模式名稱"""
        if self._use_auto_mode:
            return "auto"
        for name, value in self.FRAME_INTERVAL_OPTIONS.items():
            if value is not None and abs(self._frame_interval_seconds - value) < 0.01:
                return name
        return f"{self._frame_interval_seconds}秒"

    @property
    def google_maps_list(self) -> str:
        """取得目前的 Google Maps 清單名稱"""
        if self._google_maps_list is not None:
            return self._google_maps_list
        return settings.google_maps_default_list

    def set_google_maps_list(self, list_name: str) -> bool:
        """
        設定 Google Maps 儲存清單

        Args:
            list_name: 清單名稱

        Returns:
            bool: 是否設定成功
        """
        if not list_name or not list_name.strip():
            return False
        self._google_maps_list = list_name.strip()
        self._save_settings()
        self._logger.info(f"已設定 Google Maps 清單為: {self._google_maps_list}")
        return True

    def reset_google_maps_list(self):
        """重設為 .env 預設清單"""
        self._google_maps_list = None
        self._save_settings()


# 建立全域運行時設定實例
runtime_settings = RuntimeSettings()
