"""地點擷取服務"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List

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


@dataclass
class ExtractionResult:
    """擷取結果（可能包含多個地點）"""
    
    found: bool = False
    places: List[PlaceInfo] = field(default_factory=list)
    notes: Optional[str] = None
    
    @property
    def place_count(self) -> int:
        """回傳找到的地點數量"""
        return len(self.places)
    
    @property
    def first_place(self) -> Optional[PlaceInfo]:
        """回傳第一個地點（向後相容）"""
        return self.places[0] if self.places else None


class PlaceExtractor:
    """
    地點擷取器
    
    使用 LLM 從影片內容中擷取餐廳/景點/店家資訊
    支援一次擷取多個地點
    """
    
    EXTRACTION_PROMPT = """你是一個專業的地點資訊擷取助手。請從以下影片/貼文內容中擷取所有餐廳/景點/店家資訊。

⚠️ 重要：所有回覆內容必須使用「繁體中文」，不可使用簡體中文。

【貼文說明文】
{caption}

【語音內容】
{transcript}

【畫面描述】
{visual_description}

【IG 帳號】
{ig_account}

注意：
1. 一篇貼文/影片可能包含多個地點（例如美食推薦合集、多店家介紹等），請擷取所有提到的地點。
2. 貼文說明文通常包含店家名稱、地址、營業時間等重要資訊，請優先參考。
3. 說明文中的 hashtag（#）可能包含地點名稱或城市名。

請以 JSON 格式回覆（確保是有效的 JSON，所有中文必須是繁體中文）：

{{
  "found": true或false,
  "places": [
    {{
      "name": "地點名稱（繁體中文）",
      "name_en": "英文名稱（如有）",
      "city": "城市（繁體中文，如：台北、東京、首爾）",
      "country": "國家（繁體中文，如：台灣、日本、韓國）",
      "address": "地址（繁體中文，如有提到）",
      "place_type": ["地點類型（繁體中文），如：餐廳、咖啡廳、景點、博物館、公園"],
      "highlights": ["亮點（繁體中文）：推薦餐點、必看特色等"],
      "price_range": "$或$$或$$$或$$$$（如適用）",
      "recommendation": "推薦原因（繁體中文，簡短描述）",
      "tags": ["標籤（繁體中文），如：約會、打卡、親子、拍照"],
      "confidence": "high或medium或low",
      "search_keywords": ["用於 Google Maps 搜尋的關鍵字，包含地點名稱和城市"]
    }}
  ],
  "notes": "其他備註（繁體中文）"
}}

重要規則：
1. 所有中文內容必須使用繁體中文（Traditional Chinese），禁止使用簡體中文
2. 如果無法確定是餐廳/景點/店家相關內容，請設 found 為 false，places 為空陣列
3. 儘量從畫面中的招牌、標示擷取正確名稱
4. 根據口音、貨幣、語言、環境推測可能的城市/國家
5. 如果是台灣的地點，city 請填城市名（如：台北、台中）
6. search_keywords 應該是可以直接在 Google Maps 搜尋到地點的關鍵字組合
7. 每個地點獨立評估 confidence：
   - high: 明確看到/聽到名稱，且有地點線索
   - medium: 有名稱但地點不確定，或有地點但名稱模糊
   - low: 只能推測，資訊不完整
8. 如果內容介紹多個地點，全部列出（例如「台北5家必吃拉麵」應列出5個地點）"""

    def __init__(self):
        self.model = settings.ollama_model
    
    async def extract(
        self,
        transcript: str,
        visual_description: str,
        ig_account: Optional[str] = None,
        caption: Optional[str] = None
    ) -> ExtractionResult:
        """
        從影片內容擷取地點資訊
        
        Args:
            transcript: 語音轉文字結果
            visual_description: 視覺分析結果
            ig_account: IG 帳號名稱（可能包含地點線索）
            caption: 貼文說明文（通常包含店家名稱、地址等重要資訊）
            
        Returns:
            ExtractionResult: 擷取結果（可能包含多個地點）
        """
        logger.info("開始擷取地點資訊...")
        
        prompt = self.EXTRACTION_PROMPT.format(
            caption=caption or "（無貼文說明）",
            transcript=transcript or "（無語音內容）",
            visual_description=visual_description or "（無畫面描述）",
            ig_account=ig_account or "（未知）"
        )
        
        try:
            # 使用 Ollama 呼叫 LLM（啟用 thinking 模式）
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ollama.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    think=True,  # 啟用 thinking 模式
                    options={"temperature": 0.3}
                )
            )
            
            # 新版 ollama 套件回傳物件而非字典
            msg = response["message"]
            result_text = msg.content if hasattr(msg, 'content') else msg.get("content", "")
            
            # 記錄思考過程（如果有）
            if hasattr(msg, 'thinking') and msg.thinking:
                logger.info(f"🧠 LLM 思考過程: {msg.thinking[:200]}...")
            
            logger.debug(f"LLM 回應: {result_text}")
            
            # 解析 JSON
            return self._parse_response(result_text)
            
        except Exception as e:
            logger.error(f"擷取地點失敗: {e}")
            return ExtractionResult(found=False, notes=str(e))
    
    def _parse_response(self, response_text: str) -> ExtractionResult:
        """解析 LLM 回應"""
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
                return ExtractionResult(found=False, notes="無法解析回應")
            
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
                        return ExtractionResult(found=False, notes=f"JSON 解析失敗: {second_error}")
                else:
                    logger.error(f"JSON 解析失敗，無法修復: {first_error}")
                    return ExtractionResult(found=False, notes=f"JSON 解析失敗: {first_error}")
            
            if not data.get("found", False):
                return ExtractionResult(found=False, notes=data.get("notes"))
            
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
            
            return ExtractionResult(
                found=len(places) > 0,
                places=places,
                notes=data.get("notes")
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失敗: {e}")
            return ExtractionResult(found=False, notes=f"JSON 解析失敗: {e}")
