"""F27.3：三家 CLI 版合併後端 —— agy / claude / codex。

都是已登入的訂閱制互動 CLI 的非互動模式（subprocess），不是 API、不吃
API key。三家都遵守同一個順序：先驗外層信封的成功欄位，成功才把內層
文字交給 `parse_merge_response`（app.services.merge_backends 的共用函式，
含 markdown 圍欄剝除與 schema 漂移救援）；不成功就整份丟棄轉
`MergeFailure`，連內層文字都不看——這是 gemini_video.py 已經踩過的教訓
（agy 讀不到輸入時會編一個像樣的答案，只有外層信封的狀態欄位說實話）。

三家外層信封形狀不同：
- agy：單一 JSON 物件，`status` 不是 `"SUCCESS"` 就整份丟棄（配方見
  gemini_video.py 的 `_run`/`_parse`，這裡原樣照抄）。
- claude：`--output-format json` 回一個物件，`is_error` 為真就丟棄。
- codex：`exec --json` 輸出的是 JSONL 事件串流，不是單一物件；要看整串
  事件裡有沒有終局的 `turn.completed`（成功）或 `turn.failed`（失敗）—
  中途的 `item.completed`（type=error）可能只是警告（例如 model metadata
  找不到、skill 描述被截短），不代表這輪真的失敗，2026-08-26 实测确认。

已知限制：這台機器登入的 ChatGPT 帳號目前呼叫 `codex exec` 時，不論指定
哪個 model 都被伺服器拒絕（`... model is not supported when using Codex
with a ChatGPT account`），所以 CodexMergeBackend 的成功路徑（agent_message
item 的文字欄位）沒能用真的成功回應驗證，是照 codex.exe 二進位內的事件
字串（`item.type == "agent_message"`）與已驗證的失敗分支命名慣例
（`item.type`/`message`）類推寫的，並用多個候選欄位名容錯。帳號一旦能跑
通就照 docs/merge-cli-backends.md 補一次真實成功樣本。
"""

import asyncio
import json
import logging
import shutil
from typing import List, Optional

from app.config import settings
from app.services.merge_backends import MergeFailure, PlaceInfo, parse_merge_response

logger = logging.getLogger(__name__)


async def _run_cli_subprocess(
    backend_name: str, command: str, args: List[str], timeout: float
) -> str:
    """跑一次 CLI，回傳 stdout 文字。

    一律 `asyncio.create_subprocess_exec`，不經 shell。Windows 上 .cmd
    腳本（例如 npm 裝的 codex）CreateProcess 認不得裸檔名，要先用
    `shutil.which` 解析出帶副檔名的完整路徑（2026-08-26 在這台機器實測
    驗證過，直接傳裸名會找不到執行檔）；agy／claude 是真正的 .exe，
    `shutil.which` 一樣找得到，行為不變。

    執行檔找不到、啟動失敗、逾時，都在這裡統一轉成 MergeFailure，三個
    後端的 merge() 不用各自重複這段（acceptance #3）。
    """
    resolved = shutil.which(command)
    if not resolved:
        raise MergeFailure(f"{backend_name}：找不到 CLI 執行檔 {command!r}")

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise MergeFailure(f"{backend_name}：無法啟動 CLI {command!r}：{e}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise MergeFailure(f"{backend_name}：逾時（{timeout}s）")

    if proc.returncode != 0 and not stdout:
        raise MergeFailure(
            f"{backend_name}：CLI 結束碼 {proc.returncode}："
            f"{stderr.decode('utf-8', 'replace')[:300]}"
        )

    return stdout.decode("utf-8", "replace")


def _load_json_envelope(backend_name: str, stdout: str) -> dict:
    """把 CLI 的單一 JSON 信封解析成 dict；不是合法 JSON 就直接 MergeFailure。"""
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        raise MergeFailure(f"{backend_name}：輸出不是 JSON：{stdout[:200]}")


class AgyMergeBackend:
    """走 `agy --output-format json --print <prompt>`（配方同 gemini_video.py）。"""

    name = "agy"

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        stdout = await _run_cli_subprocess(
            self.name,
            settings.agy_command,
            ["--output-format", "json", "--print", prompt],
            settings.merge_cli_timeout,
        )
        envelope = _load_json_envelope(self.name, stdout)
        status = envelope.get("status")
        if status != "SUCCESS":
            # status 是唯一會說實話的欄位——不是 SUCCESS 就連 response 都不看
            raise MergeFailure(
                f"{self.name}：status={status}："
                f"{envelope.get('error') or '(無錯誤訊息)'}"
            )
        return parse_merge_response(envelope.get("response") or "")


class ClaudeMergeBackend:
    """走 `claude -p --output-format json <prompt>`。"""

    name = "claude"

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        stdout = await _run_cli_subprocess(
            self.name,
            settings.claude_command,
            ["-p", "--output-format", "json", prompt],
            settings.merge_cli_timeout,
        )
        envelope = _load_json_envelope(self.name, stdout)
        if envelope.get("is_error"):
            raise MergeFailure(
                f"{self.name}：{envelope.get('subtype') or 'is_error'}："
                f"{envelope.get('result') or '(無錯誤訊息)'}"
            )
        return parse_merge_response(envelope.get("result") or "")


class CodexMergeBackend:
    """走 `codex exec --json --sandbox read-only --skip-git-repo-check <prompt>`。

    輸出是 JSONL；`_extract_success_text` 掃過整串事件找終局訊號。
    """

    name = "codex"

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        stdout = await _run_cli_subprocess(
            self.name,
            settings.codex_command,
            [
                "exec",
                "--json",
                "--sandbox", "read-only",
                "--skip-git-repo-check",
                prompt,
            ],
            settings.merge_cli_timeout,
        )
        return parse_merge_response(self._extract_success_text(stdout))

    def _extract_success_text(self, stdout: str) -> str:
        """掃過 JSONL 事件串流，找終局的 turn.completed／turn.failed。

        中途出現的 item.completed（type=error）可能只是警告（model metadata
        找不到、skill 描述被截短之類），不當成整輪失敗；只有明確的
        turn.failed，或整串跑完都沒等到 turn.completed，才算失敗。
        """
        texts: List[str] = []
        turn_failed: Optional[str] = None
        turn_completed = False

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "turn.completed":
                turn_completed = True
            elif event_type == "turn.failed":
                error = event.get("error")
                turn_failed = (
                    (error.get("message") if isinstance(error, dict) else None)
                    or str(error)
                )
            elif event_type == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = item.get("text") or item.get("content") or item.get("message")
                    if text:
                        texts.append(text)

        if turn_failed is not None:
            raise MergeFailure(f"{self.name}：turn.failed：{turn_failed}")
        if not turn_completed:
            raise MergeFailure(f"{self.name}：輸出裡沒有 turn.completed，判定失敗")
        if not texts:
            raise MergeFailure(f"{self.name}：turn.completed 但找不到 agent_message 內容")
        return "\n".join(texts)
