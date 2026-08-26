"""F22 回歸檢查：拿快取材料重跑合併步驟，比對驗收標準的兩個案例。

材料（逐字稿 / agy 候選 / 說明文）由 build_fixtures 一次跑好存成 JSON，
所以這支不碰網路、不跑 whisper、不呼叫 agy——秒級可重複，
也不會受 agy 約 50% 失敗率影響而變成擲骰子。

驗收要求連續三次都過，所以預設跑三次；qwen3:8b 有隨機性，跑一次不算數。
用法：python scripts/f22_regression.py [次數]
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.gemini_video import GeminiPlace
from app.services.place_extractor import PlaceExtractor

FIXTURES = ROOT / "temp_videos/f22_fixtures"

# 真實答案由 Ryan 2026-08-25 看過影片逐項確認，不是模型推測。
# 蔡元益紅茶在 1:35 有實際購買飲用——原本的驗收標準把它當路過招牌是錯的。
# 巫婆水餃是業配的冷凍水餃品牌，Google Maps 上無實體店（F28 起源案例），
# 六家仍要全部留在 places。巫婆水餃的 is_physical 不做硬斷言——
# 2026-08-26 #11 重簽核：qwen3:8b 兩版 prompt 共 9 次推論全判 true（能力上限），
# 端到端守門改由 A 段兜底（其 LOW 信心有 smoke [5] 真實 API 證據，
# 「LOW 不寫 Maps」由 tests/test_place_gating.py 釘住）。其餘五家仍必須
# 判 true，守住「分類規則不得誤擋實體店」這一半。
CASES = {
    "(a) DT2w2PVgXo3 北投市場": {
        "fixture": "DT2w2PVgXo3_materials.json",
        "keep": [
            "海鮮拉麵清燉豬腳",
            "阿宗蚵仔煎",
            "大溪家鄉臭豆腐",
            "高媽媽傳統米食",
            "蔡元益紅茶",
            "巫婆水餃",
        ],
        "drop": ["北投中繼市場", "巧涼", "九份紅糟", "brunii", "KYMCO"],
        "is_physical": {
            "海鮮拉麵清燉豬腳": True,
            "阿宗蚵仔煎": True,
            "大溪家鄉臭豆腐": True,
            "高媽媽傳統米食": True,
            "蔡元益紅茶": True,
        },
    },
    # 店名只出現在招牌上，逐字稿把地名聽成「位遠程序公有市場」——
    # 這條案例存在的理由就是證明 agy 補得到本地聽不出來的招牌字。
    "(b) DUzYwaAElyG 酒場清志郎": {
        "fixture": "DUzYwaAElyG_materials.json",
        "keep": ["清志郎"],
        "drop": ["鹽埕區公有市場"],
    },
}

# 行政區與市場區域名不算店家（驗收標準明文）。單獨列一組並用完全相等比對，
# 因為它們是子字串，用 in 會誤殺真的店名。
AREA_NAMES = ["北投市場", "北投市場周邊", "鹽埕區公有市場"]


def _norm(s: str) -> str:
    """比對前抹掉不影響身份的雜訊：空白與「店」字尾。

    「巫婆水餃」與「巫婆水餃店」是同一家，不該因為多一個字判失敗。
    """
    return (s or "").replace(" ", "").rstrip("店")


def score(names, spec):
    normed = [_norm(n) for n in names]
    missing = [k for k in spec["keep"] if not any(_norm(k) in n for n in normed)]
    leaked = [d for d in spec["drop"] if any(_norm(d) in n for n in normed)]
    areas = [a for a in AREA_NAMES if any(_norm(a) == n for n in normed)]
    return missing, leaked, areas


def check_is_physical(places, spec):
    """比對 is_physical 是否符合 spec 期望（F28 acceptance #11）。

    只有 spec 定義了 "is_physical" 期望值的案例才檢查；沒定義就不檢查（案例 (b)
    不需要，acceptance #11 只要求案例 (a)）。回傳 (店名, 期望值, 實際值) 的清單，
    空清單代表全對。
    """
    expected = spec.get("is_physical")
    if not expected:
        return []
    wrong = []
    for name_key, expect in expected.items():
        for p in places:
            if _norm(name_key) in _norm(p.name):
                if p.is_physical != expect:
                    wrong.append((p.name, expect, p.is_physical))
                break
    return wrong


async def run_case(extractor, label, spec, runs):
    path = FIXTURES / spec["fixture"]
    if not path.exists():
        print(f"{label}: 缺少材料檔 {path.name}，跳過（不算通過）")
        return False
    m = json.loads(path.read_text(encoding="utf-8"))
    gemini = [GeminiPlace(**p) for p in m["gemini_places"]] or None

    print(f"\n{label}  (agy 材料: {'有' if gemini else '無'})")
    passed = 0
    for i in range(1, runs + 1):
        result = await extractor.extract(
            transcript=m["transcript"],
            visual_description="",  # 影片路徑已不用抽幀描述
            ig_account=m.get("ig_account"),
            caption=m["caption"],
            gemini_places=gemini,
        )
        places = (result.places or []) if result.found else []
        names = [p.name for p in places]
        missing, leaked, areas = score(names, spec)
        wrong_physical = check_is_physical(places, spec)
        ok = not (missing or leaked or areas or wrong_physical)
        passed += ok
        print(f"  [{i}/{runs}] {'PASS' if ok else 'FAIL'}  {names}")
        for tag, items in (("漏掉", missing), ("該濾沒濾", leaked), ("區域誤判成店家", areas)):
            if items:
                print(f"          {tag}: {items}")
        if wrong_physical:
            print(f"          is_physical判斷錯誤(店名,期望,實際): {wrong_physical}")
    print(f"  小計 {passed}/{runs}")
    return passed == runs


async def main(runs):
    extractor = PlaceExtractor()
    results = {}
    for label, spec in CASES.items():
        results[label] = await run_case(extractor, label, spec, runs)

    print("\n" + "=" * 60)
    for label, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    all_ok = all(results.values())
    print(f"\nF22 回歸: {'全部通過' if all_ok else '未通過'}（驗收要求兩案例各 {runs}/{runs}）")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)))
