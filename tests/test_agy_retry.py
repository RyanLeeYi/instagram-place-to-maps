r"""F26：GeminiVideoExtractor 的 agy 重試機制。

全部用注入的假 subprocess（monkeypatch `asyncio.create_subprocess_exec`），
不連外、不呼叫真的 agy，跑起來是秒級。
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import gemini_video as gv
from app.services.gemini_video import GeminiVideoExtractor


def _envelope(status: str, response: str = "", error: str = None) -> bytes:
    body = {"status": status, "response": response}
    if error:
        body["error"] = error
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


GOOD_STDOUT = _envelope(
    "SUCCESS", json.dumps({"places": [{"name": "測試店家", "is_recommended": True}]}, ensure_ascii=False)
)
CANCELED_STDOUT = _envelope("CANCELED")
PERMISSION_STDOUT = _envelope("ERROR", error="permission check failed for read_file")


class FakeProc:
    """假 subprocess：communicate() 可回傳固定內容、延遲，或直接丟例外。"""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", exc: Exception = None, delay: float = 0.0):
        self._stdout = stdout
        self._stderr = stderr
        self._exc = exc
        self._delay = delay

    async def communicate(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._stdout, self._stderr

    def kill(self):
        pass


def _extractor(timeout: float = 5) -> GeminiVideoExtractor:
    """只測重試迴圈，不碰 __init__ 的 lock 與 temp_dir。"""
    ext = GeminiVideoExtractor.__new__(GeminiVideoExtractor)
    ext.timeout = timeout
    return ext


def _patch_subprocess(monkeypatch, procs):
    """依序把 procs 清單發給每一次 create_subprocess_exec 呼叫。"""
    it = iter(procs)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return next(it)

    monkeypatch.setattr(gv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


def _attempts(error_message: str) -> int:
    return int(re.search(r"共嘗試 (\d+) 次", error_message).group(1))


def test_暫時性失敗後成功(monkeypatch):
    monkeypatch.setattr(gv.settings, "agy_max_attempts", 3)
    monkeypatch.setattr(gv.settings, "agy_total_timeout", 60)
    _patch_subprocess(monkeypatch, [FakeProc(stdout=CANCELED_STDOUT), FakeProc(stdout=GOOD_STDOUT)])

    result = asyncio.run(_extractor()._run_with_retry(Path("fake.mp4")))

    assert result.success is True
    assert [p.name for p in result.places] == ["測試店家"]


def test_連續失敗到上限(monkeypatch):
    monkeypatch.setattr(gv.settings, "agy_max_attempts", 3)
    monkeypatch.setattr(gv.settings, "agy_total_timeout", 60)
    _patch_subprocess(monkeypatch, [FakeProc(stdout=CANCELED_STDOUT) for _ in range(3)])

    result = asyncio.run(_extractor()._run_with_retry(Path("fake.mp4")))

    assert result.success is False
    assert _attempts(result.error_message) == 3
    assert "CANCELED" in result.error_message


def test_確定性失敗只呼叫一次(monkeypatch):
    monkeypatch.setattr(gv.settings, "agy_max_attempts", 3)
    monkeypatch.setattr(gv.settings, "agy_total_timeout", 60)
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(1)
        return FakeProc(stdout=PERMISSION_STDOUT)

    monkeypatch.setattr(gv.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(_extractor()._run_with_retry(Path("fake.mp4")))

    assert result.success is False
    assert len(calls) == 1
    assert _attempts(result.error_message) == 1
    assert "permission" in result.error_message.lower()


def test_反向_確定性失敗誤判成暫時性時這條斷言會抓到迴歸():
    """證明「確定性失敗不重試」的判準真的擋得住——不是只測正常路徑就過。

    如果有人把 `_is_transient_failure` 改壞（例如永遠回傳 True），
    這條會先紅，而不是等到 `test_確定性失敗只呼叫一次` 因為多呼叫一次才發現。
    """
    assert GeminiVideoExtractor._is_transient_failure(
        "agy status=ERROR: permission check failed for read_file"
    ) is False
    # 對照組：同樣是 status=ERROR，但不是權限問題，就該是暫時性
    assert GeminiVideoExtractor._is_transient_failure("agy status=ERROR: internal error") is True


def test_總時長上限生效(monkeypatch):
    monkeypatch.setattr(gv.settings, "agy_max_attempts", 10)
    monkeypatch.setattr(gv.settings, "agy_total_timeout", 0.15)
    _patch_subprocess(monkeypatch, [FakeProc(stdout=CANCELED_STDOUT, delay=0.1) for _ in range(10)])

    result = asyncio.run(_extractor(timeout=5)._run_with_retry(Path("fake.mp4")))

    assert result.success is False
    # 0.15s 總時長上限、每次嘗試耗 0.1s：跑不到 max_attempts=10 就該被攔下來
    assert _attempts(result.error_message) < 10
