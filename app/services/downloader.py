"""Instagram Reels 下載服務"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

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
    content_type: str = "post"  # "post_image", "post_carousel"
    image_paths: List[Path] = field(default_factory=list)
    caption: Optional[str] = None
    title: Optional[str] = None
    error_message: Optional[str] = None


class InstagramDownloader:
    """Instagram Reels 下載器"""

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
            logger.info(f"✅ 找到 cookies 檔案: {self.COOKIES_FILE.absolute()}")
            return self.COOKIES_FILE
        return None

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

    def validate_url(self, url: str) -> bool:
        """驗證是否為有效的 Instagram Reels 連結"""
        for pattern in self.INSTAGRAM_URL_PATTERNS:
            if re.match(pattern, url):
                return True
        return False

    def extract_post_id(self, url: str) -> Optional[str]:
        """從 URL 提取貼文 ID"""
        for pattern in self.INSTAGRAM_URL_PATTERNS:
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
                error_message="無法解析此連結，請確認是否為有效的 Instagram Reels 連結",
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
        
        # 優先使用 cookies.txt 檔案
        if self._cookies_file:
            video_ydl_opts["cookiefile"] = str(self._cookies_file)
            audio_ydl_opts["cookiefile"] = str(self._cookies_file)
            logger.info("使用 cookies.txt 進行下載")
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
