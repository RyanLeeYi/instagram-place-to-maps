"""F27.4：兩家 SDK 版合併後端 —— claude-api / codex-api。

與 F27.3 的 CLI 版（merge_cli_backends.py）配成一對：同樣的模型，改走官方
Python SDK 與 API key 認證，不碰訂閱憑證。兩者實際的差別只有「怎麼建 client、
怎麼問、答案藏在回應物件的哪一格」，其餘（缺 key、逾時、HTTP 錯誤、內層 JSON
解析）全部收在 `_SdkMergeBackend` 這個共用基底。

Gemini（agy-api）刻意不在這裡：它點名的 antigravity-sdk-python 在 PyPI 上發行名
是 google-antigravity，相依鏈拉進 starlette 1.x，與釘住的 fastapi 0.115（要求
starlette<0.39）硬衝；退一步的 google-genai 也要動 pydantic／httpx 釘版。為了一個
當時沒有金鑰的後端動整條 FastAPI 相依不划算，2026-08-28 決議砍掉，合併階段要用
Gemini 就走 F27.3 的 `agy` CLI（訂閱憑證，不吃 API key）。

四件事是刻意的：

1. **key 留空＝停用，不是壞掉。** merge() 直接丟 `MergeFailure("未設定 <ENV名>")`，
   連 client 都不建。零金鑰狀態下整條線要能跑完（F27 envelope 的共用限制），
   所以「沒設 key」與 CLI 版的「找不到執行檔」是同一種降級，不是啟動期錯誤。
2. **SDK import 延遲到 `_build_client()`。** 放模組頂端會讓「沒裝這個 SDK」
   變成整個 app 啟動失敗；裝沒裝本來就該是降級條件之一。
3. **逾時兩層。** client 自己的 timeout 只管單次請求，擋不住 SDK 內部重試累積
   出來的總時長；外層 `asyncio.wait_for` 才是「這次合併最多花多久」的保證。
4. **對外文字一律過遮蔽。** 例外訊息可能帶上請求細節，`_redact()` 把所有已設定
   的 key 值換成 `***` 再寫進 notes 與 log（acceptance #1；mission-control F53
   的前例是含 token 的 URL 被例外訊息原樣存下再送出）。

JSON 內容解析共用 `merge_backends.parse_merge_response`，與 ollama／CLI 版是
同一份（acceptance #5），不在這裡複製第二份。

"""

import asyncio
import logging
from typing import Any, List, Optional

from app.config import settings
from app.services.merge_backends import MergeFailure, PlaceInfo, parse_merge_response

logger = logging.getLogger(__name__)


# HTTP 狀態碼 → 使用者看得懂的短標籤。401/403 與 429 是 acceptance #4 點名的
# 兩類；5xx 走區間判斷，其餘只標碼數，不假裝知道它是什麼。
_STATUS_LABELS = {
    401: "認證失敗",
    403: "認證失敗",
    429: "速率限制",
}

# 例外訊息可能很長（有些 SDK 會把整個 response body 塞進去），截斷後才寫進
# notes——notes 會出現在 Telegram 回覆裡。
_DETAIL_MAX_CHARS = 300


def _redact(text: str) -> str:
    """把所有已設定的 API key 值換成 `***`（acceptance #1）。

    遮蔽的來源是 `settings.api_key_values`，不是各後端自己記得要遮哪一把——
    某家的例外訊息夾帶另一家的 key 一樣不能外流，而且新增後端時不必回頭改
    這裡。
    """
    for key in settings.api_key_values:
        text = text.replace(key, "***")
    return text


def _describe_error(backend_name: str, exc: Exception) -> str:
    """把 SDK 例外收斂成可讀且不含 key 的一行（acceptance #1、#4）。

    用 `getattr(exc, "status_code", ...)` 鴨子型別判斷，而不是 import 各家的
    錯誤類別——那會讓模組頂端又長回三個 SDK 的硬相依，正是第 2 點要避免的。
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in _STATUS_LABELS:
            label = f"{_STATUS_LABELS[status]}（HTTP {status}）"
        elif 500 <= status < 600:
            label = f"伺服器錯誤（HTTP {status}）"
        else:
            label = f"HTTP {status}"
    else:
        label = type(exc).__name__

    detail = _redact(str(exc)) or "(無錯誤訊息)"
    return f"{backend_name}：{label}：{detail[:_DETAIL_MAX_CHARS]}"


def _join_text_blocks(blocks: Any) -> str:
    """把「一則回應由多個文字區塊組成」的回應攤平成一段文字。

    物件屬性與 dict 兩種形狀都收——假 client 用 dict 寫起來簡單，真 SDK 回的
    是物件，兩邊不該逼測試去模仿 SDK 的私有型別。
    """
    texts: List[str] = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            texts.append(text)
    return "\n".join(texts)


class _SdkMergeBackend:
    """兩家 SDK 後端的共用流程：缺 key → 建 client → 帶逾時呼叫 → 共用解析。

    子類別只需要填 `name`／`env_name` 與三個鉤子。`self._client` 是測試注入
    接縫：先設好就不會走 `_build_client()`，因此單元測試不需要裝任何 SDK，
    也永遠打不到真 API（acceptance #6）。
    """

    name: str = ""
    env_name: str = ""

    def __init__(self):
        self._client: Optional[Any] = None

    # --- 子類別鉤子 ---

    def _api_key(self) -> str:
        raise NotImplementedError

    def _build_client(self, api_key: str, timeout: float) -> Any:
        raise NotImplementedError

    async def _request_text(self, client: Any, prompt: str) -> str:
        raise NotImplementedError

    # --- 共用流程 ---

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        api_key = self._api_key()
        if not api_key:
            # 留空＝停用該後端。鏈照常往下一個降級（acceptance #1）
            raise MergeFailure(f"{self.name}：未設定 {self.env_name}")

        timeout = float(settings.merge_sdk_timeout)

        client = self._client
        if client is None:
            try:
                client = self._build_client(api_key, timeout)
            except ImportError as e:
                raise MergeFailure(
                    f"{self.name}：SDK 未安裝：{_redact(str(e))[:_DETAIL_MAX_CHARS]}"
                )
            except Exception as e:
                raise MergeFailure(_describe_error(self.name, e))

        try:
            text = await asyncio.wait_for(
                self._request_text(client, prompt), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise MergeFailure(f"{self.name}：逾時（{timeout}s）")
        except Exception as e:
            notes = _describe_error(self.name, e)
            logger.warning(f"SDK 後端呼叫失敗 {notes}")
            raise MergeFailure(notes)

        if not text:
            raise MergeFailure(f"{self.name}：回應沒有文字內容")

        return parse_merge_response(text)


class ClaudeApiMergeBackend(_SdkMergeBackend):
    """Claude（anthropic，ANTHROPIC_API_KEY）。"""

    name = "claude-api"
    env_name = "ANTHROPIC_API_KEY"

    def _api_key(self) -> str:
        return settings.anthropic_api_key

    def _build_client(self, api_key: str, timeout: float) -> Any:
        import anthropic

        return anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def _request_text(self, client: Any, prompt: str) -> str:
        message = await client.messages.create(
            model=settings.anthropic_api_model,
            max_tokens=settings.merge_sdk_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return _join_text_blocks(getattr(message, "content", None))


class CodexApiMergeBackend(_SdkMergeBackend):
    """OpenAI（openai，OPENAI_API_KEY）。"""

    name = "codex-api"
    env_name = "OPENAI_API_KEY"

    def _api_key(self) -> str:
        return settings.openai_api_key

    def _build_client(self, api_key: str, timeout: float) -> Any:
        import openai

        return openai.AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def _request_text(self, client: Any, prompt: str) -> str:
        completion = await client.chat.completions.create(
            model=settings.openai_api_model,
            max_completion_tokens=settings.merge_sdk_max_tokens,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise MergeFailure(f"{self.name}：回應沒有 choices")
        return getattr(choices[0].message, "content", None) or ""
