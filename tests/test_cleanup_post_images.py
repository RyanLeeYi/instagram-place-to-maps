"""F23 回歸測試：cleanup_post_images 跨目錄時不得留下空資料夾。

背景：舊版只對 image_paths[0].parent 做 rmdir。一次呼叫若傳入橫跨多個暫存目錄的
圖片路徑（多則貼文合併清理），第一個目錄之外的都會清空卻不刪除，空資料夾一路
累積在 temp_videos/ 底下。只是磁碟垃圾，不影響正確性，所以沒有任何訊號。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.downloader import InstagramDownloader


def _make_dir_with_images(root: Path, name: str, count: int) -> list[Path]:
    d = root / name
    d.mkdir()
    paths = []
    for i in range(count):
        p = d / f"image_{i}.jpg"
        p.write_bytes(b"fake")
        paths.append(p)
    return paths


def test_cleanup_removes_every_emptied_directory(tmp_path):
    """兩個目錄各放數張圖，呼叫後兩個目錄都要不存在。"""
    dir_a = _make_dir_with_images(tmp_path, "post_aaaa1111", 3)
    dir_b = _make_dir_with_images(tmp_path, "threads_bbbb2222", 2)

    asyncio.run(InstagramDownloader().cleanup_post_images(dir_a + dir_b))

    assert not (tmp_path / "post_aaaa1111").exists(), "第一個暫存目錄沒被刪除"
    assert not (tmp_path / "threads_bbbb2222").exists(), "第二個暫存目錄留下來了（F23 原始 bug）"


def test_cleanup_keeps_directory_that_still_has_files(tmp_path):
    """目錄裡還有沒被列入清理的檔案時不得刪除目錄。"""
    paths = _make_dir_with_images(tmp_path, "post_cccc3333", 2)
    (tmp_path / "post_cccc3333" / "keep_me.txt").write_text("x")

    asyncio.run(InstagramDownloader().cleanup_post_images(paths))

    assert (tmp_path / "post_cccc3333").exists()
    assert (tmp_path / "post_cccc3333" / "keep_me.txt").exists()


def test_cleanup_tolerates_empty_and_missing_paths(tmp_path):
    """空清單與已不存在的檔案都不得擲例外。"""
    asyncio.run(InstagramDownloader().cleanup_post_images([]))
    asyncio.run(InstagramDownloader().cleanup_post_images([tmp_path / "gone" / "nope.jpg"]))
