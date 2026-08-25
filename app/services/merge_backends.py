"""合併階段的模型後端（F27）。

PlaceExtractor 組好提示詞之後，交給這裡依 settings.merge_backend 選出的後端
跑推論，換回結構化地點清單。不同後端吃同一份提示詞才比得出差異，所以提示詞
組裝留在 PlaceExtractor（EXTRACTION_PROMPT / format_gemini_candidates）；
後端只管「怎麼問模型、怎麼把它的答案解析成 PlaceInfo」。

這個 slice（F27.1）唯一實作是 ollama——現有呼叫原樣搬過來，行為不變。
agy / claude / codex 後端與備援鏈、投票這些是後面的 slice，這裡不做。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

import ollama

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class PlaceInfo:
    """擷取的單一地點資訊（餐廳、景點等）"""

    confidence: str = "low"  # high, medium, low

    # 店家資訊
    name: Optional[str] = None
    name_en: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None

    # 分類資訊
    place_type: List[str] = field(default_factory=list)  # 餐廳、咖啡廳、景點等
    highlights: List[str] = field(default_factory=list)  # 亮點：推薦餐點或特色
    price_range: Optional[str] = None

    # 其他
    recommendation: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    search_keywords: List[str] = field(default_factory=list)


class MergeFailure(Exception):
    """後端沒能產出地點清單，`notes` 是要讓使用者看到的原因。

    存在的理由是搬家時差點弄丟的東西：舊的 `_parse_response` 在找不到 JSON、
    JSON 修不好、或模型自己回 found=false 時，會把原因寫進
    `ExtractionResult.notes` 並顯示給使用者（handlers 的「備註」那行）。
    後端契約回傳 `List[PlaceInfo]`，沒有欄位承載這段文字，一開始就直接回空
    清單——使用者只會看到一句沒有資訊量的通用文案，正是這個專案已經修過
    兩次的病（F24 的 cookies、F25 的短連結）。

    用例外而不是擴充回傳型別，是因為 acceptance 要求輸出就是 `List[PlaceInfo]`。
    `PlaceExtractor` 專門接住它、補回 notes，然後**照舊繼續跑 `_reconcile`**——
    本地這步爛掉時 agy 候選還救得回來，那是舊版就有的行為，不能因為搬家弄丟。
    """

    def __init__(self, notes: Optional[str]):
        super().__init__(notes or "後端沒有回傳地點")
        self.notes = notes


class MergeBackend(Protocol):
    """輸入組好的提示詞，輸出解析後的地點清單。

    解析失敗（找不到 JSON、JSON 壞掉、模型自己說 found=false）丟 `MergeFailure`
    並帶上原因，由 PlaceExtractor 轉成 notes；它接住之後仍會跑 _reconcile，
    用 agy 候選補救，不會因為本地模型這一步輸出爛掉就整段中斷。
    真正的「呼叫本身失敗」（連線問題、SDK 參數不合等）照舊讓例外往上拋，
    PlaceExtractor.extract() 的 try/except 會接住並轉成
    ExtractionResult(found=False, notes=...)。
    """

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        ...


class UnsupportedMergeBackendError(ValueError):
    """settings.merge_backend 給了目前不支援的值。"""


def get_backend(name: str) -> MergeBackend:
    """依名稱建立後端。這個 slice 唯一合法值是 "ollama"；

    給其他值在這裡就明確報錯，不靜默 fallback 回 ollama。
    """
    if name == "ollama":
        return OllamaMergeBackend()
    raise UnsupportedMergeBackendError(
        f"不支援的合併後端 merge_backend={name!r}；目前唯一支援 'ollama'"
    )


class OllamaMergeBackend:
    """現有的 ollama 呼叫，原樣搬過來（F27.1：純粹搬家，行為不變）。"""

    def __init__(self):
        self.model = settings.ollama_model

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        # 只傳 requirements.txt 釘住的 ollama 版本支援的參數。
        # 曾經傳過 think=True，但那是 0.5+ 才有的參數，對釘住的 0.3.3 會
        # TypeError，整個擷取階段靜默退化成 found=False（2026-08-23 冒煙測試抓到）。
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: ollama.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3}
            )
        )

        # 新版 ollama 套件回傳物件而非字典
        msg = response["message"]
        result_text = msg.content if hasattr(msg, 'content') else msg.get("content", "")

        # 記錄思考過程（如果有）
        if hasattr(msg, 'thinking') and msg.thinking:
            logger.info(f"[思考過程] {msg.thinking[:200]}...")

        logger.debug(f"LLM 回應: {result_text}")

        return self._parse(result_text)

    def _parse(self, response_text: str) -> List[PlaceInfo]:
        """解析 LLM 回應（搬自 PlaceExtractor._parse_response，行為不變）。"""
        try:
            # 預處理：移除可能的 markdown 程式碼區塊標記
            cleaned_text = response_text
            if "```json" in cleaned_text:
                cleaned_text = re.sub(r'```json\s*', '', cleaned_text)
                cleaned_text = re.sub(r'```\s*$', '', cleaned_text)
            elif "```" in cleaned_text:
                cleaned_text = re.sub(r'```\s*', '', cleaned_text)

            # 嘗試找出 JSON 區塊（匹配最外層的大括號）
            json_match = re.search(r'\{[\s\S]*\}', cleaned_text)
            if not json_match:
                logger.warning("回應中找不到 JSON")
                raise MergeFailure("無法解析回應")

            json_str = json_match.group()

            # 嘗試修復常見的 JSON 格式問題
            # 1. 移除尾隨逗號
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            # 2. 修復可能的單引號問題
            # 3. 移除註解（LLM 有時會加註解）
            json_str = re.sub(r'//.*?(?=\n|$)', '', json_str)

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as first_error:
                # 二次嘗試：更激進的清理
                logger.warning(f"第一次 JSON 解析失敗，嘗試修復: {first_error}")

                # 嘗試只提取有效的 JSON 結構
                # 找到 "found" 開始的部分
                found_match = re.search(r'\{\s*"found"[\s\S]*', json_str)
                if found_match:
                    json_str = found_match.group()
                    # 確保閉合
                    open_braces = json_str.count('{')
                    close_braces = json_str.count('}')
                    if open_braces > close_braces:
                        json_str += '}' * (open_braces - close_braces)

                    try:
                        data = json.loads(json_str)
                    except json.JSONDecodeError as second_error:
                        logger.error(f"JSON 解析最終失敗: {second_error}")
                        logger.debug(f"問題 JSON: {json_str[:500]}...")
                        raise MergeFailure(f"JSON 解析失敗: {second_error}")
                else:
                    logger.error(f"JSON 解析失敗，無法修復: {first_error}")
                    raise MergeFailure(f"JSON 解析失敗: {first_error}")

            if not data.get("found", False):
                # 模型自己說沒找到——它附的理由是有資訊的，要送到使用者眼前
                raise MergeFailure(data.get("notes"))

            places_data = data.get("places", [])

            # 向後相容：如果是舊格式（單一 place 物件）
            if not places_data and "place" in data:
                places_data = [data["place"]]

            places = []
            for place_data in places_data:
                place = PlaceInfo(
                    confidence=place_data.get("confidence", "low"),
                    name=place_data.get("name"),
                    name_en=place_data.get("name_en"),
                    city=place_data.get("city"),
                    country=place_data.get("country"),
                    address=place_data.get("address"),
                    place_type=place_data.get("place_type", []),
                    highlights=place_data.get("highlights", []),
                    price_range=place_data.get("price_range"),
                    recommendation=place_data.get("recommendation"),
                    tags=place_data.get("tags", []),
                    search_keywords=place_data.get("search_keywords", [])
                )
                places.append(place)

            logger.info(f"成功擷取 {len(places)} 個地點")

            return places

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失敗: {e}")
            raise MergeFailure(f"JSON 解析失敗: {e}")
