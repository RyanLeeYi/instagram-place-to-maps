r"""F22 回歸測試：agy 的 status 是唯一會說實話的欄位。

背景：2026-08-23 實測 agy 讀不到檔案時**不會中止**，而是回一份 schema 合法、
內容捏造的答案——回的是另一支影片的店名，而那些名字就寫在 workspace 的
docs/spike-gemini-video.md 裡。`response` 看起來完全正常，只有
`"status": "ERROR"` 會說實話。只 parse response 就會靜默入庫錯誤店家。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.gemini_video import GeminiVideoExtractor


def _envelope(status: str, response: str, error: str = None) -> str:
    """照 agy --output-format json 的實際外層形狀組一份輸出。"""
    body = {"conversation_id": "x", "status": status, "response": response}
    if error:
        body["error"] = error
    return json.dumps(body, ensure_ascii=False)


def _fenced(payload: dict, prose: str = "") -> str:
    """agy 習慣把 JSON 包在 ```json 圍欄裡，前面還接一段敘述。"""
    return prose + "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"


# 實際踩到的那一份：status ERROR，但 response 是別支影片的店名
FABRICATED = _envelope(
    "ERROR",
    _fenced({"places": [{"name": "酒場清志郎", "is_recommended": True}]}),
    error="permission check failed for read_file",
)

GOOD = _envelope(
    "SUCCESS",
    _fenced(
        {
            "places": [
                {"name": "美軍炸雞", "is_recommended": True, "reason": "全片主角"},
                {"name": "台灣運彩", "is_recommended": False, "reason": "路過招牌"},
            ]
        },
        prose="我看完了這支影片。\n",
    ),
)


def _extractor():
    """只測 _parse，不碰 settings 與 subprocess。"""
    return GeminiVideoExtractor.__new__(GeminiVideoExtractor)


def test_status_不是_SUCCESS_時整份丟掉_連_response_都不看():
    """核心回歸：捏造的答案再像樣也不准進 pipeline。"""
    result = _extractor()._parse(FABRICATED)

    assert result.success is False
    assert result.places == []
    assert "ERROR" in result.error_message


def test_status_成功時取出店家與推薦旗標():
    result = _extractor()._parse(GOOD)

    assert result.success is True
    assert [p.name for p in result.places] == ["美軍炸雞", "台灣運彩"]
    assert result.places[0].is_recommended is True
    assert result.places[1].is_recommended is False


def test_輸出不是_JSON_時算失敗而不是丟例外():
    result = _extractor()._parse("agy: command not found")

    assert result.success is False
    assert "不是 JSON" in result.error_message


def test_成功但回應裡沒有_JSON_物件時算失敗():
    result = _extractor()._parse(_envelope("SUCCESS", "我找不到任何店家"))

    assert result.success is False
    assert result.places == []


def test_沒有店家的空清單仍算成功():
    result = _extractor()._parse(_envelope("SUCCESS", _fenced({"places": []})))

    assert result.success is True
    assert result.places == []


def test_略過沒有名字的項目():
    payload = _envelope(
        "SUCCESS",
        _fenced({"places": [{"name": ""}, {"name": "有名字"}, {"reason": "沒有 name 欄位"}]}),
    )

    result = _extractor()._parse(payload)

    assert [p.name for p in result.places] == ["有名字"]
