"""F14 回歸測試：登入狀態要反映真實 session，不是「檔案存在」。

背景：2026-07-25 發現 is_logged_in() 只檢查 auth_file.exists()，
Google session 死了半年仍一路回報 True——F8 壞掉沒被察覺的第二層原因。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.google_maps_saver import GoogleMapsSaver

DAY = 86400


def _cookie(name, expires, domain=".google.com"):
    return {
        "name": name,
        "value": "dummy",
        "domain": domain,
        "path": "/",
        "expires": expires,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
    }


def _write_auth(tmp_path, cookies, filename="google_auth.json"):
    """寫一份 auth 檔並回傳綁到它的 saver。"""
    path = tmp_path / filename
    path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")
    saver = GoogleMapsSaver()
    saver.auth_file = path
    return saver


def _fresh_cookies():
    later = time.time() + 365 * DAY
    return [_cookie(name, later) for name in GoogleMapsSaver.ESSENTIAL_COOKIES]


def _expired_cookies():
    earlier = time.time() - 180 * DAY
    return [_cookie(name, earlier) for name in GoogleMapsSaver.ESSENTIAL_COOKIES]


def test_沒有_auth_檔時未登入(tmp_path):
    saver = GoogleMapsSaver()
    saver.auth_file = tmp_path / "does_not_exist.json"

    assert saver.is_logged_in() is False
    assert "/setup_google" in saver.login_status_message()


def test_cookies_未過期時視為已登入(tmp_path):
    saver = _write_auth(tmp_path, _fresh_cookies())

    assert saver.is_logged_in() is True


def test_檔案存在但_cookies_已過期時未登入(tmp_path):
    """核心回歸：這正是 2026-07-25 那份躺了半年的 google_auth.json。"""
    saver = _write_auth(tmp_path, _expired_cookies())

    assert saver.auth_file.exists()
    assert saver.is_logged_in() is False
    message = saver.login_status_message()
    assert "過期" in message
    assert "/setup_google" in message


def test_只要有一個必要_cookie_過期就算未登入(tmp_path):
    cookies = _fresh_cookies()
    cookies[0] = _cookie(GoogleMapsSaver.ESSENTIAL_COOKIES[0], time.time() - DAY)
    saver = _write_auth(tmp_path, cookies)

    assert saver.is_logged_in() is False
    assert GoogleMapsSaver.ESSENTIAL_COOKIES[0] in saver.login_status_message()


def test_缺少必要_cookie_時未登入並指出缺哪個(tmp_path):
    cookies = [c for c in _fresh_cookies() if c["name"] != "SAPISID"]
    saver = _write_auth(tmp_path, cookies)

    assert saver.is_logged_in() is False
    assert "SAPISID" in saver.login_status_message()


def test_其他網域的同名_cookie_不算數(tmp_path):
    """youtube.com 上也有 SID/SAPISID；Maps 要的是 google.com 那組。"""
    later = time.time() + 365 * DAY
    cookies = [_cookie(name, later, domain=".youtube.com") for name in GoogleMapsSaver.ESSENTIAL_COOKIES]
    saver = _write_auth(tmp_path, cookies)

    assert saver.is_logged_in() is False


def test_auth_檔沒有_cookies_時未登入(tmp_path):
    saver = _write_auth(tmp_path, [])

    assert saver.is_logged_in() is False
    assert "/setup_google" in saver.login_status_message()


def test_session_cookie_沒有到期時間時不誤判為過期(tmp_path):
    """expires 為 -1 代表瀏覽器 session cookie，本機判定不了，不能當成過期。"""
    cookies = [_cookie(name, -1) for name in GoogleMapsSaver.ESSENTIAL_COOKIES]
    saver = _write_auth(tmp_path, cookies)

    assert saver.is_logged_in() is True


def test_verify_session_在本機就判定失效時不開瀏覽器(tmp_path):
    """過期的 auth 檔不必連網就知道死了，直接回 not_logged_in。"""
    saver = _write_auth(tmp_path, _expired_cookies())

    result = asyncio.run(saver.verify_session())

    assert result.success is False
    assert result.status == "not_logged_in"
    assert "/setup_google" in result.message


def test_過期時_save_to_list_回報_not_logged_in_而非通用失敗(tmp_path):
    saver = _write_auth(tmp_path, _expired_cookies())
    saver.is_enabled = lambda: True

    result = asyncio.run(saver.save_to_list("ChIJdummy"))

    assert result.status == "not_logged_in"
    assert "/setup_google" in result.message
    assert result.error_message == result.message


def test_過期時_get_saved_lists_也給得出可行動的訊息(tmp_path):
    saver = _write_auth(tmp_path, _expired_cookies())
    saver.is_enabled = lambda: True

    result = asyncio.run(saver.get_saved_lists())

    assert result.success is False
    assert "/setup_google" in result.error_message
