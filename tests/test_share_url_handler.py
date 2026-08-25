"""Threads 分享短連結在 handlers 這一層的行為（F25）。

downloader 那層的轉址邏輯由 test_threads_share_url.py 負責。這一支管的是
「使用者在 Telegram 貼上短連結」這條真實路徑——F25 第一輪驗收就是死在這裡：
downloader 寫得完全正確，但 handlers 的 _extract_url 不認得 /share/，
訊息在進到 downloader 之前就被擋掉了，新程式碼變成到不了的死碼。
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot.handlers import PlaceBotHandlers


def _bare_handler():
    """不跑 __init__ 的空殼，只測 URL 判斷那幾個純函式。"""
    return PlaceBotHandlers.__new__(PlaceBotHandlers)


def test_短連結會被擷取出來():
    h = _bare_handler()
    url = h._extract_url("看看這間 https://www.threads.com/share/DJ4GTzoS2/ 好吃")
    assert url == "https://www.threads.com/share/DJ4GTzoS2"


def test_短連結判定為_threads():
    h = _bare_handler()
    url = "https://www.threads.com/share/DJ4GTzoS2"
    assert h._get_url_type(url) == "threads"
    assert h._get_platform(url) == "threads"


def test_既有格式不退化():
    h = _bare_handler()
    for text, expected in [
        (
            "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX",
            "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX",
        ),
        ("https://www.threads.net/t/abc123", "https://www.threads.net/t/abc123"),
        (
            "https://www.instagram.com/reel/DcAQ-iTvQdA/",
            "https://www.instagram.com/reel/DcAQ-iTvQdA",
        ),
    ]:
        assert h._extract_url(text) == expected

    assert h._get_url_type("https://www.instagram.com/reel/DcAQ-iTvQdA") == "reel"


class _FakeMessage:
    def __init__(self, text, message_id=999):
        self.text = text
        self.message_id = message_id
        self.from_user = SimpleNamespace(is_bot=False, id=1)
        self.reply_to_message = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(edit_text=None)


def _fake_update(text, chat_id=1, message_id=999):
    msg = _FakeMessage(text, message_id)
    return SimpleNamespace(
        message=msg,
        edited_message=None,
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=msg,
    ), msg


def _prepare(handler, resolved="https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX"):
    """把 message_handler 早期會用到的東西都換成替身，只留 URL 這條路。"""
    seen = {}

    async def fake_resolve(url):
        seen["resolve_arg"] = url
        return resolved, None

    async def fake_find(url):
        seen["dedup_arg"] = url
        return [SimpleNamespace(name="替身", city=None, google_maps_url=None)]

    handler.downloader = SimpleNamespace(
        is_threads_share_url=lambda u: "/share/" in u,
        resolve_threads_url=fake_resolve,
    )
    handler._find_places_by_url = fake_find
    handler._format_existing_places = lambda places: "替身回覆"
    handler._is_authorized = lambda chat_id: True
    handler._processed_message_ids = set()
    handler._processing_messages = set()
    return seen


def test_去重比對的是轉址後的網址():
    """轉址一定要發生在去重之前。

    去重是拿貼文 ID 比對的，而短連結裡那串 ID 不是貼文 ID。順序反了，
    同一則貼文從分享鈕貼進來就會被當成新的一則——F13 修掉的那種重複。
    """
    h = _bare_handler()
    seen = _prepare(h)
    update, msg = _fake_update("https://www.threads.com/share/DJ4GTzoS2/")

    asyncio.run(h.message_handler(update, None))

    assert seen["resolve_arg"] == "https://www.threads.com/share/DJ4GTzoS2"
    assert seen["dedup_arg"] == "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX"


def test_正規連結不會觸發轉址():
    h = _bare_handler()
    seen = _prepare(h)
    canonical = "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX"
    update, msg = _fake_update(canonical)

    asyncio.run(h.message_handler(update, None))

    assert "resolve_arg" not in seen
    assert seen["dedup_arg"] == canonical


def test_轉址失敗時回報轉址的錯誤而不是_請貼有效連結():
    """錯誤訊息不能把網路問題講成使用者貼錯連結（與 F24 同型）。"""
    h = _bare_handler()
    _prepare(h)

    async def failing_resolve(url):
        return None, "連上 Threads 逾時，暫時無法解析此分享連結，請稍後再試"

    h.downloader.resolve_threads_url = failing_resolve
    update, msg = _fake_update("https://www.threads.com/share/DJ4GTzoS2/")

    asyncio.run(h.message_handler(update, None))

    assert msg.replies, "應該要回一則錯誤訊息"
    assert "逾時" in msg.replies[-1]
    assert "請傳送有效的" not in msg.replies[-1]
