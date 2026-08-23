r"""F24 回歸測試：cookies.txt 沒有 sessionid（＝未登入）時要當場喊，不得偽裝成「影片不存在」。

背景：2026-08-23 查到 repo 的 cookies.txt 只有 8 個 cookie、不含 sessionid，
而 Instagram 對未登入請求回的是
「Requested content is not available, rate-limit reached or login required」。
downloader 的 `"not available" in error_msg` 分支把它翻成「此影片已不存在或無法存取」——
一個設定問題被講成內容問題，每次都往錯的方向查。
"""

import asyncio
import sys
from pathlib import Path

import pytest
import yt_dlp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader

REEL_URL = "https://www.instagram.com/reel/DT2w2PVgXo3"

# 實際踩到的那則 yt-dlp 訊息：登入與限流被講成同一句話
REAL_YTDLP_ERROR = (
    "ERROR: [Instagram] DT2w2PVgXo3: Requested content is not available, "
    "rate-limit reached or login required. Use --cookies, --cookies-from-browser, "
    "--username and --password, --netrc-cmd, or --netrc (instagram) to provide "
    "account credentials"
)

COOKIE_NAMES_WITHOUT_SESSION = [
    "ps_l", "ps_n", "ig_nrcb", "ig_did", "csrftoken", "rur", "mid", "ds_user_id",
]


def _write_cookies(path: Path, names: list[str]) -> Path:
    """寫一份 Netscape 格式的 cookies.txt。"""
    lines = ["# Netscape HTTP Cookie File"]
    lines += [
        f".instagram.com\tTRUE\t/\tTRUE\t0\t{name}\tvalue-of-{name}"
        for name in names
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def downloader_factory(tmp_path, monkeypatch):
    """建一個 cookies.txt 指定給 downloader，並讓 yt-dlp 直接丟出真實的下載錯誤。"""

    def _make(cookie_names: list[str]) -> InstagramDownloader:
        cookies = _write_cookies(tmp_path / "cookies.txt", cookie_names)
        monkeypatch.setattr(InstagramDownloader, "COOKIES_FILE", cookies)

        class _ExplodingYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def download(self, urls):
                raise yt_dlp.utils.DownloadError(REAL_YTDLP_ERROR)

            def extract_info(self, url, download=False):
                raise yt_dlp.utils.DownloadError(REAL_YTDLP_ERROR)

        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ExplodingYDL)

        dl = InstagramDownloader()
        dl.temp_dir = tmp_path
        return dl

    return _make


def test_缺少_sessionid_時錯誤訊息指向_cookies_而不是影片不存在(downloader_factory):
    """核心回歸：未登入要說未登入，不能說影片不存在。"""
    dl = downloader_factory(COOKIE_NAMES_WITHOUT_SESSION)

    result = asyncio.run(dl.download(REEL_URL))

    assert result.success is False
    assert "cookies" in result.error_message.lower()
    assert "已不存在" not in result.error_message


def test_缺少_sessionid_時載入當下就判定為未認證(downloader_factory):
    dl = downloader_factory(COOKIE_NAMES_WITHOUT_SESSION)

    assert dl._cookies_authenticated is False


def test_有_sessionid_時視為已認證(downloader_factory):
    dl = downloader_factory(COOKIE_NAMES_WITHOUT_SESSION + ["sessionid"])

    assert dl._cookies_authenticated is True


def test_有_sessionid_時不把上游錯誤誤報成_cookies_問題(downloader_factory):
    """反向護欄：這個訊息不可以變成所有下載失敗的萬用答案。"""
    dl = downloader_factory(COOKIE_NAMES_WITHOUT_SESSION + ["sessionid"])

    result = asyncio.run(dl.download(REEL_URL))

    assert result.success is False
    assert "未含登入憑證" not in result.error_message
