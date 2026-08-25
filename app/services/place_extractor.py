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
    def error_message(self) -> Optional[str]:
        """失敗原因；found=True 時為 None。

        與 downloader / google_places / transcriber 等 service 同名——
        呼叫端問「為什麼失敗」只有一個欄位要記，不會因為這裡叫 notes 而讀到 None。
        """
        return None if self.found else (self.notes or None)

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

【來源帳號】
{ig_account}

【Gemini 影片理解讀到的店家候選】
{gemini_candidates}

注意：
1. 一篇貼文/影片可能包含多個地點（例如美食推薦合集、多店家介紹等），請擷取所有提到的地點。
2. 貼文說明文通常包含店家名稱、地址、營業時間等重要資訊，請優先參考。
3. 說明文中的 hashtag（#）可能包含地點名稱或城市名。
4. 此內容可能來自 Instagram 或 Threads 貼文。

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
8. 如果內容介紹多個地點，全部列出（例如「台北5家必吃拉麵」應列出5個地點）
9. 【Gemini 候選】是另一個模型直接看影片讀到的招牌文字。它補得到語音沒講、
   畫面描述也漏掉的店名，但它也會把路過的隔壁攤、菜單橫幅、街景招牌一起讀進來。
   請把它與語音/畫面的線索合併去重，然後**只保留「作者在推薦的店家」**：
   - 標記為「影片主要拍攝對象」的候選 → **一律保留**。這是判斷推薦與否的最強證據，
     它比逐字稿可靠：逐字稿是語音辨識的結果，店名經常被聽錯（例如把「鹽埕」
     聽成「位遠程序」），所以「逐字稿裡沒有這個店名」**不構成**排除它的理由。
   - 語音或說明文在講它 → 保留
   - 標記為「只是畫面中出現」，而且語音與說明文都沒提到它 → 不要列入 places
     （路過的隔壁攤、菜單橫幅、街景招牌、路牌屬於這一類）
   注意：如果合併之後 places 變成空的，那幾乎一定是判太嚴了。
   語音講了某間店、或 Gemini 標了主要拍攝對象，就至少要留下那一間。
10. 去重要看「是不是同一家店」，不是「字串一不一樣」。同一家店的中文名、英文名、
   招牌全名（例如「美軍炸雞」與「Padam Padam 1970」與「Padam Padam 1970 美軍炸雞」）
   **只能出現一筆**：挑最完整的當 name，其餘寫進 name_en 或 search_keywords。
   兩個名字指同一家店卻各列一筆，會在 Google Maps 清單裡存成兩個地點。"""

    def __init__(self):
        self.model = settings.ollama_model
    
    @staticmethod
    def format_gemini_candidates(gemini_places: Optional[List] = None) -> str:
        """把 Gemini 讀到的候選攤平成 prompt 用的一段文字。

        沒有候選時明說「無」——空字串會讓 LLM 自己腦補這一區在講什麼。
        """
        if not gemini_places:
            return "（無，本次只有本地來源）"
        return "\n".join(
            "- {name}（{flag}）{reason}".format(
                name=p.name,
                flag="影片主要拍攝對象" if p.is_recommended else "只是畫面中出現",
                reason=f"：{p.reason}" if p.reason else "",
            )
            for p in gemini_places
        )

    async def extract(
        self,
        transcript: str,
        visual_description: str,
        ig_account: Optional[str] = None,
        caption: Optional[str] = None,
        gemini_places: Optional[List] = None,
    ) -> ExtractionResult:
        """
        從影片內容擷取地點資訊
        
        Args:
            transcript: 語音轉文字結果
            visual_description: 視覺分析結果
            ig_account: IG 帳號名稱（可能包含地點線索）
            caption: 貼文說明文（通常包含店家名稱、地址等重要資訊）
            gemini_places: Gemini 影片理解讀到的候選（GeminiPlace），沒有就只用本地來源
            
        Returns:
            ExtractionResult: 擷取結果（可能包含多個地點）
        """
        logger.info("開始擷取地點資訊...")
        
        prompt = self.EXTRACTION_PROMPT.format(
            caption=caption or "（無貼文說明）",
            transcript=transcript or "（無語音內容）",
            visual_description=visual_description or "（無畫面描述）",
            ig_account=ig_account or "（未知）",
            gemini_candidates=self.format_gemini_candidates(gemini_places),
        )
        
        try:
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
                logger.info(f"🧠 LLM 思考過程: {msg.thinking[:200]}...")
            
            logger.debug(f"LLM 回應: {result_text}")
            
            # 解析 JSON，再用確定性規則收斂（8b 的判斷不是最終權威）
            parsed = self._parse_response(result_text)
            parsed.places = self._reconcile(parsed.places, gemini_places)
            parsed.found = bool(parsed.places)
            return parsed
            
        except Exception as e:
            logger.error(f"擷取地點失敗: {e}")
            return ExtractionResult(found=False, notes=str(e))
    
    # 區域名不是店家（驗收標準明文）。8b 會把逐字稿與 hashtag 裡的市場名當成
    # 一家店塞進來，實測三次全中（「北投市場」「北投中繼市場」）。這條寫在提示詞
    # 裡沒有用——同樣三次全中，它照塞。所以改成程式擋。
    # ponytail: 純後綴比對，遇到真的叫「XX市場」的店家會誤殺；等真的踩到再加白名單
    AREA_SUFFIXES = ("市場", "夜市", "商圈", "老街", "周邊")

    @staticmethod
    def _norm_name(name: Optional[str]) -> str:
        """比對用的正規化：抹掉空白與「店」字尾。

        「巫婆水餃」與「巫婆水餃店」是同一家，不該因為多一個字被當成兩筆。
        """
        return (name or "").replace(" ", "").strip().rstrip("店")

    def _reconcile(self, places: List[PlaceInfo], gemini_places: Optional[List] = None) -> List[PlaceInfo]:
        """用 agy 的判斷收斂 8b 的輸出。

        2026-08-25 消融實驗：對照 Ryan 逐幀確認的答案，agy 的 is_recommended
        拿到 11/11，8b 合併之後掉到 8-9/11——它漏掉 whisper 聽錯名字的店
        （海鮮拉麵清燉豬腳），又把 agy 已經標成「只是畫面帶到」的地標放進來。
        所以判斷權歸 agy，8b 只剩兩件事：補 agy 看不到的來源（說明文裡的業配
        店家），以及把同一家店的多個寫法收成一筆。

        agy 失敗時 gemini_places 是空的，這裡只做區域名過濾，其餘原樣通過——
        降級路徑本來就不要求精度。
        """
        kept = [p for p in places if not self._is_area_name(p.name)]

        if not gemini_places:
            return kept

        rejected = {self._norm_name(g.name) for g in gemini_places if not g.is_recommended}
        kept = [p for p in kept if self._norm_name(p.name) not in rejected]

        # agy 認定的推薦店家一律補齊：8b 漏掉它們的原因通常是逐字稿把店名
        # 聽爛了，而那正是 agy 存在的理由，不能讓 8b 的漏聽蓋掉它。
        seen = {self._norm_name(p.name) for p in kept}
        for g in gemini_places:
            if not g.is_recommended or self._is_area_name(g.name):
                continue
            if self._norm_name(g.name) in seen:
                continue
            kept.append(PlaceInfo(
                name=g.name,
                confidence="medium",
                recommendation=g.reason,
                search_keywords=[g.name],
            ))
            seen.add(self._norm_name(g.name))
        return kept

    def _is_area_name(self, name: Optional[str]) -> bool:
        """行政區、市場、商圈這類區域名，不是可以存進地圖清單的店家。"""
        return self._norm_name(name).endswith(self.AREA_SUFFIXES)

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
