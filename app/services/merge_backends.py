"""合併階段的模型後端（F27）。

PlaceExtractor 組好提示詞之後，交給這裡依 settings.merge_backends 建的後端鏈
跑推論，換回結構化地點清單。不同後端吃同一份提示詞才比得出差異，所以提示詞
組裝留在 PlaceExtractor（EXTRACTION_PROMPT / format_gemini_candidates）；
後端只管「怎麼問模型、怎麼把它的答案解析成 PlaceInfo」。

F27.1 搬進來唯一實作 ollama，單一後端、行為不變。F27.2（這個 slice）把它
擴充成「一條後端鏈 + 一種執行模式」：failover（依鏈序試到第一個成功為止）與
vote（鏈上後端同時跑、多數決）。鏈上唯一合法名稱仍是 ollama——這個 slice
不新增任何後端實作，多後端行為一律用假後端測；agy / claude / codex 等真正
的第二個後端是 F27.3／F27.4 的事。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, fields as dc_fields
from typing import Dict, List, NamedTuple, Optional, Protocol, Tuple

import ollama

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class PlaceInfo:
    """擷取的單一地點資訊（餐廳、景點等）"""

    confidence: str = "low"  # high, medium, low

    # 店家資訊
    name: Optional[str] = None
    name_en: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None

    # 分類資訊
    place_type: List[str] = field(default_factory=list)  # 餐廳、咖啡廳、景點等
    highlights: List[str] = field(default_factory=list)  # 亮點：推薦餐點或特色
    price_range: Optional[str] = None

    # 其他
    recommendation: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    search_keywords: List[str] = field(default_factory=list)

    # F28：源頭分類（E）與行政區查準（D）。預設 is_physical=True 是刻意的
    # fail-open——降級路徑、_reconcile 從 agy 候選補進來的地點、任何舊呼叫端
    # 沒有明確給值的情況，行為都要與這個欄位加入前一致（F28 acceptance #1）。
    is_physical: bool = True
    district: Optional[str] = None


def norm_place_name(name: Optional[str]) -> str:
    """比對用的正規化：抹掉空白與「店」字尾。

    「巫婆水餃」與「巫婆水餃店」是同一家，不該因為多一個字被當成兩筆。

    F27.1 這條原本是 PlaceExtractor._norm_name（_reconcile 用它去重）；
    F27.2 vote 模式的記票鍵也要用同一套正規化，所以搬成這裡的模組層級函式，
    PlaceExtractor._norm_name 保留為轉呼叫的 staticmethod，_reconcile 的內容
    不用改一行。
    """
    return (name or "").replace(" ", "").strip().rstrip("店")


class MergeFailure(Exception):
    """後端沒能產出地點清單，`notes` 是要讓使用者看到的原因。

    存在的理由是搬家時差點弄丟的東西：舊的 `_parse_response` 在找不到 JSON、
    JSON 修不好、或模型自己回 found=false 時，會把原因寫進
    `ExtractionResult.notes` 並顯示給使用者（handlers 的「備註」那行）。
    後端契約回傳 `List[PlaceInfo]`，沒有欄位承載這段文字，一開始就直接回空
    清單——使用者只會看到一句沒有資訊量的通用文案，正是這個專案已經修過
    兩次的病（F24 的 cookies、F25 的短連結）。

    用例外而不是擴充回傳型別，是因為 acceptance 要求輸出就是 `List[PlaceInfo]`。
    `PlaceExtractor` 專門接住它、補回 notes，然後**照舊繼續跑 `_reconcile`**——
    本地這步爛掉時 agy 候選還救得回來，那是舊版就有的行為，不能因為搬家弄丟。
    """

    def __init__(self, notes: Optional[str]):
        super().__init__(notes or "後端沒有回傳地點")
        self.notes = notes


class MergeBackend(Protocol):
    """輸入組好的提示詞，輸出解析後的地點清單。

    解析失敗（找不到 JSON、JSON 壞掉、模型自己說 found=false）丟 `MergeFailure`
    並帶上原因。在 F27.2 的鏈式執行下，這與任何其他例外、或回傳空清單一樣，
    都由 `merge_with_backends()` 接住當作「這個後端失敗」處理，往鏈上下一個
    後端試（failover）或不計入這次投票（vote）；只有整條鏈都失敗，才會由
    `merge_with_backends()` 重新丟出 `MergeFailure`，PlaceExtractor 接住之後
    仍會跑 _reconcile，用 agy 候選補救，不會因為本地模型這一步輸出爛掉就
    整段中斷。

    `name` 是這個後端在鏈上的識別名稱，供 failover/vote 的 log 與
    ExtractionResult.backend_note 使用。
    """

    name: str

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        ...


class UnsupportedMergeBackendError(ValueError):
    """settings.merge_backends 鏈裡有不支援的名稱，或鏈本身是空的。"""


class UnsupportedMergeModeError(ValueError):
    """settings.merge_mode 給了 failover / vote 以外的值。"""


def get_backend(name: str) -> MergeBackend:
    """依名稱建立單一後端。目前唯一合法值是 "ollama"；

    給其他值在這裡就明確報錯，不靜默 fallback 回 ollama。
    """
    if name == "ollama":
        return OllamaMergeBackend()
    raise UnsupportedMergeBackendError(
        f"不支援的合併後端 {name!r}；目前唯一支援 'ollama'"
    )


def get_backend_chain(chain: str) -> List[MergeBackend]:
    """依 MERGE_BACKENDS（逗號分隔的名稱鏈）建立後端鏈。

    鏈為空、或含不支援的名稱，都在這裡（PlaceExtractor 建構時呼叫）明確
    報錯，不靜默 fallback（F27.2 acceptance #2）。
    """
    names = [n.strip() for n in chain.split(",") if n.strip()]
    if not names:
        raise UnsupportedMergeBackendError(
            f"合併後端鏈不可為空：MERGE_BACKENDS={chain!r}"
        )
    return [get_backend(name) for name in names]


class OllamaMergeBackend:
    """現有的 ollama 呼叫，原樣搬過來（F27.1：純粹搬家，行為不變）。"""

    name = "ollama"

    def __init__(self):
        self.model = settings.ollama_model

    async def merge(self, prompt: str) -> List[PlaceInfo]:
        # 只傳 requirements.txt 釘住的 ollama 版本支援的參數。
        # 曾經傳過 think=True，但那是 0.5+ 才有的參數，對釘住的 0.3.3 會
        # TypeError，整個擷取階段靜默退化成 found=False（2026-08-23 冒煙測試抓到）。
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: ollama.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                # format="json" 是 server 端的文法約束，堵住「整段回自然語言分析、
                # 連 JSON 都不給」的失敗模式（F28 降級路徑，prompt 規則五版都壓不住）。
                # 釘住的 0.3.3 簽章是 Literal['', 'json']，不支援 schema dict。
                format="json",
                options={"temperature": 0.3}
            )
        )

        # 新版 ollama 套件回傳物件而非字典
        msg = response["message"]
        result_text = msg.content if hasattr(msg, 'content') else msg.get("content", "")

        # 記錄思考過程（如果有）
        if hasattr(msg, 'thinking') and msg.thinking:
            logger.info(f"[思考過程] {msg.thinking[:200]}...")

        logger.debug(f"LLM 回應: {result_text}")

        return self._parse(result_text)

    def _parse(self, response_text: str) -> List[PlaceInfo]:
        """解析 LLM 回應（搬自 PlaceExtractor._parse_response，行為不變）。"""
        try:
            # 預處理：移除可能的 markdown 程式碼區塊標記
            cleaned_text = response_text
            if "```json" in cleaned_text:
                cleaned_text = re.sub(r'```json\s*', '', cleaned_text)
                cleaned_text = re.sub(r'```\s*$', '', cleaned_text)
            elif "```" in cleaned_text:
                cleaned_text = re.sub(r'```\s*', '', cleaned_text)

            # 嘗試找出 JSON 區塊（匹配最外層的大括號）
            json_match = re.search(r'\{[\s\S]*\}', cleaned_text)
            if not json_match:
                logger.warning("回應中找不到 JSON")
                raise MergeFailure("無法解析回應")

            json_str = json_match.group()

            # 嘗試修復常見的 JSON 格式問題
            # 1. 移除尾隨逗號
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            # 2. 修復可能的單引號問題
            # 3. 移除註解（LLM 有時會加註解）
            json_str = re.sub(r'//.*?(?=\n|$)', '', json_str)

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as first_error:
                # 二次嘗試：更激進的清理
                logger.warning(f"第一次 JSON 解析失敗，嘗試修復: {first_error}")

                # 嘗試只提取有效的 JSON 結構
                # 找到 "found" 開始的部分
                found_match = re.search(r'\{\s*"found"[\s\S]*', json_str)
                if found_match:
                    json_str = found_match.group()
                    # 確保閉合
                    open_braces = json_str.count('{')
                    close_braces = json_str.count('}')
                    if open_braces > close_braces:
                        json_str += '}' * (open_braces - close_braces)

                    try:
                        data = json.loads(json_str)
                    except json.JSONDecodeError as second_error:
                        logger.error(f"JSON 解析最終失敗: {second_error}")
                        logger.debug(f"問題 JSON: {json_str[:500]}...")
                        raise MergeFailure(f"JSON 解析失敗: {second_error}")
                else:
                    logger.error(f"JSON 解析失敗，無法修復: {first_error}")
                    raise MergeFailure(f"JSON 解析失敗: {first_error}")

            # schema 漂移救援（F28）：format="json" 保證合法 JSON，但 8b 在降級
            # 路徑（無 agy 候選）仍會自創頂層鍵（實測三種：「推荐店家」、
            # 「景点/地点」、city+additional_info，且都沒有 found 鍵）。只要頂層
            # 有「含 name 的 dict 陣列」就當 places 收下——名字是唯一不可替代的
            # 欄位，其餘欄位缺了走 PlaceInfo 預設值（is_physical 預設 True 即
            # fail-open，交給 A 段守門）。
            if not data.get("places") and "place" not in data:
                for value in data.values():
                    if (
                        isinstance(value, list)
                        and value
                        and all(isinstance(x, dict) for x in value)
                        and any(x.get("name") for x in value)
                    ):
                        logger.warning(
                            f"回應 schema 漂移，從自創鍵救回 {len(value)} 筆地點"
                        )
                        data = {"found": True, "places": value, "notes": data.get("notes")}
                        break

            if not data.get("found", False):
                # 模型自己說沒找到——它附的理由是有資訊的，要送到使用者眼前
                raise MergeFailure(data.get("notes"))

            places_data = data.get("places", [])

            # 向後相容：如果是舊格式（單一 place 物件）
            if not places_data and "place" in data:
                places_data = [data["place"]]

            places = []
            for place_data in places_data:
                place = PlaceInfo(
                    confidence=place_data.get("confidence", "low"),
                    name=place_data.get("name"),
                    name_en=place_data.get("name_en"),
                    city=place_data.get("city"),
                    country=place_data.get("country"),
                    address=place_data.get("address"),
                    place_type=place_data.get("place_type", []),
                    highlights=place_data.get("highlights", []),
                    price_range=place_data.get("price_range"),
                    recommendation=place_data.get("recommendation"),
                    tags=place_data.get("tags", []),
                    search_keywords=place_data.get("search_keywords", []),
                    is_physical=place_data.get("is_physical", True),
                    district=place_data.get("district"),
                )
                places.append(place)

            logger.info(f"成功擷取 {len(places)} 個地點")

            return places

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失敗: {e}")
            raise MergeFailure(f"JSON 解析失敗: {e}")


# --- 鏈的執行模式：failover / vote（F27.2） -------------------------------


class _BackendCallResult(NamedTuple):
    """單一後端呼叫的結果，把例外／MergeFailure／空清單都收斂成同一種形狀，
    讓 failover／vote 不用各自處理三種失敗來源。"""

    name: str
    places: Optional[List[PlaceInfo]]  # None 表示失敗；成功時保證非空
    failure_reason: Optional[str]


async def _call_backend(backend: MergeBackend, prompt: str) -> _BackendCallResult:
    """呼叫單一後端，把失敗（丟例外／MergeFailure／回傳空清單）都轉成
    `_BackendCallResult(places=None, failure_reason=...)`，不往外拋——
    鏈上一個後端失敗不該讓整次合併中斷（acceptance #8）。
    """
    try:
        places = await backend.merge(prompt)
    except MergeFailure as e:
        return _BackendCallResult(backend.name, None, e.notes or str(e))
    except Exception as e:
        return _BackendCallResult(backend.name, None, str(e))
    if not places:
        # 後端解析成功但一家都沒抓到，鏈上下一個仍值得問（acceptance #8）
        return _BackendCallResult(backend.name, None, "回傳空清單")
    return _BackendCallResult(backend.name, places, None)


def _failure_notes(outcomes: List[_BackendCallResult]) -> str:
    """全部後端都失敗時的 notes：逐一列出每個後端的名稱與失敗原因，
    不得只留最後一個、也不得退化成通用文案（acceptance #9）。"""
    return "；".join(f"{o.name}：{o.failure_reason}" for o in outcomes)


async def _run_failover(
    backends: List[MergeBackend], prompt: str
) -> Tuple[List[PlaceInfo], str]:
    """依鏈設定順序逐一呼叫，第一個回傳非空清單的就是答案；

    其後的後端不得被呼叫（acceptance #7）——for 迴圈找到答案就立刻
    return，本來就不會呼叫到後面的項目。
    """
    outcomes: List[_BackendCallResult] = []
    for backend in backends:
        result = await _call_backend(backend, prompt)
        if result.places is not None:
            # 各後端逐一標成功/失敗（acceptance #18），不能只列名稱靠位置隱含推斷
            attempts = "；".join(
                [f"{o.name}:失敗({o.failure_reason})" for o in outcomes]
                + [f"{result.name}:成功"]
            )
            logger.info(
                f"合併完成 mode=failover 採用後端={result.name} 各後端={attempts}"
            )
            return result.places, f"採用後端：{result.name}"
        outcomes.append(result)

    logger.info(f"合併失敗 mode=failover 各後端原因={outcomes}")
    raise MergeFailure(_failure_notes(outcomes))


def _merge_place_fields(base: PlaceInfo, addition: PlaceInfo) -> PlaceInfo:
    """欄位級合併：base 的非空值優先，只用 addition 補 base 的空值。

    「鏈設定順序中第一個有非空值的後端的值」（acceptance #14）——呼叫端
    依鏈順序把同一個地點的多筆來源依序疊上來，base 永遠是目前為止順序
    較前面的那份。
    """
    merged = {
        f.name: getattr(base, f.name) or getattr(addition, f.name)
        for f in dc_fields(PlaceInfo)
    }
    return PlaceInfo(**merged)


def _tally_votes(
    succeeded: List[_BackendCallResult],
) -> Tuple[List[PlaceInfo], Dict[str, int]]:
    """統計嚴格多數決：count >= floor(n/2)+1 才保留（acceptance #12）。

    依 `succeeded`（已經是鏈設定順序，見 `_run_vote` 的 asyncio.gather）
    逐一處理，記票鍵是 norm_place_name(name)；同一後端內同名只算一票
    （acceptance #13）；輸出順序與欄位取值都依鏈設定順序，不依完成順序
    （acceptance #14）。回傳 (kept_places, 每個後端投的票數)。
    """
    n = len(succeeded)
    threshold = n // 2 + 1

    votes: Dict[str, int] = {}
    merged: Dict[str, PlaceInfo] = {}
    order: List[str] = []
    backend_vote_counts: Dict[str, int] = {}

    for result in succeeded:
        seen_this_backend = set()
        for place in result.places:
            key = norm_place_name(place.name)
            if key in seen_this_backend:
                continue
            seen_this_backend.add(key)
            votes[key] = votes.get(key, 0) + 1
            if key not in merged:
                merged[key] = place
                order.append(key)
            else:
                merged[key] = _merge_place_fields(merged[key], place)
        backend_vote_counts[result.name] = len(seen_this_backend)

    kept = [merged[key] for key in order if votes[key] >= threshold]
    return kept, backend_vote_counts


async def _run_vote(
    backends: List[MergeBackend], prompt: str
) -> Tuple[List[PlaceInfo], str]:
    """鏈上所有後端同時呼叫（asyncio 併發，不是迴圈依序 await），
    多數決後回傳結果（acceptance #10）。
    """
    outcomes = await asyncio.gather(
        *(_call_backend(backend, prompt) for backend in backends)
    )
    succeeded = [o for o in outcomes if o.places is not None]

    if not succeeded:
        logger.info(f"合併失敗 mode=vote 各後端原因={list(outcomes)}")
        raise MergeFailure(_failure_notes(list(outcomes)))

    places, backend_vote_counts = _tally_votes(succeeded)
    backend_note = "投票後端：" + "、".join(
        f"{o.name}({backend_vote_counts[o.name]}票)" for o in succeeded
    )
    n = len(succeeded)
    # 各後端逐一標成功/失敗（acceptance #18）——失敗的後端不計入 n，
    # 但它被呼叫過，log 不能把它略去
    attempts = "；".join(
        f"{o.name}:成功({backend_vote_counts[o.name]}票)"
        if o.places is not None
        else f"{o.name}:失敗({o.failure_reason})"
        for o in outcomes
    )
    logger.info(
        f"合併完成 mode=vote 門檻={n // 2 + 1}/{n} "
        f"各後端={attempts} 採用={[p.name for p in places]}"
    )
    return places, backend_note


async def merge_with_backends(
    backends: List[MergeBackend], prompt: str, mode: str
) -> Tuple[List[PlaceInfo], Optional[str]]:
    """依 mode 跑後端鏈，回傳 (places, backend_note)。

    - failover：依鏈序試到第一個成功為止
    - vote：鏈上後端同時跑，嚴格多數決
    全部後端都失敗（丟例外、丟 MergeFailure、或回傳空清單）時丟
    `MergeFailure`，notes 逐一列出每個後端的名稱與失敗原因。
    """
    if mode == "vote":
        return await _run_vote(backends, prompt)
    if mode == "failover":
        return await _run_failover(backends, prompt)
    raise UnsupportedMergeModeError(f"不支援的合併模式：{mode!r}")
