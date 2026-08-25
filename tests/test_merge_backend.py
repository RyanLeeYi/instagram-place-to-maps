"""F27.1：合併後端介面的單元測試。

驗證 PlaceExtractor.extract() 真的會呼叫注入的後端、並正確套用它的回傳；
另外用一條反向測試證明「後端有沒有被呼叫」這件事本身測得出來，不是永遠
碰不到紅燈的假安全感（同一手法見 test_no_frame_analysis_on_video.py）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.config import settings
from app.services.merge_backends import (
    OllamaMergeBackend,
    PlaceInfo,
    UnsupportedMergeBackendError,
    get_backend,
)
from app.services.place_extractor import PlaceExtractor


class FakeMergeBackend:
    """記錄有沒有被呼叫、被傳了什麼 prompt，回傳預先準備好的地點清單。"""

    def __init__(self, places):
        self._places = places
        self.calls = []

    async def merge(self, prompt):
        self.calls.append(prompt)
        return self._places


def test_extract呼叫注入的後端並套用其回傳():
    fake = FakeMergeBackend([PlaceInfo(name="阿宗蚵仔煎", confidence="high")])
    extractor = PlaceExtractor()
    extractor._backend = fake

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    assert fake.calls, "extract() 沒有呼叫注入的後端"
    assert [p.name for p in result.places] == ["阿宗蚵仔煎"]
    assert result.found is True


def test_反向_假後端沒被呼叫時上面的斷言會抓到():
    """證明上面 `assert fake.calls` 不是永遠綠燈：真的沒呼叫時它會紅。"""
    fake = FakeMergeBackend([PlaceInfo(name="不該出現")])
    # 刻意不透過 extractor.extract() 呼叫它
    assert not fake.calls


def test_get_backend_ollama回傳ollama實作():
    backend = get_backend("ollama")
    assert isinstance(backend, OllamaMergeBackend)


def test_get_backend_不支援的值會明確報錯():
    with pytest.raises(UnsupportedMergeBackendError):
        get_backend("claude")


def test_placeextractor建構時採用settings的merge_backend_不支援就報錯(monkeypatch):
    """驗收要求：不支援的後端要在啟動或呼叫時報明確錯誤，不得靜默 fallback。"""
    monkeypatch.setattr(settings, "merge_backend", "not-a-real-backend")
    with pytest.raises(UnsupportedMergeBackendError):
        PlaceExtractor()
