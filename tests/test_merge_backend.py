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
    MergeFailure,
    OllamaMergeBackend,
    PlaceInfo,
    UnsupportedMergeBackendError,
    get_backend,
)
from app.services.place_extractor import ExtractionResult, PlaceExtractor


class FakeMergeBackend:
    """記錄有沒有被呼叫、被傳了什麼 prompt，回傳預先準備好的地點清單。"""

    def __init__(self, places):
        self._places = places
        self.calls = []

    async def merge(self, prompt):
        self.calls.append(prompt)
        return self._places


def _跑一次並斷言後端有被用到(extractor, fake):
    """正向斷言抽成函式，好讓反向測試能拿同一段去跑壞掉的實作。

    分開寫的理由：反向測試如果自己另外寫一組斷言，它證明的就是它自己那組，
    不是正向測試那組。要證明「正向斷言抓得到迴歸」，就得讓它跑同一段程式。
    """
    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )
    assert fake.calls, "extract() 沒有呼叫注入的後端"
    return result


def test_extract呼叫注入的後端並套用其回傳():
    fake = FakeMergeBackend([PlaceInfo(name="阿宗蚵仔煎", confidence="high")])
    extractor = PlaceExtractor()
    extractor._backend = fake

    result = _跑一次並斷言後端有被用到(extractor, fake)

    assert [p.name for p in result.places] == ["阿宗蚵仔煎"]
    assert result.found is True


def test_反向_後端沒被呼叫時正向斷言真的會紅(monkeypatch):
    """把 extract() 換成不碰後端的實作，證明上面那句 assert 抓得到。

    這條原本只斷言「剛建好的 fake 沒有 calls」——恆真式。2026-08-25 驗收者
    用 mutation 證實：把 extract() 換成完全不碰 self._backend 的版本之後，
    正向測試正確變紅，但這條反向測試依然是綠的，等於它從沒驗證過自己
    docstring 宣稱的事。改成對著真的壞掉的實作跑同一段斷言。
    """
    fake = FakeMergeBackend([PlaceInfo(name="不該出現")])
    extractor = PlaceExtractor()
    extractor._backend = fake

    async def 不碰後端的_extract(*args, **kwargs):
        return ExtractionResult(found=False)

    monkeypatch.setattr(extractor, "extract", 不碰後端的_extract)

    with pytest.raises(AssertionError):
        _跑一次並斷言後端有被用到(extractor, fake)


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


# --- 後端失敗時的診斷文字（D2：搬家時弄丟過，2026-08-25 驗收抓到）---------


def _parse(text):
    """直接打 OllamaMergeBackend._parse，不碰 ollama。"""
    return OllamaMergeBackend()._parse(text)


def test_找不到_JSON_時帶出原因():
    with pytest.raises(MergeFailure) as e:
        _parse("模型今天想聊天，一個大括號都沒有")
    assert e.value.notes == "無法解析回應"


def test_JSON_壞掉時帶出原因():
    # 要有閉合大括號才會走到解析那一步，否則會先被判成「找不到 JSON」
    with pytest.raises(MergeFailure) as e:
        _parse('{"found": true, "places": [{"name": "a",,}]}')
    assert "JSON 解析失敗" in e.value.notes


def test_模型自己說沒找到時把它的理由帶出來():
    """模型附的理由是有資訊的，不能換成通用文案。"""
    with pytest.raises(MergeFailure) as e:
        _parse('{"found": false, "places": [], "notes": "這是穿搭影片，沒有店家"}')
    assert e.value.notes == "這是穿搭影片，沒有店家"


class _壞掉的後端:
    async def merge(self, prompt):
        raise MergeFailure("無法解析回應")


def test_後端解析失敗時_notes_會送到使用者眼前():
    extractor = PlaceExtractor()
    extractor._backend = _壞掉的後端()

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    assert result.found is False
    assert result.notes == "無法解析回應"
    assert result.error_message == "無法解析回應"


def test_後端解析失敗時仍然跑_reconcile_讓_agy_候選救回來():
    """這條是 D2 真正要守住的行為。

    舊版在解析失敗時照樣跑 _reconcile，所以本地模型輸出爛掉時，agy 標為
    主角的店家還救得回來、found 甚至翻回 True。搬家時如果在失敗分支直接
    return，這條路就斷了——而且不會有任何測試變紅。
    """
    from app.services.gemini_video import GeminiPlace

    extractor = PlaceExtractor()
    extractor._backend = _壞掉的後端()

    result = asyncio.run(
        extractor.extract(
            transcript="",
            visual_description="",
            caption="",
            gemini_places=[GeminiPlace(name="阿宗蚵仔煎", is_recommended=True)],
        )
    )

    assert result.found is True, "解析失敗時 agy 候選應該還救得回來"
    assert [p.name for p in result.places] == ["阿宗蚵仔煎"]
    assert result.notes == "無法解析回應", "救回來了也要保留失敗原因"
