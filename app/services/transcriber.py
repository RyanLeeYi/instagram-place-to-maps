"""Whisper 語音轉錄服務 (使用 faster-whisper 本地模型)"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from faster_whisper import WhisperModel

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """轉錄結果"""

    success: bool
    transcript: Optional[str] = None
    language: Optional[str] = None
    error_message: Optional[str] = None


class WhisperTranscriber:
    """
    Whisper 語音轉錄服務

    使用 faster-whisper 在本地運行，無需 API Key
    """

    # 語言代碼映射
    LANGUAGE_MAP = {
        "zh": "zh-TW",
        "chinese": "zh-TW",
        "en": "en",
        "english": "en",
        "ja": "ja",
        "japanese": "ja",
        "ko": "ko",
        "korean": "ko",
    }

    def __init__(self):
        self._model: Optional[WhisperModel] = None

    def _get_model(self) -> WhisperModel:
        """延遲載入模型（首次使用時才載入）"""
        if self._model is None:
            logger.info(
                f"載入 Whisper 模型: {settings.whisper_model_size} "
                f"(裝置: {settings.whisper_device})"
            )
            
            # 根據裝置選擇計算類型
            if settings.whisper_device == "cuda":
                compute_type = "float16"
            else:
                compute_type = "int8"
            
            self._model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=compute_type,
            )
            logger.info("Whisper 模型載入完成")
        
        return self._model

    def _transcribe_sync(self, audio_path: Path) -> TranscriptionResult:
        """同步轉錄方法"""
        try:
            model = self._get_model()
            
            # 執行轉錄
            segments, info = model.transcribe(
                str(audio_path),
                beam_size=5,
                language=None,  # 自動偵測語言
                vad_filter=True,  # 過濾靜音段落
            )

            # 收集所有文字
            transcript_parts: List[str] = []
            for segment in segments:
                transcript_parts.append(segment.text.strip())

            transcript = " ".join(transcript_parts).strip()

            # 檢查是否有實際內容
            if not transcript:
                return TranscriptionResult(
                    success=False,
                    error_message="此影片無可辨識的語音內容",
                )

            # 映射語言代碼
            detected_language = self.LANGUAGE_MAP.get(
                info.language, info.language
            )

            logger.info(
                f"轉錄成功，偵測語言: {detected_language} "
                f"(信心度: {info.language_probability:.2%})"
            )
            
            return TranscriptionResult(
                success=True,
                transcript=transcript,
                language=detected_language,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"轉錄失敗: {error_msg}")

            return TranscriptionResult(
                success=False,
                error_message=f"轉錄失敗: {error_msg}",
            )

    async def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """
        將音訊檔案轉錄為文字

        Args:
            audio_path: 音訊檔案路徑

        Returns:
            TranscriptionResult: 轉錄結果
        """
        if not audio_path.exists():
            return TranscriptionResult(
                success=False,
                error_message="音訊檔案不存在",
            )

        # 在執行緒池中執行同步轉錄（避免阻塞事件循環）
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_path)

    def detect_language(self, audio_path: Path) -> Optional[str]:
        """
        偵測音訊語言（不進行完整轉錄）

        Args:
            audio_path: 音訊檔案路徑

        Returns:
            語言代碼或 None
        """
        try:
            model = self._get_model()
            _, info = model.transcribe(
                str(audio_path),
                beam_size=1,  # 快速偵測
                vad_filter=False,
            )
            return self.LANGUAGE_MAP.get(info.language, info.language)
        except Exception as e:
            logger.warning(f"語言偵測失敗: {e}")
            return None
