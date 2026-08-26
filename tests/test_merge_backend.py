"""F27.1/F27.2：合併後端介面與鏈式執行模式的單元測試。

F27.1：驗證 PlaceExtractor.extract() 真的會呼叫注入的後端、並正確套用它的
回傳；另外用一條反向測試證明「後端有沒有被呼叫」這件事本身測得出來，不是
永遠碰不到紅燈的假安全感（同一手法見 test_no_frame_analysis_on_video.py）。

F27.2：合併階段從「單一後端」改成「一條後端鏈 + 一種執行模式」
（failover / vote）。測試注入接縫也從 `extractor._backend`（單一物件）
改成 `extractor._backends`（list）——F27.1 留下的三處注入（原第 54、135、
157 行）在這裡一併改寫。
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.config import RuntimeSettings, runtime_settings, settings
from app.services.merge_backends import (
    MergeFailure,
    OllamaMergeBackend,
    PlaceInfo,
    UnsupportedMergeBackendError,
    UnsupportedMergeModeError,
    get_backend,
    get_backend_chain,
    merge_with_backends,
    norm_place_name,
)
from app.services.place_extractor import ExtractionResult, PlaceExtractor


class FakeMergeBackend:
    """記錄有沒有被呼叫、被傳了什麼 prompt，回傳預先準備好的地點清單。"""

    def __init__(self, places, name="fake"):
        self._places = places
        self.name = name
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
    extractor._backends = [fake]

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
    extractor._backends = [fake]

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


# --- F27.2：後端鏈與模式的建構期驗證（acceptance #2、#3）------------------


def test_鏈為空時建構丟例外(monkeypatch):
    monkeypatch.setattr(settings, "merge_backends", "")
    with pytest.raises(UnsupportedMergeBackendError):
        PlaceExtractor()


def test_鏈含不支援名稱時建構丟例外(monkeypatch):
    """驗收要求：不支援的後端要在啟動時報明確錯誤，不得靜默 fallback。"""
    monkeypatch.setattr(settings, "merge_backends", "ollama,claude")
    with pytest.raises(UnsupportedMergeBackendError):
        PlaceExtractor()


def test_merge_mode不合法時建構丟例外(monkeypatch):
    monkeypatch.setattr(settings, "merge_mode", "weighted")
    with pytest.raises(UnsupportedMergeModeError):
        PlaceExtractor()


def test_get_backend_chain解析逗號分隔的鏈():
    backends = get_backend_chain("ollama, ollama")
    assert len(backends) == 2
    assert all(isinstance(b, OllamaMergeBackend) for b in backends)


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
    name = "ollama"

    async def merge(self, prompt):
        raise MergeFailure("無法解析回應")


def test_後端解析失敗時_notes_會送到使用者眼前():
    """F27.2 起，notes 是 `merge_with_backends()` 全敗時組出來的
    「後端名稱：原因」清單（acceptance #9），不再是後端自己的原始 notes。
    這裡只有一個後端，格式退化成單一一條，但仍要帶出後端名稱與原因。
    """
    extractor = PlaceExtractor()
    extractor._backends = [_壞掉的後端()]

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    assert result.found is False
    assert result.notes == "ollama：無法解析回應"
    assert result.error_message == "ollama：無法解析回應"
    assert result.backend_note is None, "全敗時 backend_note 要是 None，原因走 notes"


def test_後端解析失敗時仍然跑_reconcile_讓_agy_候選救回來():
    """這條是 D2 真正要守住的行為。

    舊版在解析失敗時照樣跑 _reconcile，所以本地模型輸出爛掉時，agy 標為
    主角的店家還救得回來、found 甚至翻回 True。搬家時如果在失敗分支直接
    return，這條路就斷了——而且不會有任何測試變紅。
    """
    from app.services.gemini_video import GeminiPlace

    extractor = PlaceExtractor()
    extractor._backends = [_壞掉的後端()]

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
    assert result.notes == "ollama：無法解析回應", "救回來了也要保留失敗原因"


# --- F27.2：norm_place_name 搬到模組層級，PlaceExtractor._norm_name 轉呼叫 --


def test_norm_place_name與placeextractor_norm_name行為一致():
    """acceptance #11：不得在兩個模組各留一份正規化邏輯（會漂移）。"""
    assert PlaceExtractor._norm_name("巫婆水餃店") == norm_place_name("巫婆水餃店") == "巫婆水餃"


# --- F27.2：failover 模式（acceptance #7、#8、#9、#21） --------------------


class _CountingBackend:
    """記錄自己被呼叫幾次的後端，回傳固定清單。"""

    def __init__(self, name, places):
        self.name = name
        self._places = places
        self.calls = 0

    async def merge(self, prompt):
        self.calls += 1
        return self._places


class _FailingBackend:
    """依 failure_mode 用三種不同方式失敗：MergeFailure、任意例外、空清單。"""

    def __init__(self, name, failure_mode, notes="失敗原因"):
        self.name = name
        self._failure_mode = failure_mode
        self._notes = notes

    async def merge(self, prompt):
        if self._failure_mode == "merge_failure":
            raise MergeFailure(self._notes)
        if self._failure_mode == "exception":
            raise RuntimeError(self._notes)
        return []


def _failover只呼叫到第一個成功為止(run_failover):
    """正向斷言抽成函式，好讓反向測試能跑同一段驗證錯誤實作（acceptance #21，
    同一手法見 F27.1 的 `_跑一次並斷言後端有被用到`）。"""
    first = _CountingBackend("first", [PlaceInfo(name="A")])
    second = _CountingBackend("second", [PlaceInfo(name="B")])

    places, note = asyncio.run(run_failover([first, second], "prompt"))

    assert first.calls == 1
    assert second.calls == 0, "failover 找到答案後不該再呼叫後面的後端"
    return places, note


def test_failover找到答案後不再呼叫後面的後端():
    async def run(backends, prompt):
        return await merge_with_backends(backends, prompt, "failover")

    places, note = _failover只呼叫到第一個成功為止(run)
    assert [p.name for p in places] == ["A"]
    assert note == "採用後端：first"


def test_反向_全部呼叫的錯誤實作會被抓到():
    """把 failover 換成「即使找到答案也繼續呼叫其餘後端」的錯誤實作，證明
    上面那句 `second.calls == 0` 抓得到這種迴歸。"""

    async def 錯誤的_全部呼叫版本(backends, prompt):
        places, note = [], None
        for b in backends:
            p = await b.merge(prompt)
            if p and not places:
                places, note = p, f"採用後端：{b.name}"
        return places, note

    with pytest.raises(AssertionError):
        _failover只呼叫到第一個成功為止(錯誤的_全部呼叫版本)


@pytest.mark.parametrize("failure_mode", ["merge_failure", "exception", "empty"])
def test_failover前一個以任何形式失敗都會往下一個試(failure_mode):
    failing = _FailingBackend("first", failure_mode)
    succeeding = _CountingBackend("second", [PlaceInfo(name="B")])

    places, note = asyncio.run(
        merge_with_backends([failing, succeeding], "prompt", "failover")
    )

    assert [p.name for p in places] == ["B"]
    assert note == "採用後端：second"


def test_failover全部失敗時notes逐一列出每個後端各自原因():
    b1 = _FailingBackend("first", "merge_failure", notes="解析失敗")
    b2 = _FailingBackend("second", "exception", notes="連線失敗")

    with pytest.raises(MergeFailure) as e:
        asyncio.run(merge_with_backends([b1, b2], "prompt", "failover"))

    assert "first" in e.value.notes and "解析失敗" in e.value.notes
    assert "second" in e.value.notes and "連線失敗" in e.value.notes


# --- F27.2：vote 模式（acceptance #10、#12、#13、#14、#15、#16） ----------


class _WaitForOtherBackend:
    """兩個後端互相等對方先開始才回。依序 await 會死鎖到逾時，
    併發 gather 兩個都推進得了（acceptance #10）。"""

    def __init__(self, name, my_event, other_event):
        self.name = name
        self._my_event = my_event
        self._other_event = other_event

    async def merge(self, prompt):
        self._my_event.set()
        await asyncio.wait_for(self._other_event.wait(), timeout=1.0)
        # 兩個後端回同一家店，讓 n=2 的門檻（需兩家都投）能通過——
        # 這條測試只在乎「兩個後端有沒有真的併發跑」，不是投票邏輯本身。
        return [PlaceInfo(name="共同店家")]


class _DelayedBackend:
    """固定延遲後回傳清單，用來讓完成順序與鏈設定順序不一致。"""

    def __init__(self, name, places, delay):
        self.name = name
        self._places = places
        self._delay = delay

    async def merge(self, prompt):
        await asyncio.sleep(self._delay)
        return self._places


def test_vote模式併發呼叫而非依序():
    event_a = asyncio.Event()
    event_b = asyncio.Event()
    backend_a = _WaitForOtherBackend("A", event_a, event_b)
    backend_b = _WaitForOtherBackend("B", event_b, event_a)

    places, note = asyncio.run(
        merge_with_backends([backend_a, backend_b], "prompt", "vote")
    )

    assert [p.name for p in places] == ["共同店家"]


def test_vote門檻_n1全留():
    b1 = _CountingBackend("A", [PlaceInfo(name="甲店"), PlaceInfo(name="乙店")])

    places, note = asyncio.run(merge_with_backends([b1], "prompt", "vote"))

    assert {p.name for p in places} == {"甲店", "乙店"}
    assert note == "投票後端：A(2票)"


def test_vote門檻_n2需兩家都有():
    b1 = _CountingBackend("A", [PlaceInfo(name="甲店"), PlaceInfo(name="乙店")])
    b2 = _CountingBackend("B", [PlaceInfo(name="甲店")])

    places, note = asyncio.run(merge_with_backends([b1, b2], "prompt", "vote"))

    assert [p.name for p in places] == ["甲店"]


def test_vote門檻_n3需兩家():
    b1 = _CountingBackend("A", [PlaceInfo(name="甲店")])
    b2 = _CountingBackend("B", [PlaceInfo(name="甲店")])
    b3 = _CountingBackend("C", [PlaceInfo(name="乙店")])

    places, note = asyncio.run(merge_with_backends([b1, b2, b3], "prompt", "vote"))

    assert [p.name for p in places] == ["甲店"]


def test_vote同後端內同名地點只算一票():
    b1 = _CountingBackend("A", [PlaceInfo(name="巫婆水餃"), PlaceInfo(name="巫婆水餃店")])
    b2 = _CountingBackend("B", [PlaceInfo(name="其他店")])
    b3 = _CountingBackend("C", [PlaceInfo(name="其他店")])

    places, note = asyncio.run(merge_with_backends([b1, b2, b3], "prompt", "vote"))

    names = {p.name for p in places}
    assert "巫婆水餃" not in names and "巫婆水餃店" not in names, (
        "同後端內部重複只能算一票，不該達到 n=3 的門檻 2"
    )
    assert "其他店" in names


def test_vote欄位合併取鏈設定順序中第一個非空值():
    b1 = _CountingBackend("A", [PlaceInfo(name="甲店", city=None, address="地址A")])
    b2 = _CountingBackend("B", [PlaceInfo(name="甲店", city="台北", address="地址B")])

    places, note = asyncio.run(merge_with_backends([b1, b2], "prompt", "vote"))

    assert len(places) == 1
    place = places[0]
    assert place.city == "台北", "A 的 city 是空的，該補 B 的值"
    assert place.address == "地址A", "A 先有值，不該被 B 覆蓋"


def test_vote輸出順序依鏈設定順序不依完成順序():
    slow_first = _DelayedBackend(
        "A", [PlaceInfo(name="甲店"), PlaceInfo(name="乙店")], delay=0.05
    )
    fast_second = _DelayedBackend(
        "B", [PlaceInfo(name="乙店"), PlaceInfo(name="甲店")], delay=0.0
    )

    places, note = asyncio.run(
        merge_with_backends([slow_first, fast_second], "prompt", "vote")
    )

    assert [p.name for p in places] == ["甲店", "乙店"], (
        "輸出順序要依鏈設定順序（A 在前），不是依完成順序（B 先完成）"
    )


def test_vote全部失敗時比照failover全敗處理():
    b1 = _FailingBackend("first", "exception", notes="連線失敗")
    b2 = _FailingBackend("second", "empty")

    with pytest.raises(MergeFailure) as e:
        asyncio.run(merge_with_backends([b1, b2], "prompt", "vote"))

    assert "first" in e.value.notes and "連線失敗" in e.value.notes
    assert "second" in e.value.notes


def test_vote成功時backend_note列出每個後端與票數():
    b1 = _CountingBackend("A", [PlaceInfo(name="甲店")])
    b2 = _CountingBackend("B", [PlaceInfo(name="甲店")])

    places, note = asyncio.run(merge_with_backends([b1, b2], "prompt", "vote"))

    assert note == "投票後端：A(1票)、B(1票)"


# --- F27.2：ExtractionResult.backend_note 透過 extract() 端到端驗證（#16） --


def test_extract_failover成功時backend_note是採用後端格式():
    fake = FakeMergeBackend([PlaceInfo(name="甲店")], name="ollama")
    extractor = PlaceExtractor()
    extractor._backends = [fake]

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    assert result.backend_note == "採用後端：ollama"


def test_extract_vote模式成功時backend_note是投票後端格式(monkeypatch):
    monkeypatch.setattr(runtime_settings, "_merge_mode", "vote")
    b1 = FakeMergeBackend([PlaceInfo(name="甲店")], name="A")
    b2 = FakeMergeBackend([PlaceInfo(name="甲店")], name="B")
    extractor = PlaceExtractor()
    extractor._backends = [b1, b2]

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )

    assert result.backend_note == "投票後端：A(1票)、B(1票)"


# --- F27.2：merge_mode 每次 extract() 都重讀，不在建構時快取（acceptance #5） --


def test_merge_mode每次extract都重讀不在建構時快取(monkeypatch):
    """PlaceExtractor 是長生命週期物件（handlers.py 只建構一次、活到 bot
    結束），所以模式不能在 __init__ 讀完就快取。建構之後改
    runtime_settings 的模式，下一次 extract() 就要走新模式，不必重啟。"""
    monkeypatch.setattr(runtime_settings, "_merge_mode", None)  # 用 env 預設 failover

    first = FakeMergeBackend([PlaceInfo(name="甲店")], name="first")
    second = FakeMergeBackend([PlaceInfo(name="乙店")], name="second")
    extractor = PlaceExtractor()
    extractor._backends = [first, second]

    asyncio.run(extractor.extract(transcript="", visual_description="", caption=""))
    assert len(first.calls) == 1
    assert len(second.calls) == 0, "failover 模式下第一個成功後不該呼叫第二個"

    monkeypatch.setattr(runtime_settings, "_merge_mode", "vote")
    asyncio.run(extractor.extract(transcript="", visual_description="", caption=""))
    assert len(second.calls) == 1, "改成 vote 後下一次 extract() 應該呼叫到第二個後端"


# --- F27.2：runtime_settings.json 存壞掉的 merge_mode（acceptance #6） -----


def test_runtime_settings_json有非法merge_mode時警告並退回env值(tmp_path, monkeypatch, caplog):
    """持久化檔案是運行期可能損毀的資料，不是啟動期設定錯誤——不得讓運行中
    的 bot 崩潰，只警告並退回 MERGE_MODE 的值（與 acceptance #3 刻意不對稱）。
    """
    (tmp_path / "runtime_settings.json").write_text(
        json.dumps({"merge_mode": "not-a-real-mode"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING)

    rs = RuntimeSettings()

    assert rs.merge_mode == settings.merge_mode
    assert any("merge_mode" in record.message for record in caplog.records)


def test_set_merge_mode合法值更新_非法值不覆蓋(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_settings, "_settings_file", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(runtime_settings, "_merge_mode", None)

    assert runtime_settings.set_merge_mode("vote") is True
    assert runtime_settings.merge_mode == "vote"

    assert runtime_settings.set_merge_mode("not-a-mode") is False
    assert runtime_settings.merge_mode == "vote", "非法值不該覆蓋掉原本的設定"


def test_合併成功的log逐一列出各後端成功或失敗(caplog):
    """acceptance #18：成功分支的 INFO log 也要列出呼叫過但失敗的後端，
    不能只統計成功子集（2026-08-26 驗收抓到，失敗的 C 被整個略去）。"""
    import logging

    ok_a = _CountingBackend("A", [PlaceInfo(name="甲店")])
    ok_b = _CountingBackend("B", [PlaceInfo(name="甲店")])
    bad_c = _FailingBackend("C", "merge_failure", notes="解析失敗")

    with caplog.at_level(logging.INFO):
        asyncio.run(merge_with_backends([ok_a, ok_b, bad_c], "prompt", "vote"))
    vote_log = next(r.message for r in caplog.records if "mode=vote" in r.message)
    assert "A:成功" in vote_log and "B:成功" in vote_log
    assert "C:失敗" in vote_log and "解析失敗" in vote_log

    caplog.clear()
    bad_first = _FailingBackend("X", "empty")
    ok_y = _CountingBackend("Y", [PlaceInfo(name="乙店")])
    with caplog.at_level(logging.INFO):
        asyncio.run(merge_with_backends([bad_first, ok_y], "prompt", "failover"))
    fo_log = next(r.message for r in caplog.records if "mode=failover" in r.message)
    assert "X:失敗" in fo_log and "Y:成功" in fo_log
