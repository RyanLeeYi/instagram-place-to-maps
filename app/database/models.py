"""資料庫模型"""

from datetime import datetime
from typing import Optional, List
import json

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


Base = declarative_base()


class Place(Base):
    """地點資料表（餐廳、景點等）"""
    
    __tablename__ = "places"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 店家基本資訊
    name = Column(String(255), nullable=False)
    name_en = Column(String(255), nullable=True)
    
    # 位置資訊
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    google_place_id = Column(String(255), nullable=True)
    google_maps_url = Column(Text, nullable=True)
    
    # 分類資訊
    place_type = Column(Text, nullable=True)   # JSON array: 餐廳、景點等
    highlights = Column(Text, nullable=True)   # JSON array: 亮點、推薦項目
    price_range = Column(String(10), nullable=True)
    
    # 來源資訊
    source_url = Column(Text, nullable=False)
    source_account = Column(String(100), nullable=True)
    telegram_chat_id = Column(String(50), nullable=True)
    
    # 推薦資訊
    recommendation = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)  # JSON array
    
    # 狀態
    status = Column(String(20), default="pending")  # pending, confirmed, rejected
    confidence = Column(String(20), nullable=True)  # high, medium, low
    
    # 時間戳記
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_place_types(self) -> List[str]:
        """取得地點類型列表"""
        if self.place_type:
            try:
                return json.loads(self.place_type)
            except:
                return []
        return []
    
    def set_place_types(self, types: List[str]):
        """設定地點類型"""
        self.place_type = json.dumps(types, ensure_ascii=False)
    
    def get_highlights(self) -> List[str]:
        """取得亮點列表"""
        if self.highlights:
            try:
                return json.loads(self.highlights)
            except:
                return []
        return []
    
    def set_highlights(self, items: List[str]):
        """設定亮點"""
        self.highlights = json.dumps(items, ensure_ascii=False)
    
    def get_tags(self) -> List[str]:
        """取得標籤列表"""
        if self.tags:
            try:
                return json.loads(self.tags)
            except:
                return []
        return []
    
    def set_tags(self, tag_list: List[str]):
        """設定標籤"""
        self.tags = json.dumps(tag_list, ensure_ascii=False)


# 建立非同步引擎
engine = create_async_engine(settings.database_url, echo=False)

# 建立非同步 Session
async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db():
    """初始化資料庫"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """取得資料庫 session"""
    async with async_session() as session:
        yield session
