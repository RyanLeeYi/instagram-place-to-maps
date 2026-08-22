"""F9 回歸測試：Maps 結果物件的錯誤欄位要與其他 service 同名（error_message）。

背景：2026-07-25 冒煙測試時，呼叫端讀 `error_message` 拿到 None，誤判為「失敗完全沒訊息」，
實際訊息在 `message` 裡。這組測試釘住兩者的一致性。
"""

import dataclasses
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.google_maps_saver import ListsResult, SaveResult
from app.services.google_places import PlaceSearchResult
from app.services.transcriber import TranscriptionResult


def test_lists_result_失敗時_error_message_帶原因():
    result = ListsResult(success=False, message="無法打開儲存選單")
    assert result.error_message == "無法打開儲存選單"


def test_lists_result_成功時_error_message_為_None():
    result = ListsResult(success=True, lists=["食物名單"], message="找到 1 個清單")
    assert result.error_message is None


def test_save_result_失敗時_error_message_帶原因():
    result = SaveResult(success=False, status="not_logged_in", message="尚未登入 Google 帳戶")
    assert result.error_message == "尚未登入 Google 帳戶"


def test_save_result_成功時_error_message_為_None():
    result = SaveResult(success=True, status="saved", message="已儲存至清單")
    assert result.error_message is None


def test_失敗但沒填訊息時回傳_None_而非空字串():
    assert ListsResult(success=False).error_message is None
    assert SaveResult(success=False, status="failed").error_message is None


def _result_dataclasses():
    """掃出 app/services/ 下所有結果物件（*Result 的 dataclass）。

    手維護的清單會漏——2026-07-25 review 時 8 個結果物件只涵蓋 4 個，
    測試名稱宣稱的不變量其實沒被釘住。改成掃描，新增的結果物件自動進來。
    """
    services_dir = Path(__file__).resolve().parent.parent / "app" / "services"
    found = []
    for path in sorted(services_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(f"app.services.{path.stem}")
        for cls in _result_classes_in(module):
            found.append(pytest.param(cls, id=f"{path.stem}.{cls.__name__}"))
    assert found, "掃不到任何結果物件，掃描邏輯壞了"
    return found


def _result_classes_in(module):
    """這個 module 自己定義了哪些結果物件（*Result 的 dataclass）。"""
    return [
        obj
        for name, obj in vars(module).items()
        if dataclasses.is_dataclass(obj)
        and isinstance(obj, type)
        and obj.__module__ == module.__name__
        and name.endswith("Result")
    ]


def _has_error_message(cls) -> bool:
    """這個結果物件問得出「為什麼失敗」嗎？欄位或 property 都算。"""
    if any(f.name == "error_message" for f in dataclasses.fields(cls)):
        return True
    return isinstance(getattr(cls, "error_message", None), property)


@pytest.mark.parametrize("cls", _result_dataclasses())
def test_所有_service_結果物件都有_error_message(cls):
    """跨 service 的一致性：任何結果物件都能用同一個欄位名問「為什麼失敗」。"""
    assert _has_error_message(cls), (
        f"{cls.__module__}.{cls.__name__} 沒有 error_message；"
        "呼叫端會讀到 None 而誤判為『失敗但沒訊息』"
    )


def test_掃描抓得到所有結果物件而不是只有幾個():
    """釘住掃描本身：漏掉的話上面那組 parametrize 會安靜地少跑。"""
    names = {p.values[0].__name__ for p in _result_dataclasses()}
    assert {
        "DownloadResult",
        "PostDownloadResult",
        "SaveResult",
        "ListsResult",
        "PlaceSearchResult",
        "ExtractionResult",
        "TranscriptionResult",
        "VisualAnalysisResult",
        "ImageAnalysisResult",
    } <= names


def test_新增只用_message_命名的結果物件會被掃到並判為不合格():
    """這條證明上面那組檢查真的會 fail，而不是永遠綠燈。

    模擬「有人在 app/services/ 新增了一個沿用 message 命名的結果物件」：
    掃描要抓得到它（不然 parametrize 會安靜地少跑一項），
    且一致性檢查要判它不合格（不然檢查等於沒作用）。
    """
    fake_module = types.ModuleType("app.services.fake_service")

    @dataclasses.dataclass
    class FakeResult:
        success: bool
        message: str = ""

    FakeResult.__module__ = fake_module.__name__
    fake_module.FakeResult = FakeResult

    assert _result_classes_in(fake_module) == [FakeResult]
    assert _has_error_message(FakeResult) is False


# --- F16：error_message 要是可傳入的真欄位，不是唯讀 property ---


def test_可以像其他_service_一樣用_error_message_建構():
    """照別處寫法傳 error_message 不得炸在失敗分支（最少被跑到的地方）。"""
    save = SaveResult(success=False, status="failed", error_message="打不開儲存選單")
    lists = ListsResult(success=False, error_message="找不到清單容器")

    assert save.error_message == "打不開儲存選單"
    assert lists.error_message == "找不到清單容器"


def test_只傳_message_或只傳_error_message_都問得出同一個原因():
    """兩個入口收斂成同一個值，呼叫端讀哪個都拿得到原因。"""
    via_message = SaveResult(success=False, status="failed", message="x")
    via_error = SaveResult(success=False, status="failed", error_message="x")

    assert via_message.error_message == via_error.error_message == "x"
    assert via_message.message == via_error.message == "x"


@pytest.mark.parametrize(
    "result",
    [
        SaveResult(success=False, status="failed", error_message="壞了"),
        SaveResult(success=False, status="failed", message="壞了"),
        ListsResult(success=False, error_message="壞了"),
        ListsResult(success=False, message="壞了"),
    ],
)
def test_asdict_帶得到失敗原因(result):
    """序列化（log、API 回傳）時失敗原因不能消失——property 版的就會消失。"""
    dumped = dataclasses.asdict(result)
    assert dumped["error_message"] == "壞了"


def test_asdict_成功時_error_message_為_None():
    dumped = dataclasses.asdict(SaveResult(success=True, status="saved", message="已儲存"))
    assert dumped["error_message"] is None
