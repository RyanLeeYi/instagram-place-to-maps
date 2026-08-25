r"""agy 可靠度人工量測（F26）：需要網路與真的 agy，不列入自動測試。

對 temp_videos/f22_fixtures/ 的兩支影片各跑 N 次 `GeminiVideoExtractor.extract()`
（含 F26 內建重試，`agy_max_attempts` 讀 settings），記錄「這次呼叫——內部可能
重試了好幾輪——最後有沒有成功」的比率，也就是使用者實際感受到的可靠度，
不是單次 agy subprocess 呼叫的成功率。

用法：.\.venv\Scripts\python.exe scripts\agy_reliability.py [N]
結果印在終端機，同時覆寫 docs\agy-reliability.md。
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.services.gemini_video import GeminiVideoExtractor

FIXTURES = ROOT / "temp_videos" / "f22_fixtures"
VIDEOS = {
    "(a) DT2w2PVgXo3 北投市場": FIXTURES / "DT2w2PVgXo3_video.mp4",
    "(b) DUzYwaAElyG 酒場清志郎": FIXTURES / "DUzYwaAElyG_video.mp4",
}

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


async def run(n: int) -> dict:
    extractor = GeminiVideoExtractor(timeout=settings.gemini_video_timeout)
    results = {}
    for label, path in VIDEOS.items():
        if not path.exists():
            print(f"{label}: 缺少影片檔 {path.name}，跳過")
            continue
        print(f"\n{label}")
        successes = 0
        for i in range(1, n + 1):
            t0 = time.monotonic()
            result = await extractor.extract(path)
            elapsed = time.monotonic() - t0
            successes += result.success
            detail = (
                [p.name for p in result.places] if result.success else result.error_message
            )
            print(f"  [{i}/{n}] {'PASS' if result.success else 'FAIL'} ({elapsed:.1f}s) {detail}")
        results[label] = (successes, n)
        print(f"  小計（至少一次成功）: {successes}/{n}")
    return results


def write_report(results: dict, n: int) -> Path:
    lines = [
        "# agy 可靠度量測（F26 重試機制上線後）",
        "",
        f"每支影片跑 {n} 次 `GeminiVideoExtractor.extract()`"
        f"（內建重試，`agy_max_attempts={settings.agy_max_attempts}`、"
        f"`agy_total_timeout={settings.agy_total_timeout}`），"
        "記錄整次呼叫（內部可能重試了好幾輪）最後有沒有成功的比率。",
        "",
    ]
    for label, (successes, total) in results.items():
        rate = successes / total * 100 if total else 0
        lines.append(f"- {label}: {successes}/{total}（{rate:.0f}%）")
    lines.append("")
    report = "\n".join(lines)

    out = ROOT / "docs" / "agy-reliability.md"
    out.write_text(report, encoding="utf-8")
    return out


async def main(n: int) -> int:
    results = await run(n)
    if not results:
        print("沒有任何影片可測，未寫入報告。")
        return 1
    out = write_report(results, n)
    print(f"\n結果已寫入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)))
