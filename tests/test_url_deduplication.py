r"""F13 回歸測試：同一則連結傳第二次要回既有紀錄，不重跑 pipeline。

比對基準是貼文 ID 而不是整條 URL——同一支 Reel 從 IG app 分享出來，
每次都帶不同的 ?igsh= 追蹤參數，用字串比對會把它當成沒看過的新連結。
"""

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot import handlers as handlers_module
from app.bot.handlers import PlaceBotHandlers
from app.database.models import Base, Place
from app.services.downloader import InstagramDownloader

REEL = "https://www.instagram.com/reel/DT2w2PVgXo3/"
SAME_REEL_SHARED = "https://www.instagram.com/reel/DT2w2PVgXo3/?igsh=MXY3bGx4ZQ%3D%3D"
OTHER_REEL = "https://www.instagram.com/reel/DbBDplQS71J/"


@pytest.fixture
def handler(monkeypatch):
    """只裝上這個測試需要的兩樣東西，不去啟動 whisper / ollama / Places。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                Place(name="巫婆水餃", city="台北", source_url=REEL,
                      google_maps_url="https://maps.google.com/?cid=1")
            )
            await session.commit()

    asyncio.run(seed())
    monkeypatch.setattr(handlers_module, "async_session", session_factory)

    h = PlaceBotHandlers.__new__(PlaceBotHandlers)
    h.downloader = InstagramDownloader()
    return h


def test_同一則連結第二次傳來時找得到既有紀錄(handler):
    places = asyncio.run(handler._find_places_by_url(REEL))

    assert [p.name for p in places] == ["巫婆水餃"]


def test_帶不同追蹤參數的同一支_Reel_仍視為同一則(handler):
    """核心回歸：IG 分享出來的連結每次 ?igsh= 都不一樣，不能當成新連結。"""
    places = asyncio.run(handler._find_places_by_url(SAME_REEL_SHARED))

    assert [p.name for p in places] == ["巫婆水餃"]


def test_沒處理過的連結回空清單(handler):
    assert asyncio.run(handler._find_places_by_url(OTHER_REEL)) == []


def test_解析不出貼文_ID_的連結不查也不炸(handler):
    assert asyncio.run(handler._find_places_by_url("https://example.com/nope")) == []


def test_既有紀錄的回覆講得出處理過與店名():
    place = Place(name="巫婆水餃", city="台北",
                  google_maps_url="https://maps.google.com/?cid=1")

    message = PlaceBotHandlers._format_existing_places([place])

    assert "已經處理過" in message
    assert "巫婆水餃" in message
    assert "maps.google.com" in message


def test_回覆的_MarkdownV2_保留字元都有跳脫():
    """2026-08-25 端到端踩到的：城市那對括號沒跳脫，Telegram 整則拒收
    （Can't parse entities: character '(' is reserved）。
    上面那條只比對子字串，抓不到這件事。
    """
    place = Place(name="研田拉麵", city="高雄",
                  google_maps_url="https://maps.google.com/?cid=1")

    message = PlaceBotHandlers._format_existing_places([place])

    # 連結目的地以外的保留字元都必須帶著前導反斜線
    link_start = message.index("[Google Maps](")
    body = message[:link_start]
    for ch in "()":
        for idx, c in enumerate(body):
            if c == ch:
                assert idx > 0 and body[idx - 1] == "\\", (
                    f"MarkdownV2 保留字元 {ch!r} 沒跳脫: {body!r}"
                )
