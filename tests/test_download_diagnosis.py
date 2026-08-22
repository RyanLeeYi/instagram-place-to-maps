"""F11 回歸測試：下載失敗的訊息要說得出「是設定問題還是上游問題」。

背景：2026-08-23 以歷史上成功抓過的 /p/ 貼文重跑 download_post()，
cookies.txt 的 session 已失效，instaloader 未認證被 Instagram 擋下（403），
最後從函式庫深處冒出 TypeError，使用者只看到
「下載失敗: 'NoneType' object is not subscriptable」——
看不出該去重匯 cookies，還是等 Instagram 恢復。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader

# 實際踩到的那個例外：403 之後 instaloader 解析空回應炸掉
REAL_FAILURE = TypeError("'NoneType' object is not subscriptable")


@pytest.fixture
def downloader():
    return InstagramDownloader()


def test_未認證時說是設定問題並指名_cookies(downloader):
    """核心回歸：cookies 過期時要叫人去重匯 cookies.txt，不是丟 Python 內部錯誤。"""
    downloader._instaloader_username = None

    message = downloader._diagnose_download_failure(REAL_FAILURE)

    assert message.startswith("設定問題：")
    assert "cookies.txt" in message
    # 原始錯誤仍要留著，不然沒東西可查
    assert "NoneType" in message


def test_已認證但被擋時說是上游問題(downloader):
    downloader._instaloader_username = "someone"

    message = downloader._diagnose_download_failure(
        Exception("JSON Query to graphql/query: 403 Forbidden")
    )

    assert "上游問題" in message
    assert "不是本機設定問題" in message
    assert "someone" in message


@pytest.mark.parametrize(
    "raw",
    [
        "429 Too Many Requests",
        "Please wait a few minutes before you try again.",
        "ConnectionException: 401 Unauthorized",
        "Checkpoint required",
    ],
)
def test_各種上游訊號都判成上游(downloader, raw):
    downloader._instaloader_username = "someone"

    assert "上游問題" in downloader._diagnose_download_failure(Exception(raw))


def test_已認證時結構不符也算上游而非設定(downloader):
    """Instagram 改版導致解析失敗——設定沒問題，重匯 cookies 也沒用。"""
    downloader._instaloader_username = "someone"

    message = downloader._diagnose_download_failure(REAL_FAILURE)

    assert message.startswith("上游問題：")
    # 「不是本機設定問題」裡也有「設定問題」四個字，要比對開頭那個標籤
    assert "設定問題：" not in message


def test_訊息不會空白(downloader):
    """例外沒有訊息時仍要說得出類別，不能給一個空字串。"""
    downloader._instaloader_username = "someone"

    message = downloader._diagnose_download_failure(RuntimeError())

    assert "RuntimeError" in message
