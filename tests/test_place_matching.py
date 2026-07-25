"""F18 測試：Places 匹配要有信心度，不得靜默採信第一筆候選。

背景：2026-07-25 實測，逐字稿明確講「巫婆水餃」，Places API 回「芳芳江蘇水餃」，
程式直接 found=True 收下，使用者被送到隔壁家。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.google_places import GooglePlacesService, MatchConfidence, name_similarity


class Test名稱相似度:
    def test_完全相同為_1(self):
        assert name_similarity("巫婆水餃店", "巫婆水餃店") == pytest.approx(1.0)

    def test_包含關係算高相似(self):
        # LLM 給「巫婆水餃店」、Google 收錄「巫婆水餃」——同一家
        assert name_similarity("巫婆水餃店", "巫婆水餃") >= 0.8

    def test_不同店家算低相似(self):
        # 這就是 2026-07-25 抓錯的那組
        assert name_similarity("巫婆水餃店", "芳芳江蘇水餃") < 0.6
        assert name_similarity("巫婆水餃店", "丫頭水餃") < 0.6

    def test_空字串不炸(self):
        assert name_similarity("", "巫婆水餃") == 0.0
        assert name_similarity("巫婆水餃", "") == 0.0
        assert name_similarity(None, None) == 0.0


class Test候選挑選:
    """_pick_best_candidate 純函式：給候選清單與期望店名，回傳 (候選, 信心度)"""

    def test_命中期望店名就算高信心(self):
        candidates = [
            {"displayName": {"text": "芳芳江蘇水餃"}},
            {"displayName": {"text": "巫婆水餃"}},
        ]
        service = GooglePlacesService()
        best, confidence = service._pick_best_candidate(candidates, "巫婆水餃店")
        assert best["displayName"]["text"] == "巫婆水餃"
        assert confidence == MatchConfidence.HIGH

    def test_全部都不像時標低信心而不是靜默採用(self):
        """F18 的核心：這組正是 2026-07-25 的失敗案例"""
        candidates = [
            {"displayName": {"text": "芳芳江蘇水餃"}},
            {"displayName": {"text": "丫頭水餃"}},
        ]
        service = GooglePlacesService()
        best, confidence = service._pick_best_candidate(candidates, "巫婆水餃店")
        assert confidence == MatchConfidence.LOW
        # 仍回傳第一筆供參考，但信心度必須誠實
        assert best is not None

    def test_沒有期望店名時不假裝高信心(self):
        candidates = [{"displayName": {"text": "某某小吃"}}]
        service = GooglePlacesService()
        best, confidence = service._pick_best_candidate(candidates, None)
        assert confidence == MatchConfidence.MEDIUM

    def test_空候選回傳_None(self):
        service = GooglePlacesService()
        best, confidence = service._pick_best_candidate([], "巫婆水餃店")
        assert best is None
        assert confidence == MatchConfidence.LOW


class Test結果物件:
    def test_預設信心度不是_high(self):
        from app.services.google_places import PlaceSearchResult

        result = PlaceSearchResult(found=True, name="某店")
        assert result.match_confidence != MatchConfidence.HIGH

    def test_低信心結果要能被呼叫端辨識(self):
        from app.services.google_places import PlaceSearchResult

        result = PlaceSearchResult(
            found=True, name="芳芳江蘇水餃", match_confidence=MatchConfidence.LOW
        )
        assert result.needs_human_check is True

    def test_高信心不需要人工確認(self):
        from app.services.google_places import PlaceSearchResult

        result = PlaceSearchResult(
            found=True, name="巫婆水餃", match_confidence=MatchConfidence.HIGH
        )
        assert result.needs_human_check is False
