"""Google Places API 服務"""

import asyncio
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import quote

import aiohttp

from app.config import settings


logger = logging.getLogger(__name__)

# 名稱相似度門檻：高於 HIGH 視為同一家，低於 LOW 視為抓錯
SIMILARITY_HIGH = 0.8
SIMILARITY_MEDIUM = 0.6


class MatchConfidence(str, Enum):
    """匹配信心度——Places API 幾乎總會回「某個」店家，信心度是用來承認它可能不是我們要的那家"""

    HIGH = "high"      # 候選名稱與 LLM 抓到的店名高度吻合
    MEDIUM = "medium"  # 沒有期望店名可比對，或只是部分吻合
    LOW = "low"        # 所有候選都不像——需人工確認，不可靜默採用


def name_similarity(expected: Optional[str], actual: Optional[str]) -> float:
    """兩個店名的相似度 0.0–1.0。一方包含另一方（『巫婆水餃店』vs『巫婆水餃』）視為高相似。"""
    if not expected or not actual:
        return 0.0
    a, b = expected.strip(), actual.strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        # 包含關係：以較短者佔較長者的比例當分數，最低 0.8
        ratio = min(len(a), len(b)) / max(len(a), len(b))
        return max(0.8, ratio)
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class PlaceSearchResult:
    """地點搜尋結果"""

    found: bool = False
    place_id: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    price_level: Optional[int] = None
    google_maps_url: Optional[str] = None
    types: List[str] = None
    error_message: Optional[str] = None
    match_confidence: MatchConfidence = MatchConfidence.MEDIUM
    expected_name: Optional[str] = None  # LLM 抓到的店名，供呼叫端比對用

    def __post_init__(self):
        if self.types is None:
            self.types = []

    @property
    def needs_human_check(self) -> bool:
        """低信心結果要標示給使用者確認，不可當成已驗證的地點直接入庫"""
        return self.match_confidence == MatchConfidence.LOW


class GooglePlacesService:
    """
    Google Places API 服務
    
    使用 Google Places API (New) 搜尋店家資訊
    """
    
    # Places API (New) 端點
    TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
    PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
    
    def __init__(self):
        self.api_key = settings.google_places_api_key
    
    def _generate_maps_url(self, place_id: str = None, query: str = None, lat: float = None, lng: float = None) -> str:
        """
        生成 Google Maps 連結（支援瀏覽器和 App）
        
        使用 Maps URLs API 格式，在手機上會自動開啟 Google Maps App
        https://developers.google.com/maps/documentation/urls/get-started
        
        Args:
            place_id: Google Place ID
            query: 搜尋關鍵字
            lat: 緯度
            lng: 經度
            
        Returns:
            str: Google Maps URL（App 相容格式）
        """
        if place_id and lat and lng:
            # 最佳格式：同時使用 place_id 和座標，App 相容性最好
            return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}&query_place_id={place_id}"
        elif place_id:
            # 只有 place_id，使用 search API 格式（比 place/?q=place_id 更好）
            return f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}"
        elif query:
            # 使用搜尋關鍵字
            encoded_query = quote(query)
            return f"https://www.google.com/maps/search/?api=1&query={encoded_query}"
        elif lat and lng:
            # 使用座標
            return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        else:
            return ""
    
    def generate_search_url(self, keywords: List[str]) -> str:
        """
        生成 Google Maps 搜尋連結（不需要 API Key）
        
        Args:
            keywords: 搜尋關鍵字列表
            
        Returns:
            str: Google Maps 搜尋 URL
        """
        query = " ".join(keywords)
        encoded_query = quote(query)
        return f"https://www.google.com/maps/search/?api=1&query={encoded_query}"
    
    def _pick_best_candidate(
        self, candidates: List[Dict[str, Any]], expected_name: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], MatchConfidence]:
        """從候選中挑最像 expected_name 的一筆，並誠實回報信心度。

        沒有 expected_name 時不假裝高信心（MEDIUM）；全部都不像時回 LOW，
        仍回傳第一筆供參考，但呼叫端必須據 needs_human_check 處理。
        """
        if not candidates:
            return None, MatchConfidence.LOW

        if not expected_name:
            return candidates[0], MatchConfidence.MEDIUM

        scored = [
            (name_similarity(expected_name, c.get("displayName", {}).get("text")), c)
            for c in candidates
        ]
        best_score, best = max(scored, key=lambda pair: pair[0])

        if best_score >= SIMILARITY_HIGH:
            confidence = MatchConfidence.HIGH
        elif best_score >= SIMILARITY_MEDIUM:
            confidence = MatchConfidence.MEDIUM
        else:
            # 所有候選都不像——2026-07-25 的失敗案例走這條
            logger.warning(
                f"Places 候選都不像期望店名『{expected_name}』"
                f"（最佳：{best.get('displayName', {}).get('text')}，相似度 {best_score:.2f}）"
                f"，標記為低信心"
            )
            return candidates[0], MatchConfidence.LOW

        return best, confidence

    async def search_place(
        self,
        query: str,
        region_code: str = "TW",
        expected_name: Optional[str] = None,
    ) -> PlaceSearchResult:
        """
        搜尋地點

        Args:
            query: 搜尋關鍵字（店名 + 城市）
            region_code: 地區代碼（預設台灣）
            expected_name: LLM 抓到的店名，用來驗證 Places 回傳的是不是同一家

        Returns:
            PlaceSearchResult: 搜尋結果（含 match_confidence）
        """
        if not self.api_key:
            # 無 API Key，回傳搜尋連結
            logger.info("未設定 Google Places API Key，回傳搜尋連結")
            return PlaceSearchResult(
                found=True,
                name=query,
                google_maps_url=self.generate_search_url([query])
            )
        
        logger.info(f"搜尋地點: {query}")
        
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.priceLevel,places.types"
        }
        
        # 取多筆候選才有得挑；只取 1 筆等於強迫自己接受 API 的第一個猜測（F18）
        body = {
            "textQuery": query,
            "regionCode": region_code,
            "languageCode": "zh-TW",
            "maxResultCount": 5
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.TEXT_SEARCH_URL,
                    headers=headers,
                    json=body
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Places API 錯誤: {response.status} - {error_text}")
                        return PlaceSearchResult(
                            found=False,
                            error_message=f"API 錯誤: {response.status}",
                            google_maps_url=self.generate_search_url([query])
                        )
                    
                    data = await response.json()
                    
                    places = data.get("places", [])
                    if not places:
                        logger.info(f"找不到地點: {query}")
                        return PlaceSearchResult(
                            found=False,
                            error_message="找不到符合的地點",
                            google_maps_url=self.generate_search_url([query])
                        )
                    
                    place, confidence = self._pick_best_candidate(places, expected_name)
                    place_id = place.get("id", "").replace("places/", "")
                    location = place.get("location", {})
                    lat = location.get("latitude")
                    lng = location.get("longitude")
                    
                    result = PlaceSearchResult(
                        found=True,
                        place_id=place_id,
                        name=place.get("displayName", {}).get("text"),
                        address=place.get("formattedAddress"),
                        latitude=lat,
                        longitude=lng,
                        rating=place.get("rating"),
                        user_ratings_total=place.get("userRatingCount"),
                        price_level=place.get("priceLevel"),
                        types=place.get("types", []),
                        google_maps_url=self._generate_maps_url(place_id=place_id, lat=lat, lng=lng),
                        match_confidence=confidence,
                        expected_name=expected_name,
                    )

                    logger.info(
                        f"找到地點: {result.name} ({result.address}) "
                        f"[信心度 {confidence.value}，候選 {len(places)} 筆]"
                    )
                    return result
                    
        except Exception as e:
            logger.error(f"搜尋地點失敗: {e}")
            return PlaceSearchResult(
                found=False,
                error_message=str(e),
                google_maps_url=self.generate_search_url([query])
            )
    
    async def search_with_keywords(
        self,
        keywords: List[str],
        region_code: str = "TW",
        expected_name: Optional[str] = None,
    ) -> PlaceSearchResult:
        """
        使用多個關鍵字搜尋地點

        Args:
            keywords: 關鍵字列表
            region_code: 地區代碼
            expected_name: LLM 抓到的店名；未給時預設用第一個關鍵字驗證

        Returns:
            PlaceSearchResult: 搜尋結果（含 match_confidence）
        """
        query = " ".join(keywords)
        if expected_name is None and keywords:
            expected_name = keywords[0]
        return await self.search_place(query, region_code, expected_name=expected_name)
