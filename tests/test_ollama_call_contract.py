"""回歸測試：呼叫 ollama 時只能傳「釘住的那個版本真的收得下」的參數。

背景：2026-08-23 冒煙測試抓到 `Client.chat() got an unexpected keyword argument 'think'`。
`think=True` 是 ollama 0.5+ 才有的參數，requirements.txt 釘的是 0.3.3。
呼叫在 try/except 裡，所以整個擷取階段只是靜默回 found=False——
F4 早就標成 passing，實際上壞掉，冒煙測試以外沒有任何東西會紅。

這組測試用 AST 掃出所有 ollama.chat() 呼叫點的關鍵字，
比對「當前安裝的 ollama 客戶端」的簽章。程式碼與相依版本一漂移就紅。
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ollama

SERVICES_DIR = Path(__file__).resolve().parent.parent / "app" / "services"


def _accepted_kwargs() -> set:
    """當前安裝的 ollama.chat 收得下哪些關鍵字。"""
    params = inspect.signature(ollama.chat).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        pytest.skip("這個 ollama 版本的 chat() 收 **kwargs，簽章擋不住錯字")
    return {name for name in params if name != "self"}


def _ollama_chat_calls():
    """掃出 app/services/ 下所有 `ollama.chat(...)` 呼叫及其關鍵字。"""
    calls = []
    for path in sorted(SERVICES_DIR.glob("*.py")):
        # utf-8-sig：這個 repo 有幾支檔案帶 BOM，用 utf-8 讀會 SyntaxError
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "chat"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "ollama"):
                continue
            names = {kw.arg for kw in node.keywords if kw.arg is not None}
            calls.append(
                pytest.param(names, id=f"{path.name}:{node.lineno}")
            )
    assert calls, "掃不到任何 ollama.chat 呼叫，掃描邏輯壞了"
    return calls


@pytest.mark.parametrize("kwargs_used", _ollama_chat_calls())
def test_呼叫_ollama_只用安裝版本支援的參數(kwargs_used):
    unsupported = kwargs_used - _accepted_kwargs()
    assert not unsupported, (
        f"傳了 {sorted(unsupported)} 給 ollama.chat()，但安裝的版本不收——"
        "呼叫會 TypeError，而它被 try/except 吃掉後只會變成靜默的 found=False"
    )


def test_掃描抓得到擷取與視覺兩個呼叫點():
    """釘住掃描本身：漏掉的話上面那組 parametrize 會安靜地少跑。"""
    files = {p.id.split(":")[0] for p in _ollama_chat_calls()}
    assert {"place_extractor.py", "visual_analyzer.py"} <= files


def test_未支援的參數真的會被判為不合格():
    """證明上面的檢查會 fail，而不是永遠綠燈。"""
    assert "這個參數不可能存在" not in _accepted_kwargs()
