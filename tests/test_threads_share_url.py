"""F25 回歸測試：Threads 分享短連結（/share/<id>）要能跟正規連結一樣被解析。

背景：Threads 分享按鈕產生的就是 `/share/<id>` 這種網址，目前 100% 落到
「無法解析此連結，請確認是否為有效的 Threads 連結」——連結是有效的，是我們
不認得，跟 F24 修掉的那種誤導性錯誤訊息同一種病。

做法：解析前先跟隨 HTTP 轉址取得正規網址（@user/post/<id> 或 /t/<id>），
再交給既有解析邏輯。轉址那一步是可替換的接縫（`downloader._share_url_resolver`），
以下全部用假的 resolver，不連外。真正連外的檢查在檔案最後，預設 skip。
"""

import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader

# 2026-08-25 實測：這兩條分享短連結各自轉址到什麼正規網址
SHARE_A = "https://www.threads.com/share/DJ4GTzoS2/"
RESOLVED_A = "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX"
SHARE_B = "https://www.threads.com/share/DZPznCtey/"
RESOLVED_B = "https://www.threads.com/@mooqi_eat/post/DLRnCYQSqsJ"


@pytest.fixture
def downloader():
    return InstagramDownloader()


def _fake_resolver(target_url: str):
    async def resolver(url: str) -> str:
        return target_url

    return resolver


def _raising_resolver(exc: Exception):
    async def resolver(url: str) -> str:
        raise exc

    return resolver


# --- 情況一：短連結解析成功 ---


@pytest.mark.parametrize(
    "share_url, redirect_target, expected",
    [
        (SHARE_A, f"{RESOLVED_A}?xmt=AQGz", RESOLVED_A),
        (SHARE_B, f"{RESOLVED_B}?xmt=AQGz", RESOLVED_B),
    ],
)
def test_短連結解析成功_轉址結果削掉查詢參數後成為正規網址(
    downloader, share_url, redirect_target, expected
):
    import asyncio

    downloader._share_url_resolver = _fake_resolver(redirect_target)
    resolved, error = asyncio.run(downloader.resolve_threads_url(share_url))

    assert error is None
    assert resolved == expected
    assert downloader.is_threads_url(resolved)


def test_短連結解析成功後接進既有_pipeline_行為與正規連結一致(downloader):
    """驗證 download_threads_post() 真的把解析後的正規網址往下傳，
    而不是只有 resolve_threads_url() 本身正確。"""
    import asyncio

    downloader._share_url_resolver = _fake_resolver(f"{RESOLVED_A}?xmt=AQGz")

    seen_urls = []

    async def fake_detect(url):
        seen_urls.append(url)
        from app.services.downloader import ThreadsContentType

        return ThreadsContentType.TEXT_ONLY, {
            "description": "測試內容",
            "author": "mooqi_eat",
        }

    downloader.detect_threads_content_type = fake_detect

    result = asyncio.run(downloader.download_threads_post(SHARE_A))

    assert result.success is True
    assert seen_urls == [RESOLVED_A]


# --- 情況二：轉址失敗 ---


def test_轉址逾時_回傳可區分的錯誤訊息不冒用無法解析此連結(downloader):
    import asyncio

    downloader._share_url_resolver = _raising_resolver(
        httpx.TimeoutException("timed out")
    )

    resolved, error = asyncio.run(downloader.resolve_threads_url(SHARE_A))

    assert resolved is None
    assert error is not None
    assert "無法解析此連結，請確認是否為有效的 Threads 連結" not in error
    assert "逾時" in error


def test_轉址連線失敗_回傳可區分的錯誤訊息且與逾時不同句(downloader):
    """連不上 Threads 跟連線逾時是兩種不同的失敗，訊息不能撞在一起，
    不然使用者分不出是網路慢還是真的連不上。"""
    import asyncio

    downloader._share_url_resolver = _raising_resolver(
        httpx.ConnectError("connection refused")
    )
    resolved, connect_error = asyncio.run(downloader.resolve_threads_url(SHARE_A))

    downloader._share_url_resolver = _raising_resolver(
        httpx.TimeoutException("timed out")
    )
    _, timeout_error = asyncio.run(downloader.resolve_threads_url(SHARE_A))

    assert resolved is None
    assert connect_error is not None
    assert "無法解析此連結，請確認是否為有效的 Threads 連結" not in connect_error
    assert connect_error != timeout_error


# --- 情況三：轉址結果仍不合法 ---


def test_轉址目標不是合法_Threads_貼文網址_回傳可區分的錯誤訊息(downloader):
    import asyncio

    downloader._share_url_resolver = _fake_resolver("https://www.threads.com/")

    resolved, error = asyncio.run(downloader.resolve_threads_url(SHARE_A))

    assert resolved is None
    assert error is not None
    assert "無法解析此連結，請確認是否為有效的 Threads 連結" not in error
    assert "逾時" not in error


# --- 情況四：既有格式不退化 ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX",
        "https://www.threads.net/@mooqi_eat/post/DLRnLRfSqvX",
        "https://www.threads.com/t/DLRnLRfSqvX",
        "https://www.threads.net/t/DLRnLRfSqvX",
    ],
)
def test_既有正規連結格式不經過轉址原樣通過(downloader, url):
    import asyncio

    calls = []

    async def spy(_url: str) -> str:
        calls.append(_url)
        return "不該被呼叫"

    downloader._share_url_resolver = spy

    resolved, error = asyncio.run(downloader.resolve_threads_url(url))

    assert error is None
    assert resolved == url
    assert calls == []  # 完全沒觸發轉址


def test_is_threads_share_url_只認_share_路徑(downloader):
    assert downloader.is_threads_share_url("https://www.threads.com/share/DJ4GTzoS2/")
    assert downloader.is_threads_share_url("https://threads.net/share/abc123")
    assert not downloader.is_threads_share_url(
        "https://www.threads.com/@mooqi_eat/post/DLRnLRfSqvX"
    )
    assert not downloader.is_threads_share_url("https://www.threads.com/t/abc123")


# --- 需要網路的人工檢查（預設 skip） ---


@pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason=(
        "需要真的連外到 Threads。手動執行："
        "RUN_NETWORK_TESTS=1 .venv/Scripts/python.exe -m pytest "
        "tests/test_threads_share_url.py -k 實機 -v"
    ),
)
@pytest.mark.parametrize(
    "share_url, expected",
    [(SHARE_A, RESOLVED_A), (SHARE_B, RESOLVED_B)],
)
def test_實機_真的轉址到_Threads_取得正規網址(share_url, expected):
    import asyncio

    downloader = InstagramDownloader()
    resolved, error = asyncio.run(downloader.resolve_threads_url(share_url))

    assert error is None
    assert resolved == expected
