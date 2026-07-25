r"""冒煙測試：一則真實 IG Reel 走完 下載 → 轉錄 → 視覺 → 擷取 → Places → Sheets 認證 → Maps 登入狀態。

唯讀：不寫 Google Maps 清單、不寫 Sheets、不寫 SQLite，暫存檔跑完即刪。
用法：.\.venv\Scripts\python.exe scripts\smoke_pipeline.py [URL]
退出碼：0 = 全段通過；1 = 有階段失敗（逐段結果印在最後）
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader
from app.services.transcriber import WhisperTranscriber
from app.services.visual_analyzer import VideoVisualAnalyzer
from app.services.place_extractor import PlaceExtractor
from app.services.google_places import GooglePlacesService
from app.services.google_sheets import GoogleSheetsService
from app.services.google_maps_saver import google_maps_saver

DEFAULT_URL = "https://www.instagram.com/reel/DT2w2PVgXo3"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


async def run(url: str) -> dict:
    """跑完所有階段。任何一段炸掉都只讓那一段 FAIL，不中斷後面的階段。

    這支腳本是 CLAUDE.md 訂的驗證關卡，所以它自己不能因為單一階段的例外而失去
    「逐段結果」與「暫存檔清理」——2026-07-25 review 的 HIGH finding 就是這個。
    """
    results: dict[str, bool] = {}
    downloader = InstagramDownloader()
    cleanup_paths: list[Path] = []

    try:
        print(f"[1] 下載 {url}")
        dl = await downloader.download(url)
        results["download"] = dl.success
        print(f"    success={dl.success} caption={len(dl.caption or '')} 字")
        if not dl.success:
            print(f"    error={dl.error_message}")
            return results
        for p in (dl.video_path, dl.audio_path):
            if p:
                cleanup_paths.append(Path(p))

        print("[2] 轉錄")
        transcription = None
        audio_source = dl.audio_path or dl.video_path
        if not audio_source:
            results["transcribe"] = False
            print("    FAIL 下載回報成功但沒有音訊也沒有影片檔")
        else:
            try:
                transcription = await WhisperTranscriber().transcribe(Path(audio_source))
                results["transcribe"] = transcription.success and bool(transcription.transcript)
                print(f"    success={transcription.success} lang={transcription.language} "
                      f"長度={len(transcription.transcript or '')}")
            except Exception as exc:  # noqa: BLE001 — 冒煙測試要看到任何失敗原因
                results["transcribe"] = False
                print(f"    ERROR {type(exc).__name__}: {exc}")

        print("[3] 視覺分析")
        visual = None
        if not dl.video_path:
            # downloader 在影片失敗、音訊成功時仍回 success=True 且 video_path=None
            results["visual"] = False
            print("    FAIL 沒有影片檔可分析（影片下載失敗、僅取得音訊）")
        else:
            try:
                visual = await VideoVisualAnalyzer().analyze(Path(dl.video_path))
                frames = visual.frame_descriptions or []
                results["visual"] = visual.success and len(frames) > 0
                print(f"    success={visual.success} 幀數={len(frames)}")
            except Exception as exc:  # noqa: BLE001
                results["visual"] = False
                print(f"    ERROR {type(exc).__name__}: {exc}")

        print("[4] LLM 擷取地點")
        extraction = None
        try:
            extraction = await PlaceExtractor().extract(
                transcript=(transcription.transcript if transcription else "") or "",
                caption=dl.caption or "",
                visual_description=(visual.overall_visual_summary if visual else "") or "",
            )
            results["extract"] = extraction.found and extraction.place_count > 0
            print(f"    found={extraction.found} 地點數={extraction.place_count}")
            for place in extraction.places:
                print(f"    - {place.name} | {place.city} | conf={place.confidence}")
        except Exception as exc:  # noqa: BLE001
            results["extract"] = False
            print(f"    ERROR {type(exc).__name__}: {exc}")

        print("[5] Google Places 驗證")
        places_ok = False
        try:
            for place in (extraction.places[:2] if extraction else []):
                keywords = place.search_keywords or [place.name]
                search = await GooglePlacesService().search_with_keywords(
                    keywords, expected_name=place.name
                )
                places_ok = places_ok or search.found
                print(f"    {keywords} -> found={search.found} name={search.name} "
                      f"信心={search.match_confidence.value} rating={search.rating} "
                      f"err={search.error_message}")
            if not extraction or not extraction.places:
                print("    SKIP 上一階段沒有地點可查")
        except Exception as exc:  # noqa: BLE001
            print(f"    ERROR {type(exc).__name__}: {exc}")
        results["places_api"] = places_ok

        print("[6] Google Sheets 認證")
        sheets = GoogleSheetsService()
        try:
            worksheet = sheets._get_worksheet()
            results["sheets_auth"] = worksheet is not None
            print(f"    configured={sheets.is_configured()} worksheet={worksheet.title if worksheet else None}")
        except Exception as exc:  # noqa: BLE001
            results["sheets_auth"] = False
            print(f"    ERROR {type(exc).__name__}: {exc}")

        print("[7] Google Maps 登入狀態（唯讀，不寫清單）")
        try:
            lists_result = await google_maps_saver.get_saved_lists()
            results["maps_login"] = lists_result.success
            print(f"    success={lists_result.success} lists={lists_result.lists} "
                  f"err={lists_result.error_message}")
        except Exception as exc:  # noqa: BLE001
            results["maps_login"] = False
            print(f"    ERROR {type(exc).__name__}: {exc}")

        return results
    finally:
        for path in cleanup_paths:
            path.unlink(missing_ok=True)


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    started = time.time()
    results = asyncio.run(run(url))

    print(f"\n===== 冒煙測試結果（{time.time() - started:.0f}s）=====")
    for stage, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {stage}")
    failed = [s for s, ok in results.items() if not ok]
    if failed:
        print(f"\n未通過：{', '.join(failed)}")
        return 1
    print("\n全段通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
