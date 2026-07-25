"""F9 回歸測試：Maps 結果物件的錯誤欄位要與其他 service 同名（error_message）。

背景：2026-07-25 冒煙測試時，呼叫端讀 `error_message` 拿到 None，誤判為「失敗完全沒訊息」，
實際訊息在 `message` 裡。這組測試釘住兩者的一致性。
"""

import sys
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


@pytest.mark.parametrize(
    "result",
    [
        ListsResult(success=False, message="x"),
        SaveResult(success=False, status="failed", message="x"),
        PlaceSearchResult(found=False, error_message="x"),
        TranscriptionResult(success=False, error_message="x"),
    ],
)
def test_所有_service_結果物件都有_error_message(result):
    """跨 service 的一致性：任何結果物件都能用同一個欄位名問「為什麼失敗」。"""
    assert hasattr(result, "error_message")
    assert result.error_message == "x"
