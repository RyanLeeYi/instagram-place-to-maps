"""Gemini 影片理解（Antigravity CLI）——本地 pipeline 之外的第二來源。

本地那條線強在「聽人說了什麼」（whisper 逐字稿），這條線強在「看招牌寫了什麼」
（原生讀 mp4，畫面 + 聲音一起看）。兩邊互補，所以是相加不是取代。

失敗一律降級成「只用本地結果」，不讓 pipeline 中斷——spike 實測 5 支失敗 1 支，
這不是例外情況。
"""

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class GeminiPlace:
    """Gemini 從影片讀到的一個店家。"""

    name: str
    is_recommended: bool = False
    reason: Optional[str] = None


@dataclass
class GeminiVideoResult:
    """與其他 service 同形狀：success + error_message。"""

    success: bool = False
    places: List[GeminiPlace] = field(default_factory=list)
    error_message: Optional[str] = None


class GeminiVideoExtractor:
    """呼叫 `agy` 讀一支影片，回傳它看到的店家。"""

    # agy 的權限引擎（1.1.19）對 read_file 只認「一字不差的絕對路徑」——
    # 實測 `...\temp_videos\*`、`...\*.mp4`、`SideProject\**` 與正斜線版本全部被拒，
    # 唯一放行的萬用字元是 `read_file(*)`，那等於整台機器可讀。
    # 所以固定一個檔名、在 settings.json 只登記這一個路徑，是這裡最小的權限面。
    # 這個鎖是 process 內的：線上只有一隻 bot process，asyncio.Lock 就夠。
    # 但**不要在 bot 還跑著的時候另開一支腳本呼叫這個服務**——兩個 process
    # 會互相蓋掉這個檔案，而且不會有任何錯誤訊息。
    # ponytail: 固定路徑＋process 內鎖，等 agy 的 glob 能用再改成每則請求各自的檔名
    INPUT_FILENAME = "agy_input.mp4"

    PROMPT = (
        "讀這支影片：{path}\n"
        "列出影片裡出現的所有店家/餐廳/景點名稱（含招牌上的字）。\n"
        "對每一個名稱判斷它是不是「這支影片在介紹的店家」：\n"
        "影片主要在拍它、進去消費、特寫招牌或餐點 → is_recommended = true；\n"
        "只是路過畫面帶到的隔壁攤、菜單橫幅、街景招牌 → is_recommended = false。\n"
        "只回 JSON，格式：\n"
        '{{"places":[{{"name":"店名","is_recommended":true,"reason":"一句話理由"}}]}}\n'
        "店名用繁體中文。找不到任何店家就回 {{\"places\":[]}}。"
    )

    def __init__(self, timeout: int = 240):
        self.timeout = timeout
        self.temp_dir = Path(settings.temp_video_path)
        self._lock = asyncio.Lock()

    async def extract(self, video_path: Optional[Path]) -> GeminiVideoResult:
        """讀一支影片。任何失敗都回 success=False，不丟例外。"""
        if not settings.gemini_video_enabled:
            return GeminiVideoResult(error_message="Gemini 影片理解未啟用")
        if not video_path or not Path(video_path).exists():
            return GeminiVideoResult(error_message="沒有影片檔可交給 Gemini")

        async with self._lock:
            try:
                target = self.temp_dir / self.INPUT_FILENAME
                shutil.copyfile(video_path, target)
                return await self._run(target.resolve())
            except Exception as e:
                logger.warning(f"Gemini 影片理解失敗，降級為只用本地結果: {e}")
                return GeminiVideoResult(error_message=str(e))

    async def _run(self, path: Path) -> GeminiVideoResult:
        proc = await asyncio.create_subprocess_exec(
            settings.agy_command,
            "--output-format", "json",
            "--print", self.PROMPT.format(path=path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return GeminiVideoResult(error_message=f"agy 逾時（{self.timeout}s）")

        result = self._parse(stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"))
        if not result.success:
            # 2026-08-25 端到端才發現：失敗只出現在使用者回覆裡，log 一片安靜。
            # 降級是常態，但「常態」不等於「不用留紀錄」——否則沒人看得出
            # 這條線到底多久沒生效過。
            logger.warning(f"Gemini 影片理解失敗，降級為只用本地結果: {result.error_message}")
        return result

    def _parse(self, stdout: str, stderr: str = "") -> GeminiVideoResult:
        """把 agy 的 JSON 包裝拆開。

        這裡是整個服務最要緊的一段：**agy 讀不到檔案時不會中止，會編一個
        看起來完全正常的答案回來**（2026-08-23 實測回了另一支影片的店名，
        而那些名字就寫在 workspace 的 md 檔裡）。唯一會說實話的是 status，
        所以 status 不是 SUCCESS 就一律當失敗，連看都不要看 response。
        """
        try:
            envelope = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return GeminiVideoResult(
                error_message=f"agy 輸出不是 JSON: {(stdout or stderr)[:200]}"
            )

        status = envelope.get("status")
        if status != "SUCCESS":
            return GeminiVideoResult(
                error_message=f"agy status={status}: {envelope.get('error') or '(無錯誤訊息)'}"
            )

        payload = self._first_json_object(envelope.get("response") or "")
        if payload is None:
            return GeminiVideoResult(error_message="agy 回應裡找不到 JSON 物件")

        places = [
            GeminiPlace(
                name=str(p["name"]).strip(),
                is_recommended=bool(p.get("is_recommended")),
                reason=p.get("reason"),
            )
            for p in payload.get("places", [])
            if isinstance(p, dict) and str(p.get("name") or "").strip()
        ]
        return GeminiVideoResult(success=True, places=places)

    @staticmethod
    def _first_json_object(text: str) -> Optional[dict]:
        """agy 常把 JSON 包在 ```json 圍欄裡、前面還接一段敘述。"""
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidates = [fenced.group(1)] if fenced else []
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            candidates.append(brace.group(0))
        for chunk in candidates:
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
