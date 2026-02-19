"""Instagram / Threads 下載服務"""

import asyncio
import html as html_lib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, List, Tuple, Dict

import httpx
import yt_dlp
import instaloader

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """下載結果"""

    success: bool
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    title: Optional[str] = None
    caption: Optional[str] = None  # 影片說明文
    error_message: Optional[str] = None


@dataclass
class PostDownloadResult:
    """貼文下載結果"""

    success: bool
    content_type: str = "post"  # "post_image", "post_carousel", "reel", "text_only"
    image_paths: List[Path] = field(default_factory=list)
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    error_message: Optional[str] = None


class ThreadsContentType(Enum):
    """Threads 貼文內容類型"""
    VIDEO = "video"
    IMAGE = "image"
    CAROUSEL = "carousel"
    MIXED = "mixed"  # 串文中同時包含圖片和影片
    TEXT_ONLY = "text_only"
    UNKNOWN = "unknown"


class InstagramDownloader:
    """Instagram / Threads 下載器"""

    # 支援的 Instagram URL 格式
    INSTAGRAM_URL_PATTERNS = [
        r"https?://(?:www\.)?instagram\.com/reel/([A-Za-z0-9_-]+)",
        r"https?://(?:www\.)?instagram\.com/p/([A-Za-z0-9_-]+)",
        r"https?://(?:www\.)?instagram\.com/reels/([A-Za-z0-9_-]+)",
    ]
    
    # Reel 專用 pattern（用於區分內容類型）
    REEL_PATTERNS = [
        r"https?://(?:www\.)?instagram\.com/reel/([A-Za-z0-9_-]+)",
        r"https?://(?:www\.)?instagram\.com/reels/([A-Za-z0-9_-]+)",
    ]
    
    # 支援的 Threads URL 格式（支援 threads.net 和 threads.com）
    THREADS_URL_PATTERNS = [
        r"https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/post/([A-Za-z0-9_-]+)",
        r"https?://(?:www\.)?threads\.(?:net|com)/t/([A-Za-z0-9_-]+)",
    ]
    
    # Threads 預設分享圖 URL 特徵（用於判斷是否為實際圖片）
    THREADS_DEFAULT_IMAGE_PATTERNS = [
        "static.cdninstagram.com",
        "scontent.cdninstagram.com",
        "threads-logo",
        "threads_icon",
    ]
    
    # 嘗試的瀏覽器順序
    BROWSERS_TO_TRY = ["chrome", "edge", "firefox", "brave", "opera", "chromium"]
    
    # cookies 檔案路徑
    COOKIES_FILE = Path("cookies.txt")

    def __init__(self):
        self.temp_dir = settings.temp_video_path
        self.session_dir = settings.instaloader_session_path
        self._working_browser: Optional[str] = None
        self._cookies_file: Optional[Path] = self._find_cookies_file()
        self._instaloader: Optional[instaloader.Instaloader] = None
        self._instaloader_username: Optional[str] = None
    
    def _find_cookies_file(self) -> Optional[Path]:
        """尋找 cookies.txt 檔案"""
        if self.COOKIES_FILE.exists():
            logger.info(f"✅ 找到 Instagram cookies 檔案: {self.COOKIES_FILE.absolute()}")
            return self.COOKIES_FILE
        return None
    
    def _get_cookies_path_for_url(self, url: str) -> Optional[Path]:
        """根據 URL 回傳對應的 cookies 檔案路徑"""
        return self._cookies_file

    def _load_cookies_from_netscape(self, cookie_file: Path) -> dict:
        """
        從 Netscape 格式的 cookies.txt 解析 cookies
        
        Args:
            cookie_file: cookies.txt 檔案路徑
            
        Returns:
            dict: cookie 名稱與值的字典
        """
        cookies = {}
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳過註解和空行
                    if line.startswith('#') or not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain = parts[0]
                        cookie_name = parts[5]
                        cookie_value = parts[6]
                        # 只取 Instagram 相關的 cookies
                        if 'instagram.com' in domain:
                            cookies[cookie_name] = cookie_value
            logger.info(f"從 cookies.txt 解析到 {len(cookies)} 個 Instagram cookies")
        except Exception as e:
            logger.error(f"解析 cookies.txt 失敗: {e}")
        return cookies

    def _get_instaloader(self) -> instaloader.Instaloader:
        """
        取得已認證的 Instaloader 實例（含 session 快取）
        
        Returns:
            instaloader.Instaloader: 已認證的實例
        """
        if self._instaloader is not None:
            return self._instaloader
        
        L = instaloader.Instaloader(
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            max_connection_attempts=3,
        )
        
        # 嘗試載入已存在的 session 檔案
        session_files = list(self.session_dir.glob("session-*"))
        for session_file in session_files:
            try:
                username = session_file.name.replace("session-", "")
                L.load_session_from_file(username, str(session_file))
                # 驗證 session 是否有效
                test_user = L.test_login()
                if test_user:
                    self._instaloader = L
                    self._instaloader_username = test_user
                    logger.info(f"✅ 成功載入 session: {test_user}")
                    return L
            except Exception as e:
                logger.debug(f"載入 session {session_file} 失敗: {e}")
                continue
        
        # 沒有可用的 session，從 cookies.txt 建立新的
        if self._cookies_file:
            try:
                cookies = self._load_cookies_from_netscape(self._cookies_file)
                if cookies:
                    # 使用 requests 的方式正確注入 cookies（設定 domain）
                    import requests
                    for name, value in cookies.items():
                        L.context._session.cookies.set(
                            name, value, domain=".instagram.com"
                        )
                    
                    # 驗證登入狀態
                    try:
                        test_user = L.test_login()
                        if test_user:
                            self._instaloader = L
                            self._instaloader_username = test_user
                            
                            # 儲存 session 供後續使用
                            session_path = self.session_dir / f"session-{test_user}"
                            L.save_session_to_file(str(session_path))
                            logger.info(f"✅ 從 cookies.txt 建立 session 並儲存: {test_user}")
                            return L
                        else:
                            logger.warning("⚠️ cookies.txt 認證失敗，session 無效")
                    except instaloader.exceptions.ConnectionException as ce:
                        logger.warning(f"⚠️ 連線驗證失敗（可能仍可使用）: {ce}")
                        # 即使驗證失敗，仍設定 cookies 並嘗試使用
                        self._instaloader = L
                        return L
            except Exception as e:
                logger.error(f"從 cookies.txt 建立 session 失敗: {e}")
        
        # 無法認證，回傳未認證的實例（可能只能存取公開內容）
        logger.warning("⚠️ Instaloader 未認證，僅能存取公開內容")
        self._instaloader = L
        return L

    def is_reel_url(self, url: str) -> bool:
        """判斷 URL 是否為 Reel（影片）"""
        for pattern in self.REEL_PATTERNS:
            if re.match(pattern, url):
                return True
        return False
    
    def is_threads_url(self, url: str) -> bool:
        """判斷 URL 是否為 Threads 連結"""
        for pattern in self.THREADS_URL_PATTERNS:
            if re.match(pattern, url):
                return True
        return False

    def validate_url(self, url: str) -> bool:
        """驗證是否為有效的 Instagram 或 Threads 連結"""
        for pattern in self.INSTAGRAM_URL_PATTERNS:
            if re.match(pattern, url):
                return True
        for pattern in self.THREADS_URL_PATTERNS:
            if re.match(pattern, url):
                return True
        return False

    def extract_post_id(self, url: str) -> Optional[str]:
        """從 URL 提取貼文 ID"""
        for pattern in self.INSTAGRAM_URL_PATTERNS + self.THREADS_URL_PATTERNS:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def download(self, url: str) -> DownloadResult:
        """
        下載 Instagram Reels 影片

        Args:
            url: Instagram Reels 連結

        Returns:
            DownloadResult: 下載結果
        """
        if not self.validate_url(url):
            return DownloadResult(
                success=False,
                error_message="無法解析此連結，請確認是否為有效的 Instagram 或 Threads 連結",
            )

        # 生成唯一檔名
        file_id = str(uuid.uuid4())[:8]
        output_template = str(self.temp_dir / f"{file_id}")

        # 先下載影片（供視覺分析用）
        video_ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": output_template + "_video.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        # 下載音訊
        audio_ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template + ".%(ext)s",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        
        # 優先使用對應平台的 cookies.txt 檔案
        cookies_path = self._get_cookies_path_for_url(url)
        if cookies_path:
            video_ydl_opts["cookiefile"] = str(cookies_path)
            audio_ydl_opts["cookiefile"] = str(cookies_path)
            platform = "Threads" if self.is_threads_url(url) else "Instagram"
            logger.info(f"使用 {platform} cookies.txt 進行下載")
        elif self._working_browser:
            # 備用：使用瀏覽器 cookies
            video_ydl_opts["cookiesfrombrowser"] = (self._working_browser,)
            audio_ydl_opts["cookiesfrombrowser"] = (self._working_browser,)
            logger.info(f"使用 {self._working_browser} 的 cookies 進行下載")

        try:
            # 在執行緒池中執行下載（yt-dlp 是同步的）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._download_sync, url, audio_ydl_opts, video_ydl_opts
            )
            return result

        except Exception as e:
            logger.error(f"下載影片失敗: {e}")
            return DownloadResult(
                success=False,
                error_message=f"下載失敗: {str(e)}",
            )

    def _download_sync(self, url: str, audio_ydl_opts: dict, video_ydl_opts: dict = None) -> DownloadResult:
        """同步下載方法"""
        
        # 如果沒有 cookies 檔案且還沒找到可用的瀏覽器，嘗試各個瀏覽器
        if not self._cookies_file and not self._working_browser:
            for browser in self.BROWSERS_TO_TRY:
                try:
                    test_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "extract_flat": True,
                        "cookiesfrombrowser": (browser,),
                    }
                    with yt_dlp.YoutubeDL(test_opts) as ydl:
                        # 測試是否能取得影片資訊
                        info = ydl.extract_info(url, download=False)
                        if info:
                            self._working_browser = browser
                            logger.info(f"✅ 使用 {browser} 的 cookies 成功")
                            # 更新下載選項
                            video_ydl_opts["cookiesfrombrowser"] = (browser,)
                            audio_ydl_opts["cookiesfrombrowser"] = (browser,)
                            break
                except Exception as e:
                    logger.debug(f"{browser} 無法使用: {e}")
                    continue
            
            if not self._working_browser:
                logger.warning("⚠️ 無法從任何瀏覽器取得 cookies，請提供 cookies.txt 檔案")
        
        try:
            video_path = None
            
            # 先下載影片（如果提供了 video_ydl_opts）
            if video_ydl_opts:
                try:
                    with yt_dlp.YoutubeDL(video_ydl_opts) as ydl:
                        ydl.download([url])
                        
                    # 找到下載的影片檔案
                    video_template = video_ydl_opts["outtmpl"]
                    if isinstance(video_template, dict):
                        video_template = video_template.get("default", "")
                    video_base = video_template.rsplit(".", 1)[0] if "." in video_template else video_template
                    
                    for ext in ["mp4", "webm", "mkv"]:
                        vpath = Path(f"{video_base}.{ext}")
                        if vpath.exists():
                            video_path = vpath
                            logger.info(f"成功下載影片: {video_path}")
                            break
                except Exception as e:
                    logger.warning(f"影片下載失敗，將只進行音訊分析: {e}")
            
            # 下載音訊
            with yt_dlp.YoutubeDL(audio_ydl_opts) as ydl:
                # 取得影片資訊
                info = ydl.extract_info(url, download=True)

                if info is None:
                    return DownloadResult(
                        success=False,
                        error_message="無法取得影片資訊",
                    )

                title = info.get("title", "未知標題")

                # 找到下載的音訊檔案
                output_template = audio_ydl_opts["outtmpl"]
                # 處理 outtmpl 可能是字典或字串的情況
                if isinstance(output_template, dict):
                    output_template = output_template.get("default", "")
                base_path = output_template.rsplit(".", 1)[0] if "." in output_template else output_template
                audio_path = Path(f"{base_path}.mp3")

                if not audio_path.exists():
                    # 嘗試其他可能的副檔名
                    for ext in ["m4a", "webm", "opus"]:
                        alt_path = Path(f"{base_path}.{ext}")
                        if alt_path.exists():
                            audio_path = alt_path
                            break

                if not audio_path.exists():
                    return DownloadResult(
                        success=False,
                        error_message="無法找到下載的音訊檔案",
                    )

                # 取得影片說明文（caption/description）
                caption = info.get("description", "")
                if caption:
                    logger.info(f"取得影片說明文，長度: {len(caption)} 字元")

                logger.info(f"成功下載影片: {title}")
                return DownloadResult(
                    success=True,
                    video_path=video_path,
                    audio_path=audio_path,
                    title=title,
                    caption=caption,
                )

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if "Private" in error_msg or "private" in error_msg:
                return DownloadResult(
                    success=False,
                    error_message="此影片為私人影片，無法下載",
                )
            elif "not available" in error_msg.lower():
                return DownloadResult(
                    success=False,
                    error_message="此影片已不存在或無法存取",
                )
            else:
                return DownloadResult(
                    success=False,
                    error_message=f"下載失敗: {error_msg}",
                )

        except Exception as e:
            return DownloadResult(
                success=False,
                error_message=f"下載時發生錯誤: {str(e)}",
            )

    async def cleanup(self, file_path: Path) -> None:
        """清理暫存檔案"""
        try:
            if file_path and file_path.exists():
                file_path.unlink()
                logger.info(f"已刪除暫存檔案: {file_path}")
        except Exception as e:
            logger.warning(f"刪除暫存檔案失敗: {e}")

    async def download_post(self, url: str) -> PostDownloadResult:
        """
        下載 Instagram 貼文（圖片 + 說明文字）
        
        Args:
            url: Instagram 貼文連結
            
        Returns:
            PostDownloadResult: 下載結果
        """
        if not self.validate_url(url):
            return PostDownloadResult(
                success=False,
                error_message="無法解析此連結，請確認是否為有效的 Instagram 連結",
            )
        
        shortcode = self.extract_post_id(url)
        if not shortcode:
            return PostDownloadResult(
                success=False,
                error_message="無法從連結提取貼文 ID",
            )
        
        # 在執行緒池中執行（instaloader 是同步的）
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, self._download_post_sync, shortcode
            )
            return result
        except Exception as e:
            logger.error(f"下載貼文失敗: {e}")
            return PostDownloadResult(
                success=False,
                error_message=f"下載失敗: {str(e)}",
            )

    def _download_post_sync(self, shortcode: str) -> PostDownloadResult:
        """同步下載貼文方法"""
        try:
            L = self._get_instaloader()
            
            # 取得貼文資訊
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            
            # 取得貼文說明
            caption = post.caption or ""
            title = post.title or f"Instagram 貼文 by {post.owner_username}"
            
            # 建立下載目錄
            file_id = str(uuid.uuid4())[:8]
            post_dir = self.temp_dir / f"post_{file_id}"
            post_dir.mkdir(parents=True, exist_ok=True)
            
            image_paths: List[Path] = []
            
            # 判斷是否為輪播圖（carousel）
            if post.typename == "GraphSidecar":
                # 輪播圖：下載所有圖片
                content_type = "post_carousel"
                for idx, node in enumerate(post.get_sidecar_nodes(), 1):
                    if node.is_video:
                        # 跳過影片（只處理圖片）
                        logger.debug(f"跳過輪播中的影片: 第 {idx} 張")
                        continue
                    
                    image_url = node.display_url
                    image_path = post_dir / f"image_{idx:02d}.jpg"
                    
                    # 下載圖片（需轉換為字串路徑）
                    L.context.get_and_write_raw(image_url, str(image_path))
                    image_paths.append(image_path)
                    logger.info(f"下載輪播圖片 {idx}: {image_path}")
                    
            elif post.typename == "GraphImage":
                # 單張圖片
                content_type = "post_image"
                image_url = post.url
                image_path = post_dir / "image_01.jpg"
                
                # 下載圖片（需轉換為字串路徑）
                L.context.get_and_write_raw(image_url, str(image_path))
                image_paths.append(image_path)
                logger.info(f"下載單張圖片: {image_path}")
                
            elif post.typename == "GraphVideo":
                # 這是影片貼文，應該用 download() 方法處理
                return PostDownloadResult(
                    success=False,
                    content_type="reel",
                    error_message="此貼文為影片，請使用影片處理流程",
                )
            else:
                return PostDownloadResult(
                    success=False,
                    error_message=f"不支援的貼文類型: {post.typename}",
                )
            
            if not image_paths:
                return PostDownloadResult(
                    success=False,
                    error_message="無法下載任何圖片",
                )
            
            logger.info(f"成功下載貼文: {title}，共 {len(image_paths)} 張圖片")
            
            return PostDownloadResult(
                success=True,
                content_type=content_type,
                image_paths=image_paths,
                caption=caption,
                title=title,
            )
            
        except instaloader.exceptions.ProfileNotExistsException:
            return PostDownloadResult(
                success=False,
                error_message="找不到此帳號",
            )
        except instaloader.exceptions.PrivateProfileNotFollowedException:
            return PostDownloadResult(
                success=False,
                error_message="此帳號為私人帳號，無法存取",
            )
        except instaloader.exceptions.LoginRequiredException:
            return PostDownloadResult(
                success=False,
                error_message="需要登入才能存取此內容，請確認 cookies.txt 是否有效",
            )
        except instaloader.exceptions.PostChangedException as e:
            return PostDownloadResult(
                success=False,
                error_message=f"貼文已被修改或刪除: {e}",
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"下載貼文失敗: {error_msg}")
            return PostDownloadResult(
                success=False,
                error_message=f"下載失敗: {error_msg}",
            )

    async def cleanup_post_images(self, image_paths: List[Path]) -> None:
        """清理貼文圖片暫存檔案"""
        for image_path in image_paths:
            try:
                if image_path and image_path.exists():
                    image_path.unlink()
                    logger.debug(f"已刪除暫存圖片: {image_path}")
            except Exception as e:
                logger.warning(f"刪除暫存圖片失敗: {e}")
        
        # 嘗試刪除目錄
        if image_paths:
            try:
                parent_dir = image_paths[0].parent
                if parent_dir.exists() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
                    logger.debug(f"已刪除暫存目錄: {parent_dir}")
            except Exception as e:
                logger.debug(f"刪除暫存目錄失敗: {e}")

    # ============================
    # Threads 專用方法
    # ============================

    # Googlebot UA：Threads 會為此 UA 回傳伺服器端渲染 HTML（含 data-sjs JSON）
    _GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

    async def detect_threads_content_type(self, url: str) -> Tuple[ThreadsContentType, Dict]:
        """
        偵測 Threads 貼文內容類型

        使用 Googlebot UA 請求頁面，解析嵌入的 data-sjs JSON 取得貼文結構化資料。
        Threads 是 React SPA，一般 UA 不會回傳 og: tags 或嵌入資料。
        只有 Googlebot UA 會觸發伺服器端渲染。

        Args:
            url: Threads 貼文連結

        Returns:
            (ThreadsContentType, metadata_dict)
        """
        metadata: Dict = {
            "description": "",
            "image_urls": [],
            "video_urls": [],
            "author": None,
            "carousel_items": [],
            "media_type": None,
        }

        try:
            post_data = await self._extract_threads_post_data(url)
            if not post_data:
                logger.warning(f"無法從 Threads HTML 提取貼文資料: {url}")
                return ThreadsContentType.UNKNOWN, metadata

            # 轉換 post_data 為 metadata 格式
            metadata["description"] = post_data.get("description", "")
            metadata["image_urls"] = post_data.get("image_urls", [])
            metadata["video_urls"] = post_data.get("video_urls", [])
            metadata["author"] = post_data.get("author", "")
            metadata["carousel_items"] = post_data.get("carousel_items", [])
            metadata["media_type"] = post_data.get("media_type")
            metadata["thread_items_count"] = post_data.get("thread_items_count", 1)

            media_type = post_data.get("media_type")
            thread_count = metadata["thread_items_count"]
            has_images = bool(metadata["image_urls"])
            has_videos = bool(metadata["video_urls"])

            # 串文包含多種媒體類型 → MIXED
            if thread_count > 1 and has_images and has_videos:
                content_type = ThreadsContentType.MIXED
                logger.info(
                    f"串文包含混合媒體 ({thread_count} 篇): "
                    f"{len(metadata['image_urls'])} 張圖片, "
                    f"{len(metadata['video_urls'])} 個影片"
                )
            # Meta media_type 值：1=圖片, 2=影片, 8=輪播, 19=文字貼文
            elif media_type == 2 or (not has_images and has_videos):
                content_type = ThreadsContentType.VIDEO
            elif media_type == 8:
                content_type = ThreadsContentType.CAROUSEL
                if has_videos:
                    logger.info(f"輪播貼文包含 {len(metadata['video_urls'])} 個影片")
            elif media_type == 1 or (has_images and not has_videos):
                content_type = ThreadsContentType.IMAGE
            elif media_type == 19:
                # 文字貼文可能附帶內嵌媒體
                if has_videos:
                    content_type = ThreadsContentType.VIDEO
                elif has_images:
                    content_type = ThreadsContentType.IMAGE
                else:
                    content_type = ThreadsContentType.TEXT_ONLY
            else:
                # 未知 media_type，嘗試從可用媒體推斷
                if has_videos:
                    content_type = ThreadsContentType.VIDEO
                elif has_images:
                    content_type = ThreadsContentType.IMAGE
                elif metadata["description"]:
                    content_type = ThreadsContentType.TEXT_ONLY
                else:
                    content_type = ThreadsContentType.UNKNOWN

            logger.info(
                f"Threads 內容類型: {content_type.value} "
                f"(media_type={media_type}, thread_items={thread_count}, "
                f"images={len(metadata['image_urls'])}, "
                f"videos={len(metadata['video_urls'])})"
            )
            return content_type, metadata

        except Exception as e:
            logger.error(f"Threads 內容類型偵測失敗: {e}")
            return ThreadsContentType.UNKNOWN, metadata

    async def _extract_threads_post_data(self, url: str) -> Optional[Dict[str, Any]]:
        """
        使用 Googlebot UA 取得 Threads 頁面，解析嵌入的 data-sjs JSON。

        Threads（Meta SPA）對 Googlebot 會回傳伺服器端渲染的 HTML，
        其中 <script> 標籤含有完整的貼文資料（JSON 格式）。

        Args:
            url: Threads 貼文連結

        Returns:
            結構化貼文資料 dict，或 None
        """
        headers = {
            "User-Agent": self._GOOGLEBOT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html_text = resp.text

        logger.info(f"Threads HTML 大小: {len(html_text)} bytes")

        # 搜尋所有 <script> 區塊，找含有 thread_items 的 JSON
        all_scripts = re.findall(r"<script[^>]*>(.*?)</script>", html_text, re.DOTALL)

        for block in all_scripts:
            decoded = html_lib.unescape(block)
            if "thread_items" not in decoded or "media_type" not in decoded:
                continue
            if len(decoded) < 5000:
                continue

            try:
                data = json.loads(decoded)
            except json.JSONDecodeError:
                continue

            node = self._find_thread_node(data)
            if node:
                return self._extract_from_thread_node(node)

        logger.warning("在 Threads HTML 中找不到貼文資料")
        return None

    def _find_thread_node(self, obj: Any, depth: int = 0) -> Optional[Dict]:
        """
        在 Meta 的 require JSON 結構中遞迴搜尋含有 thread_items 的節點。

        路徑通常為：
        require[0][3][0].__bbox.require[0][3][1].__bbox.result.data.data.edges[0].node
        """
        if depth > 20:
            return None

        if isinstance(obj, dict):
            if "thread_items" in obj:
                items = obj["thread_items"]
                if isinstance(items, list) and len(items) > 0:
                    post = items[0].get("post", {})
                    if isinstance(post, dict) and "media_type" in post:
                        return obj

            for val in obj.values():
                result = self._find_thread_node(val, depth + 1)
                if result:
                    return result

        elif isinstance(obj, list):
            for item in obj[:10]:
                result = self._find_thread_node(item, depth + 1)
                if result:
                    return result

        return None

    def _extract_from_thread_node(self, node: Dict) -> Dict[str, Any]:
        """
        從 thread node 提取結構化貼文資料（支援串文 thread chain）。

        遍歷所有 thread_items，過濾同一作者的貼文，
        合併 caption 文字、聚合所有媒體 URL。
        """
        thread_items = node.get("thread_items", [])
        if not thread_items:
            return {
                "media_type": None,
                "caption": None,
                "description": None,
                "author": None,
                "image_urls": [],
                "video_urls": [],
                "carousel_items": [],
                "thread_items_count": 0,
            }

        # 取得原作者 username（以第一個 item 為準）
        first_post = thread_items[0].get("post", {})
        first_user = first_post.get("user", {})
        author_username = (
            first_user.get("username", "") if isinstance(first_user, dict) else ""
        )

        # 過濾同一作者的 items
        author_items = []
        for item in thread_items:
            post = item.get("post", {})
            user = post.get("user", {})
            username = user.get("username", "") if isinstance(user, dict) else ""
            if username == author_username:
                author_items.append(item)
            else:
                logger.debug(f"跳過非原作者的 thread item (user={username})")

        total = len(author_items)
        logger.info(
            f"串文共 {len(thread_items)} 個 items，"
            f"同作者 {total} 個 (author={author_username})"
        )

        result: Dict[str, Any] = {
            "media_type": first_post.get("media_type"),
            "caption": None,
            "description": None,
            "author": author_username,
            "image_urls": [],
            "video_urls": [],
            "carousel_items": [],
            "thread_items_count": total,
        }

        caption_parts: List[str] = []
        description_parts: List[str] = []

        for idx, item in enumerate(author_items):
            post = item.get("post", {})
            item_caption = self._extract_item_caption(post)
            item_description = self._extract_item_description(post)

            if item_caption:
                caption_parts.append(item_caption)
            if item_description:
                description_parts.append(item_description)

            # 根據此 item 的 media_type 提取媒體
            media_type = post.get("media_type")
            text_info = post.get("text_post_app_info", {})

            if media_type == 8:
                self._extract_carousel_media(post, result)
            elif media_type == 2:
                self._extract_video_media(post, result)
            elif media_type == 1:
                self._extract_image_media(post, result)
            elif media_type == 19:
                self._extract_text_post_media(post, text_info, result)
            else:
                self._extract_image_media(post, result)
                self._extract_video_media(post, result)

        # 合併 caption（多篇用編號 + 分隔線）
        if total > 1 and len(caption_parts) > 1:
            numbered = [
                f"[{i + 1}/{total}] {text}"
                for i, text in enumerate(caption_parts)
            ]
            result["caption"] = "\n---\n".join(numbered)
        elif caption_parts:
            result["caption"] = caption_parts[0]

        # 合併 description
        if total > 1 and len(description_parts) > 1:
            numbered = [
                f"[{i + 1}/{total}] {text}"
                for i, text in enumerate(description_parts)
            ]
            result["description"] = "\n---\n".join(numbered)
        elif description_parts:
            result["description"] = description_parts[0]

        # description 回退
        if not result["description"] and result["caption"]:
            result["description"] = result["caption"]

        return result

    @staticmethod
    def _extract_item_caption(post: Dict) -> Optional[str]:
        """從單一 thread item 的 post 提取 caption 文字"""
        caption = post.get("caption")
        if isinstance(caption, dict):
            return caption.get("text", "") or None
        if isinstance(caption, str) and caption:
            return caption
        return None

    @staticmethod
    def _extract_item_description(post: Dict) -> Optional[str]:
        """從單一 thread item 的 post 提取 text fragments 描述"""
        text_info = post.get("text_post_app_info", {})
        if not isinstance(text_info, dict):
            return None
        frags = text_info.get("text_fragments", {})
        if not isinstance(frags, dict):
            return None
        fragments = frags.get("fragments", [])
        texts = [
            f.get("plaintext", f.get("text", ""))
            for f in fragments
            if isinstance(f, dict)
        ]
        joined = " ".join(t for t in texts if t)
        return joined or None

    def _extract_carousel_media(self, post: Dict, result: Dict) -> None:
        """提取輪播（carousel）媒體"""
        carousel = post.get("carousel_media", [])
        for item in carousel:
            media_item: Dict[str, Any] = {"type": "image", "url": None, "video_url": None}

            img_versions = item.get("image_versions2", {})
            candidates = (
                img_versions.get("candidates", [])
                if isinstance(img_versions, dict)
                else []
            )
            if candidates:
                media_item["url"] = candidates[0].get("url")

            video_versions = item.get("video_versions", [])
            if video_versions:
                media_item["type"] = "video"
                media_item["video_url"] = video_versions[0].get("url")

            result["carousel_items"].append(media_item)
            if media_item["url"]:
                result["image_urls"].append(media_item["url"])
            if media_item.get("video_url"):
                result["video_urls"].append(media_item["video_url"])

    def _extract_video_media(self, post: Dict, result: Dict) -> None:
        """提取影片媒體"""
        video_versions = post.get("video_versions", [])
        if video_versions:
            url = video_versions[0].get("url")
            if url and url not in result["video_urls"]:
                result["video_urls"].append(url)

        # 影片縮圖
        img_versions = post.get("image_versions2", {})
        candidates = (
            img_versions.get("candidates", [])
            if isinstance(img_versions, dict)
            else []
        )
        if candidates:
            url = candidates[0].get("url")
            if url and url not in result["image_urls"]:
                result["image_urls"].append(url)

    def _extract_image_media(self, post: Dict, result: Dict) -> None:
        """提取圖片媒體"""
        img_versions = post.get("image_versions2", {})
        candidates = (
            img_versions.get("candidates", [])
            if isinstance(img_versions, dict)
            else []
        )
        if candidates:
            url = candidates[0].get("url")
            if url and url not in result["image_urls"]:
                result["image_urls"].append(url)

    def _extract_text_post_media(
        self, post: Dict, text_info: Any, result: Dict
    ) -> None:
        """提取文字貼文附帶的內嵌媒體"""
        if not isinstance(text_info, dict):
            return

        linked = text_info.get("linked_inline_media", {})
        if not isinstance(linked, dict) or not linked:
            return

        # 內嵌影片
        video_versions = linked.get("video_versions", [])
        if video_versions:
            url = video_versions[0].get("url")
            if url:
                result["video_urls"].append(url)

        # 內嵌圖片
        img_versions = linked.get("image_versions2", {})
        candidates = (
            img_versions.get("candidates", [])
            if isinstance(img_versions, dict)
            else []
        )
        if candidates:
            url = candidates[0].get("url")
            if url:
                result["image_urls"].append(url)


    async def download_threads_post(self, url: str) -> PostDownloadResult:
        """
        下載 Threads 貼文（自動偵測內容類型：影片、圖片、輪播、純文字）

        直接從 CDN URL 下載媒體，不依賴 yt-dlp（yt-dlp 不支援 threads.com）。

        Args:
            url: Threads 貼文連結

        Returns:
            PostDownloadResult: 下載結果
        """
        if not self.is_threads_url(url):
            return PostDownloadResult(
                success=False,
                error_message="無法解析此連結，請確認是否為有效的 Threads 連結",
            )

        # 偵測內容類型
        content_type, metadata = await self.detect_threads_content_type(url)
        logger.info(
            f"Threads 貼文類型: {content_type.value}, "
            f"metadata keys: {list(metadata.keys())}"
        )

        description = metadata.get("description", "")
        author = metadata.get("author", "")

        if content_type == ThreadsContentType.VIDEO:
            return await self._download_threads_video(metadata, description, author)

        elif content_type in (ThreadsContentType.IMAGE, ThreadsContentType.CAROUSEL):
            return await self._download_threads_images(
                metadata, content_type, description, author
            )

        elif content_type == ThreadsContentType.MIXED:
            return await self._download_threads_mixed(
                metadata, description, author
            )

        elif content_type == ThreadsContentType.TEXT_ONLY:
            if not description:
                return PostDownloadResult(
                    success=False,
                    error_message="此 Threads 貼文無文字內容",
                )
            return PostDownloadResult(
                success=True,
                content_type="text_only",
                caption=description,
                title=author,
            )

        else:
            return PostDownloadResult(
                success=False,
                error_message="無法辨識此 Threads 貼文的內容類型，可能需要登入或貼文已被刪除",
            )

    async def _download_threads_video(
        self, metadata: Dict, description: str, author: str
    ) -> PostDownloadResult:
        """下載 Threads 影片（從 CDN URL 直接下載）"""
        video_urls = metadata.get("video_urls", [])
        if not video_urls:
            return PostDownloadResult(
                success=False,
                content_type="reel",
                error_message="偵測到影片貼文但無法取得影片 URL",
            )

        file_id = str(uuid.uuid4())[:8]
        post_dir = self.temp_dir / f"threads_{file_id}"
        post_dir.mkdir(parents=True, exist_ok=True)

        video_path = post_dir / "video.mp4"

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=60.0
            ) as client:
                resp = await client.get(video_urls[0])
                resp.raise_for_status()
                video_path.write_bytes(resp.content)
                logger.info(
                    f"下載 Threads 影片: {video_path} "
                    f"({len(resp.content)} bytes)"
                )
        except Exception as e:
            logger.error(f"Threads 影片下載失敗: {e}")
            return PostDownloadResult(
                success=False,
                content_type="reel",
                error_message=f"影片下載失敗: {e}",
            )

        return PostDownloadResult(
            success=True,
            content_type="reel",
            video_path=video_path,
            audio_path=video_path,  # faster-whisper 可直接處理影片檔
            caption=description,
            title=author,
        )

    async def _download_threads_images(
        self,
        metadata: Dict,
        content_type: ThreadsContentType,
        description: str,
        author: str,
    ) -> PostDownloadResult:
        """下載 Threads 圖片/輪播圖片"""
        image_urls = metadata.get("image_urls", [])
        if not image_urls:
            return PostDownloadResult(
                success=False,
                error_message="偵測到圖片貼文但無法取得圖片 URL",
            )

        image_paths = await self._download_thread_images(image_urls)
        if not image_paths:
            return PostDownloadResult(
                success=False,
                error_message="圖片下載失敗",
            )

        result_type = "post_carousel" if len(image_paths) > 1 else "post_image"
        return PostDownloadResult(
            success=True,
            content_type=result_type,
            image_paths=image_paths,
            caption=description,
            title=author,
        )

    async def _download_threads_mixed(
        self,
        metadata: Dict,
        description: str,
        author: str,
    ) -> PostDownloadResult:
        """
        下載 Threads 串文混合媒體（同時包含圖片和影片）。

        下載所有圖片 + 第一個影片，回傳 thread_mixed 類型的結果。
        """
        image_urls = metadata.get("image_urls", [])
        video_urls = metadata.get("video_urls", [])
        thread_count = metadata.get("thread_items_count", 1)

        logger.info(
            f"下載 Threads 串文混合媒體 ({thread_count} 篇): "
            f"{len(image_urls)} 張圖片, {len(video_urls)} 個影片"
        )

        # 下載圖片
        image_paths: List[Path] = []
        if image_urls:
            image_paths = await self._download_thread_images(image_urls)

        # 下載第一個影片
        video_path: Optional[Path] = None
        if video_urls:
            file_id = str(uuid.uuid4())[:8]
            post_dir = self.temp_dir / f"threads_{file_id}"
            post_dir.mkdir(parents=True, exist_ok=True)
            video_path = post_dir / "video.mp4"

            try:
                async with httpx.AsyncClient(
                    follow_redirects=True, timeout=60.0
                ) as client:
                    resp = await client.get(video_urls[0])
                    resp.raise_for_status()
                    video_path.write_bytes(resp.content)
                    logger.info(
                        f"下載 Threads 串文影片: {video_path} "
                        f"({len(resp.content)} bytes)"
                    )
            except Exception as e:
                logger.warning(f"Threads 串文影片下載失敗: {e}")
                video_path = None

        if not image_paths and not video_path:
            return PostDownloadResult(
                success=False,
                error_message="串文混合媒體下載失敗：圖片和影片皆無法下載",
            )

        return PostDownloadResult(
            success=True,
            content_type="thread_mixed",
            image_paths=image_paths,
            video_path=video_path,
            audio_path=video_path,  # faster-whisper 可直接處理影片檔
            caption=description,
            title=author,
        )

    async def _download_thread_images(self, image_urls: List[str]) -> List[Path]:
        """
        下載 Threads 貼文圖片

        Args:
            image_urls: 圖片 URL 列表

        Returns:
            List[Path]: 下載後的圖片檔案路徑
        """
        file_id = str(uuid.uuid4())[:8]
        post_dir = self.temp_dir / f"threads_{file_id}"
        post_dir.mkdir(parents=True, exist_ok=True)

        image_paths: List[Path] = []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            for idx, img_url in enumerate(image_urls[:10], 1):  # 最多下載 10 張
                try:
                    resp = await client.get(img_url)
                    resp.raise_for_status()

                    # 判斷副檔名
                    content_type_header = resp.headers.get("content-type", "")
                    ext = "jpg"
                    if "png" in content_type_header:
                        ext = "png"
                    elif "webp" in content_type_header:
                        ext = "webp"

                    image_path = post_dir / f"image_{idx:02d}.{ext}"
                    image_path.write_bytes(resp.content)
                    image_paths.append(image_path)
                    logger.info(f"下載 Threads 圖片 {idx}: {image_path}")
                except Exception as e:
                    logger.warning(f"下載 Threads 圖片 {idx} 失敗: {e}")

        return image_paths
