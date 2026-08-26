"""地點擷取服務"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List

from app.config import runtime_settings, settings
from app.services.merge_backends import (
    MergeFailure,
    PlaceInfo,
    UnsupportedMergeModeError,
    get_backend_chain,
    merge_with_backends,
    norm_place_name,
)

# PlaceInfo 定義搬到 merge_backends.py（F27.1：後端要用它組裝回傳值），
# 這裡 re-export 是為了讓既有的 `from app.services.place_extractor import
# PlaceInfo` 呼叫端（handlers.py、測試）不用改 import 路徑。


logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """擷取結果（可能包含多個地點）"""
    
    found: bool = False
    places: List[PlaceInfo] = field(default_factory=list)
    notes: Optional[str] = None
    backend_note: Optional[str] = None  # F27.2：這次合併實際用了哪個/哪些後端

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
      "district": "行政區（說明文或 hashtag 明確提到才填，如：北投；否則 null）",
      "place_type": ["地點類型（繁體中文），如：餐廳、咖啡廳、景點、博物館、公園"],
      "highlights": ["亮點（繁體中文）：推薦餐點、必看特色等"],
      "price_range": "$或$$或$$$或$$$$（如適用）",
      "recommendation": "推薦原因（繁體中文，簡短描述）",
      "tags": ["標籤（繁體中文），如：約會、打卡、親子、拍照"],
      "confidence": "high或medium或low",
      "is_physical": "true或false（可實際到訪的實體店家為 true；判斷不了就填 true）",
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
   兩個名字指同一家店卻各列一筆，會在 Google Maps 清單裡存成兩個地點。
{extra_rules}"""

    # 規則 11-13 只在「有 Gemini 候選」時附加。無候選的降級路徑餵給 qwen3:8b 時，
    # prompt 一長它就整組拋棄指定 schema（自創頂層鍵、丟掉 found、簡體），F28 加這
    # 三條之後降級重現 0/3。純 prompt 工程五版都救不回來（v2-v6，見 DEVLOG 2026-08-26），
    # 所以改用「降級模式回到 F28 前的短 prompt」。安全性：降級路徑本來就不要求精度
    # （_reconcile docstring），is_physical 缺省 True 是 fail-open，非實體店家由後面
    # 的 A 段（LOW confidence 不寫 Maps）與比對步驟兜底。
    CANDIDATE_RULES = """11. district（行政區）只填說明文或 hashtag 裡明確出現的行政區名稱（如：北投、信義、
   大安），不得從地址、店名或畫面去推測、猜測。沒有明確提到就填 null。
12. is_physical（是否為可實際到訪的實體店家）——這是**標記，不是要不要列入的條件**：
   判成 false 的店家**仍然必須列在 places 裡**、found 也照常是 true，只是
   is_physical 欄位標 false。就算整篇介紹的店家全都是非實體，places 也要列出
   它們，**不可因此回 found=false 或空陣列**。判準：
   - 業配的品牌／商品：冷凍食品、真空包裝、宅配到府、電商販售、沒有實體門市可去，
     或消費場景在自己家（開箱、下鍋烹煮、冰箱囤貨、在家享用）→ false
   - 有入座用餐、至攤位／店面點餐、實地走訪的畫面或描述 → true
   - 判斷不了 → true。這條是刻意的：判不出來就放行，是不是真的能去、要不要存進
     地圖，交給後面的比對步驟判斷。
13. 回覆**必須完全依照上面給定的 JSON 結構與鍵名**：最外層只有 found、places、notes
   三個鍵，places 裡每個地點用上面列出的欄位名，一字不差。不得自創其他結構
   （例如 location、additional_info）、不得改鍵名、不得省略 found。"""

    def __init__(self):
        # 依 settings.merge_backends 建後端鏈；鏈為空或含不支援的名稱、以及
        # MERGE_MODE 不合法，都在這裡（建構時）就報錯，不等到真的呼叫才發現
        # （F27.2 acceptance #2、#3：不得靜默 fallback）。
        #
        # self（這個物件本身）是長生命週期的——handlers.py 只在啟動時建構一次、
        # 活到 bot 結束，所以鏈的內容只讀 env 這件事沒問題（要換鏈本來就該
        # 重啟），但合併「模式」不能比照辦理：/mergemode 要能在運行中切換，
        # 因此模式不在這裡快取，改成每次 extract() 都讀
        # runtime_settings.merge_mode（見下）。這裡驗證的是 MERGE_MODE 的
        # env 預設值本身合不合法，不是 runtime_settings 當下的覆寫值。
        self._backends = get_backend_chain(settings.merge_backends)
        if settings.merge_mode not in runtime_settings.MERGE_MODE_OPTIONS:
            raise UnsupportedMergeModeError(
                f"不支援的合併模式 MERGE_MODE={settings.merge_mode!r}；"
                f"僅支援 {runtime_settings.MERGE_MODE_OPTIONS}"
            )

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
            extra_rules=self.CANDIDATE_RULES if gemini_places else "",
        )
        
        notes = None
        backend_note = None
        try:
            # 依 runtime_settings.merge_mode 跑後端鏈，再用確定性規則收斂
            # （模型的判斷不是最終權威）。每次都重讀 merge_mode、不快取，
            # /mergemode 才能在運行中切換而不必重啟（見 __init__ 的說明）。
            places, backend_note = await merge_with_backends(
                self._backends, prompt, runtime_settings.merge_mode
            )
        except MergeFailure as e:
            # 整條鏈都沒產出清單，但每個後端各自的理由要送到使用者眼前
            # （handlers 的「備註」那行）。這裡不能提前 return——舊版在解析
            # 失敗時照樣跑 _reconcile，agy 候選還救得回來，found 甚至可能翻回
            # True。搬家時弄丟過這段，2026-08-25 驗收抓到。
            places, notes = [], e.notes
        except Exception as e:
            logger.error(f"擷取地點失敗: {e}")
            return ExtractionResult(found=False, notes=str(e))

        places = self._reconcile(places, gemini_places)
        return ExtractionResult(
            found=bool(places), places=places, notes=notes, backend_note=backend_note
        )
    
    # 區域名不是店家（驗收標準明文）。8b 會把逐字稿與 hashtag 裡的市場名當成
    # 一家店塞進來，實測三次全中（「北投市場」「北投中繼市場」）。這條寫在提示詞
    # 裡沒有用——同樣三次全中，它照塞。所以改成程式擋。
    # ponytail: 純後綴比對，遇到真的叫「XX市場」的店家會誤殺；等真的踩到再加白名單
    AREA_SUFFIXES = ("市場", "夜市", "商圈", "老街", "周邊")

    @staticmethod
    def _norm_name(name: Optional[str]) -> str:
        """比對用的正規化：轉呼叫 merge_backends.norm_place_name。

        F27.2 把這個邏輯搬去 merge_backends.py 給 vote 模式的記票鍵共用
        （不能兩個模組各留一份，會漂移）；這裡保留 staticmethod 是為了讓
        _reconcile 的內容一行不動。
        """
        return norm_place_name(name)

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
