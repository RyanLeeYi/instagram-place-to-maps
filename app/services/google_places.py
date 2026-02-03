"""Google Places API 服務"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List
from urllib.parse import quote

import aiohttp

from app.config import settings


logger = logging.getLogger(__name__)


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
    
    def __post_init__(self):
        if self.types is None:
            self.types = []


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
    
    async def search_place(self, query: str, region_code: str = "TW") -> PlaceSearchResult:
        """
        搜尋地點
        
        Args:
            query: 搜尋關鍵字（店名 + 城市）
            region_code: 地區代碼（預設台灣）
            
        Returns:
            PlaceSearchResult: 搜尋結果
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
        
        body = {
            "textQuery": query,
            "regionCode": region_code,
            "languageCode": "zh-TW",
            "maxResultCount": 1
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
                    
                    place = places[0]
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
                        google_maps_url=self._generate_maps_url(place_id=place_id, lat=lat, lng=lng)
                    )
                    
                    logger.info(f"找到地點: {result.name} ({result.address})")
                    return result
                    
        except Exception as e:
            logger.error(f"搜尋地點失敗: {e}")
            return PlaceSearchResult(
                found=False,
                error_message=str(e),
                google_maps_url=self.generate_search_url([query])
            )
    
    async def search_with_keywords(self, keywords: List[str], region_code: str = "TW") -> PlaceSearchResult:
        """
        使用多個關鍵字搜尋地點
        
        Args:
            keywords: 關鍵字列表
            region_code: 地區代碼
            
        Returns:
            PlaceSearchResult: 搜尋結果
        """
        # 合併關鍵字搜尋
        query = " ".join(keywords)
        return await self.search_place(query, region_code)
