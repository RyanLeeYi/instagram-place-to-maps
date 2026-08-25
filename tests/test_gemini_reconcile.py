"""agy 的判斷收斂 8b 輸出的規則（F22）。

這裡測的是 _reconcile 這個純函式，不呼叫 ollama 也不呼叫 agy——
端到端那層由 scripts/f22_regression.py 負責，但它需要模型跑起來。
兩層都要有：這層擋邏輯迴歸，那層擋提示詞迴歸。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.gemini_video import GeminiPlace
from app.services.place_extractor import PlaceExtractor, PlaceInfo


def _ex():
    return PlaceExtractor()


def test_agy否決的候選會被剔除():
    """8b 不能推翻 agy 的『只是畫面帶到』判斷。

    實測 3/3 都發生：agy 標北投中繼市場為非店家，8b 照樣塞進 places。
    """
    out = _ex()._reconcile(
        [PlaceInfo(name="阿宗蚵仔煎"), PlaceInfo(name="北投中繼市場")],
        [
            GeminiPlace(name="阿宗蚵仔煎", is_recommended=True),
            GeminiPlace(name="北投中繼市場", is_recommended=False),
        ],
    )
    assert [p.name for p in out] == ["阿宗蚵仔煎"]


def test_agy推薦但8b漏掉的會被補回():
    """whisper 把店名聽爛時 8b 會整家漏掉，agy 讀招牌讀得到。"""
    out = _ex()._reconcile(
        [PlaceInfo(name="阿宗蚵仔煎")],
        [
            GeminiPlace(name="阿宗蚵仔煎", is_recommended=True),
            GeminiPlace(name="海鮮拉麵清燉豬腳", is_recommended=True, reason="主持人入座用餐"),
        ],
    )
    names = [p.name for p in out]
    assert "海鮮拉麵清燉豬腳" in names
    補回的 = next(p for p in out if p.name == "海鮮拉麵清燉豬腳")
    assert 補回的.search_keywords == ["海鮮拉麵清燉豬腳"]
    assert 補回的.recommendation == "主持人入座用餐"


def test_同一家店多一個店字不會補成兩筆():
    out = _ex()._reconcile(
        [PlaceInfo(name="巫婆水餃店")],
        [GeminiPlace(name="巫婆水餃", is_recommended=True)],
    )
    assert [p.name for p in out] == ["巫婆水餃店"]


def test_區域名一律不算店家():
    out = _ex()._reconcile(
        [
            PlaceInfo(name="北投市場"),
            PlaceInfo(name="士林夜市"),
            PlaceInfo(name="北投市場周邊"),
            PlaceInfo(name="阿宗蚵仔煎"),
        ],
        None,
    )
    assert [p.name for p in out] == ["阿宗蚵仔煎"]


def test_agy推薦的如果是區域名也不補進來():
    """agy 偶爾會把市場標成主角，那不該變成地圖清單裡的一筆。"""
    out = _ex()._reconcile(
        [PlaceInfo(name="阿宗蚵仔煎")],
        [GeminiPlace(name="鹽埕區公有市場", is_recommended=True)],
    )
    assert [p.name for p in out] == ["阿宗蚵仔煎"]


def test_agy失敗時原樣通過只擋區域名():
    """降級路徑不要求精度，但也不能中斷或吐出區域名。"""
    out = _ex()._reconcile(
        [PlaceInfo(name="巫婆水餃店"), PlaceInfo(name="北投市場")],
        None,
    )
    assert [p.name for p in out] == ["巫婆水餃店"]
