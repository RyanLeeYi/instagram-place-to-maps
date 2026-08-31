"""F29：/mergebackends 指令——不重啟即可改變合併階段的後端鏈。

推翻 F27.2「鏈的內容只讀 env、要換鏈就重啟」的決定；F27.2 對 env 預設值
本身的驗證規則不變（PlaceExtractor.__init__ 仍然建構期報錯）。

單元測試不打真後端：後端鏈的驗證只碰 get_backend_chain（建構後端物件，
不呼叫 merge()），extract() 端到端的斷言一律用注入的 FakeMergeBackend。
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from telegram import InlineKeyboardMarkup

from app.config import RuntimeSettings, runtime_settings, settings
from app.bot.handlers import PlaceBotHandlers
from app.services import place_extractor as pe
from app.services.merge_backends import UnsupportedMergeBackendError, get_backend
from app.services.place_extractor import PlaceExtractor, PlaceInfo, supported_merge_backend_names


class FakeMergeBackend:
    """記錄有沒有被呼叫、回傳預先準備好的地點清單（與 test_merge_backend.py 同款）。"""

    def __init__(self, places, name="fake"):
        self._places = places
        self.name = name
        self.calls = []

    async def merge(self, prompt):
        self.calls.append(prompt)
        return self._places


def _reset_runtime_settings(tmp_path, monkeypatch):
    """每條測試都用獨立的 tmp_path 檔案，不寫到 repo 真正的 runtime_settings.json。"""
    monkeypatch.setattr(runtime_settings, "_settings_file", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(runtime_settings, "_merge_backends", None)
    monkeypatch.setattr(runtime_settings, "_merge_mode", None)


def _bare_handler(authorized=True):
    h = PlaceBotHandlers.__new__(PlaceBotHandlers)
    h._is_authorized = lambda chat_id: authorized
    return h


class _FakeMessage:
    def __init__(self):
        self.replies = []
        self.reply_markup_seen = None

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_markup_seen = kwargs.get("reply_markup")
        return SimpleNamespace()


def _fake_update_context(args):
    msg = _FakeMessage()
    update = SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(args=args)
    return update, context, msg


class _FakeQuery:
    """記錄 answer() 有沒有被呼叫、edit_message_text 的每次呼叫內容。"""

    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edits = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return SimpleNamespace()


def _fake_callback_update(data):
    query = _FakeQuery(data)
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace()
    return update, context, query


# --- 合法名稱清單來源是 get_backend 認得的名稱，不是另抄一份 ----------------


def test_supported名稱清單與get_backend一致不漂移():
    names = supported_merge_backend_names()
    assert set(names) == {"ollama", "agy", "claude", "codex", "claude-api", "codex-api"}
    for name in names:
        backend = get_backend(name)
        assert backend.name == name, "清單裡的名稱必須是 get_backend 真的認得、且 dispatch 到同名後端"


# --- acceptance #1：無參數查詢 ---------------------------------------------


def test_無參數時回覆目前鏈模式與合法名稱清單(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama,agy")
    monkeypatch.setattr(runtime_settings, "_merge_mode", "vote")

    h = _bare_handler()
    update, context, msg = _fake_update_context([])

    asyncio.run(h.mergebackends_handler(update, context))

    assert msg.replies
    reply = msg.replies[-1]
    assert "ollama,agy" in reply
    assert "vote" in reply
    for name in supported_merge_backend_names():
        assert name in reply


# --- acceptance #2、#6：合法切換後 extract() 拿到的鏈改變 -------------------


def test_合法切換後extract拿到新鏈且同鏈不重複建構(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)

    extractor = PlaceExtractor()
    original = FakeMergeBackend([PlaceInfo(name="舊鏈")], name="ollama")
    extractor._backends = [original]
    extractor._backends_chain = "ollama"

    build_calls = []

    def fake_get_backend_chain(chain):
        build_calls.append(chain)
        return [FakeMergeBackend([PlaceInfo(name="新鏈")], name=chain)]

    monkeypatch.setattr(pe, "get_backend_chain", fake_get_backend_chain)

    h = _bare_handler()
    update, context, msg = _fake_update_context(["agy,", "claude"])
    asyncio.run(h.mergebackends_handler(update, context))

    assert msg.replies
    assert "agy,claude" in msg.replies[-1]

    result = asyncio.run(
        extractor.extract(transcript="", visual_description="", caption="")
    )
    assert [p.name for p in result.places] == ["新鏈"]
    assert build_calls == ["agy,claude"]

    # 第二次 extract()：鏈沒變，不該再重建
    asyncio.run(extractor.extract(transcript="", visual_description="", caption=""))
    assert build_calls == ["agy,claude"], "同一條鏈不得每次 extract() 都重建後端物件"


# --- acceptance #3：鏈為空或含不認得的名稱 ----------------------------------


def test_非法名稱不改現況且不落盤(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")
    settings_file = tmp_path / "runtime_settings.json"

    h = _bare_handler()
    update, context, msg = _fake_update_context(["ollama,gemini"])
    asyncio.run(h.mergebackends_handler(update, context))

    assert "無效" in msg.replies[-1]
    for name in supported_merge_backend_names():
        assert name in msg.replies[-1]
    assert runtime_settings.merge_backends == "ollama", "驗證失敗不該改現行鏈"
    assert not settings_file.exists(), "驗證失敗不該落盤"


def test_空鏈不改現況(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")

    h = _bare_handler()
    update, context, msg = _fake_update_context([" , ,"])
    asyncio.run(h.mergebackends_handler(update, context))

    assert "無效" in msg.replies[-1]
    assert runtime_settings.merge_backends == "ollama"


# --- acceptance #4：runtime_settings.json 內的鏈不合法時退回 env -----------


def test_runtime_settings_json有非法鏈時警告並退回env值(tmp_path, monkeypatch, caplog):
    (tmp_path / "runtime_settings.json").write_text(
        json.dumps({"merge_backends": "not-a-real-backend"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING)

    rs = RuntimeSettings()

    assert rs.merge_backends == settings.merge_backends
    assert any("merge_backends" in record.message for record in caplog.records)


# --- acceptance #7：未授權拒絕 ----------------------------------------------


def test_未授權時拒絕且不查詢不改鏈(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")

    h = _bare_handler(authorized=False)
    update, context, msg = _fake_update_context(["agy"])
    asyncio.run(h.mergebackends_handler(update, context))

    assert msg.replies == ["未授權的使用者"]
    assert runtime_settings.merge_backends == "ollama"


def test_未授權時無參數也拒絕(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)

    h = _bare_handler(authorized=False)
    update, context, msg = _fake_update_context([])
    asyncio.run(h.mergebackends_handler(update, context))

    assert msg.replies == ["未授權的使用者"]


# --- acceptance #8：/start、/help 指令表要出現 /mergebackends --------------


def test_start與help指令表含mergebackends():
    h = _bare_handler()

    update, _, start_msg = _fake_update_context([])
    asyncio.run(h.start_handler(update, None))
    assert "/mergebackends" in start_msg.replies[-1]

    update2, _, help_msg = _fake_update_context([])
    asyncio.run(h.help_handler(update2, None))
    assert "/mergebackends" in help_msg.replies[-1]


# --- F30：/mergebackends 無參數回覆加 inline keyboard -----------------------
# acceptance (1)：按鈕清單來源、順序、每列數量、使用中標記


def test_按鈕清單與SUPPORTED_MERGE_BACKENDS一致順序不漂移且每列最多3顆():
    h = _bare_handler()

    _, reply_markup = h._build_mergebackends_view("ollama", "failover")

    names_in_order = [
        button.callback_data.replace("mergebackends_", "")
        for row in reply_markup.inline_keyboard
        for button in row
    ]
    assert names_in_order == list(supported_merge_backend_names())
    assert all(len(row) <= 3 for row in reply_markup.inline_keyboard)

    first_button = reply_markup.inline_keyboard[0][0]
    assert "(使用中)" in first_button.text, "目前鏈第一個後端要標使用中"


def test_無參數回覆帶按鈕(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "claude,ollama")

    h = _bare_handler()
    update, context, msg = _fake_update_context([])
    asyncio.run(h.mergebackends_handler(update, context))

    assert msg.reply_markup_seen, "無參數回覆要帶 inline keyboard"
    all_buttons = [b for row in msg.reply_markup_seen.inline_keyboard for b in row]
    claude_button = next(b for b in all_buttons if b.callback_data == "mergebackends_claude")
    assert "(使用中)" in claude_button.text


# acceptance (2)：點按鈕＝把鏈設成「該後端,ollama」（ollama 例外），走
# set_merge_backends 同一套驗證與落盤，並用 edit_message_text 更新同一則訊息


def test_點claude後鏈變成claude逗號ollama並更新訊息與按鈕(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")

    h = _bare_handler()
    update, context, query = _fake_callback_update("mergebackends_claude")
    asyncio.run(h.mergebackends_callback_handler(update, context))

    assert query.answered
    assert runtime_settings.merge_backends == "claude,ollama"
    assert query.edits
    text, kwargs = query.edits[-1]
    assert "claude,ollama" in text
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


def test_點ollama後鏈為單一ollama(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "claude,ollama")

    h = _bare_handler()
    update, context, query = _fake_callback_update("mergebackends_ollama")
    asyncio.run(h.mergebackends_callback_handler(update, context))

    assert runtime_settings.merge_backends == "ollama"
    text, _ = query.edits[-1]
    assert "目前後端鏈：ollama" in text


# acceptance (4)：callback 走 _is_authorized 同一套，未授權不改鏈


def test_未授權callback回未授權且不改鏈(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")

    h = _bare_handler(authorized=False)
    update, context, query = _fake_callback_update("mergebackends_claude")
    asyncio.run(h.mergebackends_callback_handler(update, context))

    assert query.edits == [("未授權的使用者", {})]
    assert runtime_settings.merge_backends == "ollama"


# acceptance (5)：callback_data 被偽造成不支援的名稱，set_merge_backends 回
# False 時回錯誤、不改現況


def test_偽造名稱callback不改鏈且回錯誤(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "ollama")

    h = _bare_handler()
    update, context, query = _fake_callback_update("mergebackends_gemini")
    asyncio.run(h.mergebackends_callback_handler(update, context))

    assert runtime_settings.merge_backends == "ollama"
    text, _ = query.edits[-1]
    assert "無效" in text


# 回歸：runtime_settings.json 已有覆寫鏈時，PlaceExtractor 建構就用覆寫鏈，
# 第一次 extract() 不得重建（否則測試注入的假後端會被真後端蓋掉去打 CLI；
# 2026-08-31 點了 agy 按鈕之後整套測試卡 14 秒/條、7 條紅就是這個）。


def test_建構時已有runtime覆寫鏈則首次extract不重建(tmp_path, monkeypatch):
    _reset_runtime_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_settings, "_merge_backends", "agy,ollama")

    import app.services.place_extractor as pe

    build_calls = []
    real = pe.get_backend_chain

    def counting(chain):
        build_calls.append(chain)
        return [FakeMergeBackend([PlaceInfo(name="假鏈")], name=chain)]

    monkeypatch.setattr(pe, "get_backend_chain", counting)
    extractor = PlaceExtractor()
    assert build_calls[-1] == "agy,ollama", "建構應直接用 runtime 覆寫鏈"

    injected = FakeMergeBackend([PlaceInfo(name="注入")], name="ollama")
    extractor._backends = [injected]
    n = len(build_calls)
    asyncio.run(extractor.extract(transcript="", visual_description="", caption=""))
    assert len(build_calls) == n, "鏈沒變，第一次 extract() 不該重建"
    assert injected.calls, "注入的後端必須真的被呼叫"
    monkeypatch.setattr(pe, "get_backend_chain", real)
