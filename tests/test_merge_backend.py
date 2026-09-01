"""F27.1/F27.2：合併後端介面與鏈式執行模式的單元測試。

F27.1：驗證 PlaceExtractor.extract() 真的會呼叫注入的後端、並正確套用它的
回傳；另外用一條反向測試證明「後端有沒有被呼叫」這件事本身測得出來，不是
永遠碰不到紅燈的假安全感（同一手法見 test_no_frame_analysis_on_video.py）。

F27.2：合併階段從「單一後端」改成「一條後端鏈 + 一種執行模式」
（failover / vote）。測試注入接縫也從 `extractor._backend`（單一物件）
改成 `extractor._backends`（list）——F27.1 留下的三處注入（原第 54、135、
157 行）在這裡一併改寫。

F27.3／F27.4：CLI 版與 SDK 版各三家後端。兩者的假替身層級不同——CLI 版換掉
subprocess，SDK 版換掉 client 物件——但共用同一條規則：測試不得建立真的
client、不得打真 API，這件事由 `_sdk_backend()` 裡的地雷斷言釘住。
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

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
from app.services import merge_cli_backends as mcb
from app.services.merge_cli_backends import (
    AgyMergeBackend,
    ClaudeMergeBackend,
    CodexMergeBackend,
)
from app.services.merge_sdk_backends import (
    ClaudeApiMergeBackend,
    CodexApiMergeBackend,
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
        get_backend("gemini")


# --- F27.2：後端鏈與模式的建構期驗證（acceptance #2、#3）------------------


def test_鏈為空時建構丟例外(monkeypatch):
    monkeypatch.setattr(settings, "merge_backends", "")
    with pytest.raises(UnsupportedMergeBackendError):
        PlaceExtractor()


def test_鏈含不支援名稱時建構丟例外(monkeypatch):
    """驗收要求：不支援的後端要在啟動時報明確錯誤，不得靜默 fallback。"""
    monkeypatch.setattr(settings, "merge_backends", "ollama,gemini")
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


# --- F27.3：CLI 版合併後端 agy／claude／codex（acceptance #1-#7） ----------


class _FakeCliProc:
    """假 subprocess：communicate() 回傳固定 stdout/stderr，可延遲或丟例外。"""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, delay=0.0, exc=None):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._delay = delay
        self._exc = exc
        self.killed = False

    async def communicate(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _patch_cli(monkeypatch, proc, which_path="C:/fake/cli.exe"):
    """把 create_subprocess_exec 與 shutil.which 都換成假的，不連外、不打真 CLI。"""

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(mcb.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(mcb.shutil, "which", lambda cmd: which_path)


def _codex_jsonl(*events) -> bytes:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events).encode("utf-8")


def test_get_backend認得agy_claude_codex():
    """acceptance #1：三個新名稱都要能建構出對應實作。"""
    assert isinstance(get_backend("agy"), AgyMergeBackend)
    assert isinstance(get_backend("claude"), ClaudeMergeBackend)
    assert isinstance(get_backend("codex"), CodexMergeBackend)


@pytest.mark.parametrize("cls", [AgyMergeBackend, ClaudeMergeBackend, CodexMergeBackend])
def test_CLI執行檔不存在時轉MergeFailure而非建構期報錯(monkeypatch, cls):
    """acceptance #1：CLI 不存在不擋建構，merge() 時才以 MergeFailure 呈現。"""
    monkeypatch.setattr(mcb.shutil, "which", lambda cmd: None)
    with pytest.raises(MergeFailure) as e:
        asyncio.run(cls().merge("prompt"))
    assert "找不到 CLI 執行檔" in e.value.notes


@pytest.mark.parametrize("cls", [AgyMergeBackend, ClaudeMergeBackend, CodexMergeBackend])
def test_逾時時殺掉子行程並轉MergeFailure(monkeypatch, cls):
    """acceptance #3。"""
    proc = _FakeCliProc(delay=999)
    _patch_cli(monkeypatch, proc)
    monkeypatch.setattr(settings, "merge_cli_timeout", 0.05)

    with pytest.raises(MergeFailure) as e:
        asyncio.run(cls().merge("prompt"))

    assert proc.killed, "逾時要殺掉子行程"
    assert "逾時" in e.value.notes


def test_agy_成功時解析地點(monkeypatch):
    envelope = json.dumps({
        "status": "SUCCESS",
        "response": json.dumps({"found": True, "places": [{"name": "測試店"}]}, ensure_ascii=False),
    }, ensure_ascii=False).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    places = asyncio.run(AgyMergeBackend().merge("prompt"))

    assert [p.name for p in places] == ["測試店"]


def test_agy_外層status不是SUCCESS時整份丟棄連內容都不看(monkeypatch):
    """acceptance #5：agy 讀不到輸入時會編像樣的答案，status 才是唯一可信欄位。"""
    envelope = json.dumps({
        "status": "ERROR",
        "error": "permission check failed for read_file",
        "response": json.dumps({"found": True, "places": [{"name": "編造的店"}]}, ensure_ascii=False),
    }, ensure_ascii=False).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    with pytest.raises(MergeFailure) as e:
        asyncio.run(AgyMergeBackend().merge("prompt"))

    assert "編造的店" not in (e.value.notes or "")
    assert "status=ERROR" in e.value.notes


def test_agy_內層JSON壞掉時轉MergeFailure(monkeypatch):
    envelope = json.dumps(
        {"status": "SUCCESS", "response": "模型今天想聊天，一個大括號都沒有"},
        ensure_ascii=False,
    ).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    with pytest.raises(MergeFailure):
        asyncio.run(AgyMergeBackend().merge("prompt"))


def test_claude_成功時解析地點(monkeypatch):
    envelope = json.dumps({
        "is_error": False,
        "result": json.dumps({"found": True, "places": [{"name": "測試店"}]}, ensure_ascii=False),
    }, ensure_ascii=False).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    places = asyncio.run(ClaudeMergeBackend().merge("prompt"))

    assert [p.name for p in places] == ["測試店"]


def test_claude_is_error時整份丟棄不把內容當結果(monkeypatch):
    """acceptance #5：`is_error` 為真時，就算 `result` 欄位剛好是格式正確、
    看起來像樣的地點 JSON，也不能被解析成真的結果——先判斷成功與否，
    成功才碰內容，不是「內容長得像就信」。
    """
    fabricated_result = json.dumps(
        {"found": True, "places": [{"name": "編造的店"}]}, ensure_ascii=False
    )
    envelope = json.dumps({
        "is_error": True,
        "subtype": "error_during_execution",
        "result": fabricated_result,
    }, ensure_ascii=False).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    with pytest.raises(MergeFailure) as e:
        asyncio.run(ClaudeMergeBackend().merge("prompt"))

    assert "error_during_execution" in e.value.notes


def test_claude_內層JSON壞掉時轉MergeFailure(monkeypatch):
    envelope = json.dumps(
        {"is_error": False, "result": "不是 JSON 的自然語言回覆"}, ensure_ascii=False
    ).encode("utf-8")
    _patch_cli(monkeypatch, _FakeCliProc(stdout=envelope))

    with pytest.raises(MergeFailure):
        asyncio.run(ClaudeMergeBackend().merge("prompt"))


def test_codex_成功時取turn_completed內的agent_message(monkeypatch):
    payload = json.dumps({"found": True, "places": [{"name": "測試店"}]}, ensure_ascii=False)
    stdout = _codex_jsonl(
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": payload}},
        {"type": "turn.completed"},
    )
    _patch_cli(monkeypatch, _FakeCliProc(stdout=stdout))

    places = asyncio.run(CodexMergeBackend().merge("prompt"))

    assert [p.name for p in places] == ["測試店"]


def test_codex_中途警告事件不影響turn_completed後的成功判定(monkeypatch):
    """acceptance #5：item.completed(type=error) 可能只是警告，不是失敗信號
    （2026-08-26 對著真 codex 實測過 model metadata 找不到／skill 描述截短
    這兩種都是這種形狀，但那次是真的失敗——這裡驗證的是「不能只憑中途出現
    error item 就判失敗」，真正的判準是有沒有等到 turn.completed）。
    """
    payload = json.dumps({"found": True, "places": [{"name": "測試店"}]}, ensure_ascii=False)
    stdout = _codex_jsonl(
        {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": "model metadata not found"}},
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": payload}},
        {"type": "turn.completed"},
    )
    _patch_cli(monkeypatch, _FakeCliProc(stdout=stdout))

    places = asyncio.run(CodexMergeBackend().merge("prompt"))

    assert [p.name for p in places] == ["測試店"]


def test_codex_turn_failed時整份丟棄不把內容當結果(monkeypatch):
    """acceptance #5：即使串流裡出現過格式正確的 agent_message，只要終局是
    turn.failed，就不能被解析成真的結果。"""
    fabricated = json.dumps({"found": True, "places": [{"name": "編造的店"}]}, ensure_ascii=False)
    stdout = _codex_jsonl(
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": fabricated}},
        {"type": "error", "message": "boom"},
        {"type": "turn.failed", "error": {"message": "boom"}},
    )
    _patch_cli(monkeypatch, _FakeCliProc(stdout=stdout, returncode=1))

    with pytest.raises(MergeFailure) as e:
        asyncio.run(CodexMergeBackend().merge("prompt"))

    assert "boom" in e.value.notes


def test_codex_沒有turn_completed時判定失敗(monkeypatch):
    """輸出被截斷或整串跑完都沒等到終局事件，一樣不能當成功。"""
    stdout = _codex_jsonl({"type": "thread.started", "thread_id": "t1"})
    _patch_cli(monkeypatch, _FakeCliProc(stdout=stdout))

    with pytest.raises(MergeFailure) as e:
        asyncio.run(CodexMergeBackend().merge("prompt"))

    assert "turn.completed" in e.value.notes


def test_codex_內層JSON壞掉時轉MergeFailure(monkeypatch):
    stdout = _codex_jsonl(
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "不是 JSON"}},
        {"type": "turn.completed"},
    )
    _patch_cli(monkeypatch, _FakeCliProc(stdout=stdout))

    with pytest.raises(MergeFailure):
        asyncio.run(CodexMergeBackend().merge("prompt"))


# --- F27.4：SDK 版合併後端 claude-api／codex-api（acceptance #1-#6） ---------
#
# 兩家都用假 client 注入 `backend._client`，因此 `_build_client()` 不會執行、
# 沒裝的 SDK 不影響測試、也永遠打不到真 API（acceptance #6）。


class _FakeSdkCall:
    """假的 SDK 呼叫：記錄參數，回傳預備好的結果、或延遲、或丟預備好的例外。"""

    def __init__(self, result=None, exc=None, delay=0.0):
        self._result = result
        self._exc = exc
        self._delay = delay
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._result


class _FakeApiError(Exception):
    """帶 status_code 的假 SDK 錯誤。

    刻意不繼承 anthropic／openai 的錯誤類別：產品程式是用鴨子型別讀
    `status_code`，測試跟著用鴨子型別，才不會在 SDK 換版時一起壞掉。
    """

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def _make_claude_client(text=None, exc=None, delay=0.0):
    call = _FakeSdkCall(SimpleNamespace(content=[SimpleNamespace(text=text)]), exc, delay)
    client = SimpleNamespace(messages=SimpleNamespace(create=call))
    return client, call


def _make_codex_client(text=None, exc=None, delay=0.0):
    call = _FakeSdkCall(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))]),
        exc,
        delay,
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call)))
    return client, call


# (後端類別, settings 上的 key 欄位, 對應的 ENV 名, 假 client 工廠)
_SDK_CASES = [
    (ClaudeApiMergeBackend, "anthropic_api_key", "ANTHROPIC_API_KEY", _make_claude_client),
    (CodexApiMergeBackend, "openai_api_key", "OPENAI_API_KEY", _make_codex_client),
]
_SDK_IDS = [case[0].name for case in _SDK_CASES]

_FAKE_KEY = "sk-test-DO-NOT-LEAK-0123456789"


def _sdk_backend(cls, key_attr, monkeypatch, client=None, key=_FAKE_KEY):
    """建一個 SDK 後端：設好 key、注入假 client、並把 _build_client 換成地雷。

    地雷那步是重點——它把「測試不打真 API」從口頭約定變成會紅的斷言
    （acceptance #6）。
    """
    for _, attr, _, _ in _SDK_CASES:
        monkeypatch.setattr(settings, attr, "")
    monkeypatch.setattr(settings, key_attr, key)

    def _land_mine(*args, **kwargs):
        raise AssertionError("測試不得建立真的 SDK client")

    monkeypatch.setattr(cls, "_build_client", _land_mine)

    backend = cls()
    backend._client = client
    return backend


def test_get_backend認得兩個api名稱():
    """acceptance #2。"""
    assert isinstance(get_backend("claude-api"), ClaudeApiMergeBackend)
    assert isinstance(get_backend("codex-api"), CodexApiMergeBackend)


@pytest.mark.parametrize("name", ["agy-api", "gemini-api"])
def test_未知的api名稱仍照舊丟UnsupportedMergeBackendError(name):
    """acceptance #2：agy-api 在 2026-08-28 被砍掉，要跟其他不認得的名字一樣明確報錯。"""
    with pytest.raises(UnsupportedMergeBackendError):
        get_backend(name)


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端成功時解析地點(monkeypatch, cls, key_attr, env_name, make):
    """acceptance #6：成功路徑。"""
    payload = json.dumps(
        {"found": True, "places": [{"name": "測試店", "confidence": "high"}]},
        ensure_ascii=False,
    )
    client, call = make(text=payload)
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)

    places = asyncio.run(backend.merge("prompt"))

    assert [p.name for p in places] == ["測試店"]
    assert call.calls, "沒有真的呼叫到 SDK"


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端缺key時以未設定ENV名的MergeFailure呈現(monkeypatch, cls, key_attr, env_name, make):
    """acceptance #1：留空＝停用；連 client 都不該碰，啟動與鏈都不受影響。"""
    client, call = make(text='{"found": true, "places": [{"name": "不該被叫到"}]}')
    backend = _sdk_backend(cls, key_attr, monkeypatch, client, key="")

    with pytest.raises(MergeFailure) as e:
        asyncio.run(backend.merge("prompt"))

    assert "未設定" in e.value.notes
    assert env_name in e.value.notes
    assert call.calls == [], "缺 key 時不該呼叫 SDK"


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
@pytest.mark.parametrize("status,expect", [(401, "認證失敗"), (429, "速率限制"), (503, "伺服器錯誤")])
def test_sdk後端API錯誤收斂成MergeFailure且notes可讀(
    monkeypatch, cls, key_attr, env_name, make, status, expect
):
    """acceptance #4：401／429／5xx 都變成看得懂的 MergeFailure，不外流成別的例外。"""
    client, _ = make(exc=_FakeApiError(status, "upstream said no"))
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)

    with pytest.raises(MergeFailure) as e:
        asyncio.run(backend.merge("prompt"))

    assert expect in e.value.notes
    assert str(status) in e.value.notes
    assert cls.name in e.value.notes


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端逾時收斂成MergeFailure(monkeypatch, cls, key_attr, env_name, make):
    """acceptance #4：呼叫帶逾時。外層 wait_for 是唯一的總時長保證。"""
    client, _ = make(text="{}", delay=999)
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)
    monkeypatch.setattr(settings, "merge_sdk_timeout", 0.05)

    with pytest.raises(MergeFailure) as e:
        asyncio.run(backend.merge("prompt"))

    assert "逾時" in e.value.notes
    assert cls.name in e.value.notes


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端內層JSON壞掉時轉MergeFailure(monkeypatch, cls, key_attr, env_name, make):
    """acceptance #6：內層 JSON 壞掉。"""
    client, _ = make(text="這裡完全沒有 JSON")
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)

    with pytest.raises(MergeFailure):
        asyncio.run(backend.merge("prompt"))


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端回應沒有文字內容時轉MergeFailure(monkeypatch, cls, key_attr, env_name, make):
    """空回應不能被當成「解析失敗」以外的東西默默吞掉。"""
    client, _ = make(text=None)
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)

    with pytest.raises(MergeFailure) as e:
        asyncio.run(backend.merge("prompt"))

    assert "沒有文字內容" in e.value.notes


@pytest.mark.parametrize("cls,key_attr,env_name,make", _SDK_CASES, ids=_SDK_IDS)
def test_sdk後端的例外訊息與log都不洩漏key內容(
    monkeypatch, cls, key_attr, env_name, make, caplog
):
    """acceptance #1：mission-control F53 的前例——含 token 的訊息被原樣存下再送出。

    這裡刻意讓 SDK 例外訊息「就是」夾帶 key（真實情境：SDK 把請求細節塞進
    訊息），驗證 notes 與 log 兩條對外通道都被遮蔽。
    """
    leaky = f"auth failed for key={_FAKE_KEY} at https://api.example/v1"
    client, _ = make(exc=_FakeApiError(401, leaky))
    backend = _sdk_backend(cls, key_attr, monkeypatch, client)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(MergeFailure) as e:
            asyncio.run(backend.merge("prompt"))

    assert _FAKE_KEY not in e.value.notes
    assert "***" in e.value.notes
    assert _FAKE_KEY not in caplog.text


def test_sdk後端共用parse_merge_response而非自己複製一份(monkeypatch):
    """acceptance #5：解析走同一份，所以 F28 的 schema 漂移救援對 SDK 後端同樣有效。

    自創頂層鍵、沒有 found——只有共用的那份解析救得回來；複製一份簡化版
    的實作會在這裡紅。
    """
    drifted = json.dumps({"推荐店家": [{"name": "漂移店"}]}, ensure_ascii=False)
    client, _ = _make_claude_client(text=drifted)
    backend = _sdk_backend(ClaudeApiMergeBackend, "anthropic_api_key", monkeypatch, client)

    places = asyncio.run(backend.merge("prompt"))

    assert [p.name for p in places] == ["漂移店"]


def test_兩家key全留空時鏈逐一降級且notes列出兩個ENV名(monkeypatch):
    """acceptance #1 的鏈層行為：零金鑰狀態下不中斷，全敗的 notes 說得出原因。"""
    for _, attr, _, _ in _SDK_CASES:
        monkeypatch.setattr(settings, attr, "")
    backends = [get_backend(n) for n in ("claude-api", "codex-api")]

    with pytest.raises(MergeFailure) as e:
        asyncio.run(merge_with_backends(backends, "prompt", "failover"))

    for _, _, env_name, _ in _SDK_CASES:
        assert env_name in e.value.notes


def test_零金鑰時ollama仍能接手_sdk後端不中斷鏈(monkeypatch):
    """F27 envelope 的共用限制：任何後端缺金鑰都自動降級，不得中斷。"""
    for _, attr, _, _ in _SDK_CASES:
        monkeypatch.setattr(settings, attr, "")
    chain = [get_backend("claude-api"), FakeMergeBackend([PlaceInfo(name="本地救援")], name="ollama")]

    places, note = asyncio.run(merge_with_backends(chain, "prompt", "failover"))

    assert [p.name for p in places] == ["本地救援"]
    assert note == "採用後端：ollama"
