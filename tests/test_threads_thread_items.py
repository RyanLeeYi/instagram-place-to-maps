"""F12 回歸測試：串文要挑「連結上那個帳號」的則數，不是第一則的作者。

背景：2026-08-23 以 @tomny1993/post/DU_IU9eiaC9 實測，
那則其實是回在 @brucechen1110 的串文底下。舊版以 thread_items[0] 的作者為準，
於是回傳原PO的「埔里地母廟」，而不是連結要的那份 7 間咖啡廳清單——
pipeline 後面每一步都在處理完全不相干的貼文，而且 success=True，不會有人發現。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader


def _item(username: str, caption: str, media_type: int = 1):
    return {
        "post": {
            "user": {"username": username},
            "media_type": media_type,
            "caption": {"text": caption},
        }
    }


# 實測那串的形狀：原PO在前，連結要的那則在後
REPLY_IN_OTHERS_THREAD = {
    "thread_items": [
        _item("brucechen1110", "看起來不像台灣，但是在台灣"),
        _item("tomny1993", "放幾間不像在台灣的咖啡廳"),
    ]
}

SELF_THREAD = {
    "thread_items": [
        _item("yangshin_", "過年回家媽媽問我這半年在高雄都吃什麼"),
        _item("yangshin_", "1. 一本拉麵·本店"),
    ]
}


@pytest.fixture
def downloader():
    return InstagramDownloader()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.threads.com/@tomny1993/post/DU_IU9eiaC9", "tomny1993"),
        ("https://www.threads.net/@yangshin_/post/DU5qcwtASU_", "yangshin_"),
        ("https://www.threads.com/@some.dotted_name/post/ABC123", "some.dotted_name"),
        # /t/<code> 短網址沒帶帳號
        ("https://www.threads.com/t/DU_IU9eiaC9", None),
        ("https://www.instagram.com/p/DUz0QoLAXic", None),
    ],
)
def test_從連結取得帳號(downloader, url, expected):
    assert downloader.extract_threads_author(url) == expected


def test_回覆別人串文時要挑連結上的那個作者(downloader):
    """核心回歸：不能因為原PO排在前面就回傳原PO的內容。"""
    data = downloader._extract_from_thread_node(
        REPLY_IN_OTHERS_THREAD, requested_author="tomny1993"
    )

    assert data["author"] == "tomny1993"
    assert data["thread_items_count"] == 1
    assert "咖啡廳" in data["caption"]
    assert "埔里" not in (data["caption"] or "")


def test_自己的串文兩則都要收(downloader):
    data = downloader._extract_from_thread_node(SELF_THREAD, requested_author="yangshin_")

    assert data["author"] == "yangshin_"
    assert data["thread_items_count"] == 2
    assert "一本拉麵" in data["caption"]


def test_沒帶帳號時退回舊行為_取第一則的作者(downloader):
    """/t/<code> 短網址沒有帳號可用，行為要與改動前一致。"""
    data = downloader._extract_from_thread_node(REPLY_IN_OTHERS_THREAD, requested_author=None)

    assert data["author"] == "brucechen1110"


def test_連結帳號不在這串裡時也退回第一則的作者(downloader):
    """帳號改名或解析出錯時不能整個回傳空的。"""
    data = downloader._extract_from_thread_node(
        REPLY_IN_OTHERS_THREAD, requested_author="someone_else"
    )

    assert data["author"] == "brucechen1110"
    assert data["thread_items_count"] == 1


def test_空串文不會炸(downloader):
    data = downloader._extract_from_thread_node({"thread_items": []}, requested_author="x")

    assert data["thread_items_count"] == 0
    assert data["author"] is None


def test_media_type_取作者自己的第一則(downloader):
    """舊版取 thread_items[0]，回覆別人串文時會拿到原PO的型別。"""
    node = {
        "thread_items": [
            _item("brucechen1110", "原PO", media_type=2),   # 影片
            _item("tomny1993", "咖啡廳清單", media_type=8),  # 輪播
        ]
    }

    data = downloader._extract_from_thread_node(node, requested_author="tomny1993")

    assert data["media_type"] == 8
