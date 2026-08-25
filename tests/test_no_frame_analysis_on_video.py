"""F22 回歸測試：影片路徑不得再呼叫抽幀分析（visual_analyzer.analyze）。

背景：thread_mixed（Threads 混合媒體串文）是唯一還在對影片抽幀分析、且沒接
agy 影片理解的路徑，2026-08-25 消融實驗證明抽幀描述對合併階段是負貢獻後已
移除。沒有真實 Threads 混合媒體貼文也要能擋住同型迴歸，所以用原始碼結構斷言
而非端到端測試。
"""

import re
from pathlib import Path

HANDLERS_PATH = Path(__file__).resolve().parent.parent / "app" / "bot" / "handlers.py"

# 注意是 `analyze(`，不是 `analyze_images(`——圖片路徑仍可、也應該用 analyze_images。
FRAME_ANALYSIS_PATTERN = re.compile(r"visual_analyzer\.analyze\(")


def find_frame_analysis_calls(source: str) -> list[str]:
    """回傳原始碼中「對影片抽幀分析」的呼叫，找不到則回傳空list。"""
    return FRAME_ANALYSIS_PATTERN.findall(source)


def test_handlers_無影片抽幀分析呼叫():
    source = HANDLERS_PATH.read_text(encoding="utf-8-sig")
    assert find_frame_analysis_calls(source) == []


def test_檢查邏輯抓得到合成違規():
    """反向測試：證明上面的檢查邏輯真的會抓到迴歸，不是永遠綠燈。"""
    fake_source = "video_visual = await self.visual_analyzer.analyze(video_path)\n"
    assert find_frame_analysis_calls(fake_source) != []


def test_檢查邏輯不誤判_analyze_images():
    fake_source = "await self.visual_analyzer.analyze_images(image_paths)\n"
    assert find_frame_analysis_calls(fake_source) == []
