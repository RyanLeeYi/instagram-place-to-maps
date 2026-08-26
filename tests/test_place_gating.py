"""F28 回歸測試：源頭分類（E）、低信心守門（A）、行政區查準（D）三段修法。

只測 gating 邏輯本身（純函式，用假的 place_info／place_result）與 PlaceExtractor
對 is_physical 欄位的傳遞，不呼叫真正的 Places API / Ollama / Playwright——
handlers.py 的 gating helper 本來就是為了讓這件事測得到才抽成模組層級純函式。

背景：冒煙測試把「巫婆水餃店」（業配冷凍水餃品牌，Google Maps 上無實體店）比對成
「芳芳江蘇水餃」寫進使用者地圖；needs_human_check 訊號只進回覆文字，三個出口
（DB / Sheets / Maps）全無條件寫入。這組測試釘住修法後的行為。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot.handlers import _build_search_query, _place_status, _should_save_to_maps
from app.services.google_places import MatchConfidence, PlaceSearchResult
from app.services.merge_backends import PlaceInfo
from app.services.place_extractor import PlaceExtractor


class FakeMergeBackend:
    """最小假後端，回傳預先準備好的地點清單（同 tests/test_merge_backend.py 手法）。"""

    name = "fake"

    def __init__(self, places):
        self._places = places

    async def merge(self, prompt):
        return self._places


def _found_result(place_id="p1", confidence=MatchConfidence.HIGH):
    return PlaceSearchResult(found=True, place_id=place_id, match_confidence=confidence)


def _low_confidence_result(place_id="p1"):
    """比對信心度 LOW：Places 仍然 found=True，只是比對到的可能不是同一家店。"""
    return PlaceSearchResult(found=True, place_id=place_id, match_confidence=MatchConfidence.LOW)


# --- acceptance #1：is_physical 預設 True 的 fail-open --------------------


def test_PlaceInfo預設is_physical為True():
    assert PlaceInfo(name="任何店").is_physical is True


def test_預設is_physical的實體地點行為與加欄位前一致():
    """降級路徑、agy 補進來的候選都沒有明確給 is_physical，不該被這個欄位擋下來。"""
    place_info = PlaceInfo(name="阿宗蚵仔煎")
    assert _should_save_to_maps(place_info, _found_result()) is True
    assert _place_status(place_info, _found_result()) == "confirmed"


# --- acceptance #3、#4：非實體不進 Places／Maps，但仍留在 places 與 DB ------


def test_非實體不存Maps():
    place_info = PlaceInfo(name="巫婆水餃", is_physical=False)
    assert _should_save_to_maps(place_info, _found_result()) is False


def test_非實體DB狀態為non_physical():
    place_info = PlaceInfo(name="巫婆水餃", is_physical=False)
    assert _place_status(place_info, _found_result()) == "non_physical"
    # 非實體一律不送 Places，place_result 會是預設空結果——照樣要判 non_physical
    assert _place_status(place_info, PlaceSearchResult()) == "non_physical"


def test_非實體地點仍留在extract結果的places中():
    """_reconcile 不得因為 is_physical=False 就把地點濾掉（F28 scope 明文）。"""
    fake = FakeMergeBackend([
        PlaceInfo(name="巫婆水餃", is_physical=False, confidence="high"),
        PlaceInfo(name="阿宗蚵仔煎", is_physical=True, confidence="high"),
    ])
    extractor = PlaceExtractor()
    extractor._backends = [fake]

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    is_physical_by_name = {p.name: p.is_physical for p in result.places}
    assert is_physical_by_name == {"巫婆水餃": False, "阿宗蚵仔煎": True}


# --- acceptance #5：LOW 不存 Maps，status=pending，不因 found=True 標成 confirmed --


def test_低信心不存Maps():
    place_info = PlaceInfo(name="芳芳江蘇水餃")
    assert _should_save_to_maps(place_info, _low_confidence_result()) is False


def test_低信心狀態為pending_即使found為True():
    place_info = PlaceInfo(name="芳芳江蘇水餃")
    result = _low_confidence_result()
    assert result.found is True, "LOW 信心度不代表沒找到地點，只是比對到的可能不是同一家"
    assert _place_status(place_info, result) == "pending"


# --- acceptance #8：搜尋字串優先序 district（非空）> city > search_keywords[0] --


def test_搜尋字串有district時用name加district():
    place_info = PlaceInfo(
        name="阿宗蚵仔煎", city="台北", district="北投", search_keywords=["阿宗蚵仔煎 台北"]
    )
    assert _build_search_query(place_info) == "阿宗蚵仔煎 北投"


def test_搜尋字串無district時用name加city():
    place_info = PlaceInfo(name="阿宗蚵仔煎", city="台北", search_keywords=["阿宗蚵仔煎 台北"])
    assert _build_search_query(place_info) == "阿宗蚵仔煎 台北"


def test_搜尋字串都沒有時fallback為search_keywords第一個():
    place_info = PlaceInfo(name="阿宗蚵仔煎", search_keywords=["阿宗蚵仔煎 松山"])
    assert _build_search_query(place_info) == "阿宗蚵仔煎 松山"


def test_搜尋字串連keywords都沒有時fallback為name():
    place_info = PlaceInfo(name="阿宗蚵仔煎")
    assert _build_search_query(place_info) == "阿宗蚵仔煎"
