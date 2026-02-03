"""影片視覺分析服務 (使用 MiniCPM-V)"""

import asyncio
import base64
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import ollama

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass
class FrameDescription:
    """單幀描述"""
    timestamp: float
    description: str


@dataclass
class VisualAnalysisResult:
    """視覺分析結果"""
    success: bool
    frame_descriptions: Optional[List[FrameDescription]] = None
    overall_visual_summary: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ImageAnalysisResult:
    """單張圖片分析結果"""
    success: bool
    description: Optional[str] = None
    error_message: Optional[str] = None


class VideoVisualAnalyzer:
    """
    影片視覺分析器
    
    使用 FFmpeg 截取關鍵幀，MiniCPM-V 進行圖片理解
    """
    
    # 最大截取幀數（避免過長影片產生太多幀）
    MAX_FRAMES = 30
    
    # Auto 模式的幀數範圍
    AUTO_MIN_FRAMES = 8
    AUTO_MAX_FRAMES = 10
    
    @property
    def FRAME_INTERVAL_SECONDS(self) -> float:
        """從運行時設定取得幀數間隔"""
        from app.config import runtime_settings
        return runtime_settings.frame_interval_seconds
    
    @property
    def is_auto_mode(self) -> bool:
        """是否為自動模式"""
        from app.config import runtime_settings
        return runtime_settings.use_auto_mode
    
    # 最小截取幀數
    MIN_FRAMES = 3
    
    # 並行分析幀數（設為 1 則順序處理，設為 2-4 可並行加速）
    PARALLEL_ANALYSIS = 3
    
    def __init__(self):
        self.temp_dir = settings.temp_video_path
        self.vision_model = settings.ollama_vision_model

    def _get_video_duration(self, video_path: Path) -> float:
        """
        使用 FFprobe 取得影片長度（秒）
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.warning(f"無法取得影片長度: {e}，使用預設 30 秒")
            return 30.0

    def _calculate_frame_count(self, duration: float) -> int:
        """
        根據影片長度計算幀數
        
        規則：
        - Auto 模式：根據影片長度決定 8-10 幀
        - 其他模式：每 FRAME_INTERVAL_SECONDS 秒截取一幀
        - 最少 MIN_FRAMES 幀
        - 最多 MAX_FRAMES 幀
        """
        if self.is_auto_mode:
            # Auto 模式：根據影片長度動態決定 8-10 幀
            if duration <= 30:
                return self.AUTO_MIN_FRAMES  # 8 幀
            elif duration <= 60:
                return 9
            else:
                return self.AUTO_MAX_FRAMES  # 10 幀
        else:
            # 固定間隔模式
            frame_count = int(duration / self.FRAME_INTERVAL_SECONDS)
            return max(self.MIN_FRAMES, min(frame_count, self.MAX_FRAMES))

    def _extract_frames(self, video_path: Path) -> List[Path]:
        """
        使用 FFmpeg 從影片中截取關鍵幀
        
        Args:
            video_path: 影片路徑
            
        Returns:
            截取的幀圖片路徑列表
        """
        frames_dir = self.temp_dir / f"frames_{video_path.stem}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        
        # 取得影片長度並計算幀數
        duration = self._get_video_duration(video_path)
        frame_count = self._calculate_frame_count(duration)
        
        # 計算 fps
        if self.is_auto_mode:
            # Auto 模式：均勻分佈在整部影片
            fps = frame_count / duration if duration > 0 else 0.5
            logger.info(f"影片長度: {duration:.1f} 秒，Auto 模式截取 {frame_count} 幀 (fps={fps:.3f})")
        else:
            fps = 1.0 / self.FRAME_INTERVAL_SECONDS
            logger.info(f"影片長度: {duration:.1f} 秒，每 {self.FRAME_INTERVAL_SECONDS} 秒截取一幀，共 {frame_count} 幀 (fps={fps:.3f})")
        
        # 使用 FFmpeg 截取幀
        output_pattern = str(frames_dir / "frame_%03d.jpg")
        
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vf", f"fps={fps}",
            "-frames:v", str(frame_count),
            "-q:v", "2",  # 高品質
            output_pattern,
            "-y",  # 覆蓋現有檔案
            "-loglevel", "error"
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg 截幀失敗: {e.stderr.decode()}")
            return []
        
        # 收集截取的幀
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        logger.info(f"已截取 {len(frames)} 幀")
        
        return frames

    def _image_to_base64(self, image_path: Path) -> str:
        """將圖片轉換為 base64"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _analyze_frame_sync(self, image_path: Path, frame_index: int, total_frames: int, duration: float) -> FrameDescription:
        """同步分析單幀"""
        try:
            # 計算時間戳（根據幀在影片中的位置）
            timestamp = (frame_index / total_frames) * duration if total_frames > 0 else 0
            
            # 使用 MiniCPM-V 分析圖片
            response = ollama.chat(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": """請用繁體中文描述這張圖片的主要內容。

特別注意：
1. 如果圖片中有列表、表格、清單，請完整列出所有項目
2. 如果有提到工具、技能、軟體名稱，請逐一列出
3. 如果有步驟或流程，請按順序說明
4. 如果有數字、統計資料，請精確記錄

請盡可能詳細擷取圖片中的所有文字資訊。""",
                        "images": [str(image_path)]
                    }
                ],
                options={
                    "temperature": 0.3,
                    "num_predict": 500,
                }
            )
            
            description = response["message"]["content"].strip()
            
            return FrameDescription(
                timestamp=timestamp,
                description=description
            )
            
        except Exception as e:
            logger.error(f"分析幀 {frame_index} 失敗: {e}")
            timestamp = (frame_index / total_frames) * duration if total_frames > 0 else 0
            return FrameDescription(
                timestamp=timestamp,
                description="[無法分析]"
            )

    def _cleanup_frames(self, frames: List[Path]) -> None:
        """清理截取的幀"""
        for frame in frames:
            try:
                frame.unlink()
            except Exception:
                pass
        
        # 嘗試刪除目錄
        if frames:
            try:
                frames[0].parent.rmdir()
            except Exception:
                pass

    # 圖片類型分類 Prompt
    IMAGE_TYPE_DETECTION_PROMPT = """請判斷這張圖片的主要內容類型，只需回答以下其中一個選項：
1. 表格 - 包含行列結構的資料表格
2. 清單 - 條列式的項目清單
3. 流程圖 - 流程、步驟或架構圖
4. 資訊圖 - 包含多種視覺元素的資訊圖表
5. 純文字 - 主要是文字說明
6. 其他 - 無法歸類

請只回答選項名稱，例如：表格"""

    # 根據圖片類型的專門分析 Prompts
    IMAGE_TYPE_PROMPTS = {
        "表格": """請用繁體中文完整擷取這張表格的所有內容。

請按照以下格式呈現：
1. 先列出表格的欄位標題
2. 然後逐行列出每一列的資料
3. 如果有合併儲存格或特殊格式，請說明

請確保不遺漏任何一個儲存格的內容。""",

        "清單": """請用繁體中文完整列出這張圖片中的所有項目。

特別注意：
1. 按照圖片中的順序列出每一個項目
2. 如果有子項目或巢狀結構，請保持層級關係
3. 如果項目有編號，請保留編號
4. 擷取每個項目的完整說明文字

請確保列出所有項目，不要遺漏。""",

        "流程圖": """請用繁體中文描述這張流程圖或架構圖的內容。

請說明：
1. 流程的起點和終點
2. 每個步驟或節點的名稱和說明
3. 步驟之間的連接關係和順序
4. 如果有分支或條件判斷，請說明

請按照流程順序描述。""",

        "資訊圖": """請用繁體中文詳細描述這張資訊圖的所有內容。

請擷取：
1. 主標題和副標題
2. 所有的文字說明
3. 數據、統計、百分比等數字資訊
4. 不同區塊的主題和內容
5. 任何工具、技術、品牌名稱

請盡可能完整擷取所有資訊。""",

        "純文字": """請用繁體中文完整擷取這張圖片中的所有文字。

請注意：
1. 保持文字的原始段落結構
2. 如果有標題，請標示出來
3. 如果有重點標記（粗體、底線等），請說明
4. 擷取所有可見的文字內容""",

        "其他": """請用繁體中文描述這張圖片的主要內容。

特別注意：
1. 如果圖片中有列表、表格、清單，請完整列出所有項目
2. 如果有提到工具、技能、軟體名稱，請逐一列出
3. 如果有步驟或流程，請按順序說明
4. 如果有數字、統計資料，請精確記錄

請盡可能詳細擷取圖片中的所有文字資訊。"""
    }

    # 補充分析：專注於工具與技術（只擷取圖片中明確出現的）
    TOOLS_EXTRACTION_PROMPT = """【重要】請「只」列出這張圖片中「實際可見」的工具、技術或專業術語。

規則：
- 必須是圖片文字中「明確出現」的詞彙
- 不要推測、不要聯想、不要補充任何圖片中沒有的內容
- 如果圖片中完全沒有出現任何工具或技術名詞，請直接回答「無」

請用繁體中文，以條列方式列出圖片中出現的：
1. 軟體工具名稱
2. 技術名詞或縮寫
3. 平台或服務名稱
4. 方法論或框架名稱

只列出圖片中確實看得到的項目。"""

    def _detect_image_type(self, image_path: Path) -> str:
        """偵測圖片類型"""
        try:
            response = ollama.chat(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": self.IMAGE_TYPE_DETECTION_PROMPT,
                        "images": [str(image_path)]
                    }
                ],
                options={
                    "temperature": 0.1,
                    "num_predict": 20,
                }
            )
            
            result = response["message"]["content"].strip()
            
            # 解析回應，找出類型
            for type_name in self.IMAGE_TYPE_PROMPTS.keys():
                if type_name in result:
                    logger.info(f"偵測到圖片類型: {type_name}")
                    return type_name
            
            logger.info(f"無法識別圖片類型，使用預設: 其他 (回應: {result})")
            return "其他"
            
        except Exception as e:
            logger.warning(f"圖片類型偵測失敗: {e}，使用預設類型")
            return "其他"

    def _analyze_image_sync(self, image_path: Path, image_index: int, total_images: int) -> FrameDescription:
        """
        同步分析單張圖片（用於貼文圖片分析）
        先偵測圖片類型，再使用對應的專門 prompt 分析
        
        Args:
            image_path: 圖片路徑
            image_index: 圖片索引（從 0 開始）
            total_images: 總圖片數
            
        Returns:
            FrameDescription: 圖片描述
        """
        try:
            # 第一步：偵測圖片類型
            image_type = self._detect_image_type(image_path)
            
            # 第二步：使用對應類型的 prompt 進行分析
            type_prompt = self.IMAGE_TYPE_PROMPTS.get(image_type, self.IMAGE_TYPE_PROMPTS["其他"])
            
            response = ollama.chat(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": type_prompt,
                        "images": [str(image_path)]
                    }
                ],
                options={
                    "temperature": 0.3,
                    "num_predict": 1000,
                }
            )
            
            main_description = response["message"]["content"].strip()
            
            # 第三步：補充分析 - 擷取工具與技術
            tools_response = ollama.chat(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": self.TOOLS_EXTRACTION_PROMPT,
                        "images": [str(image_path)]
                    }
                ],
                options={
                    "temperature": 0.3,
                    "num_predict": 500,
                }
            )
            
            tools_description = tools_response["message"]["content"].strip()
            
            # 組合結果（工具與技術作為獨立區塊）
            combined = f"【圖片類型：{image_type}】\n\n{main_description}"
            
            # 工具與技術作為獨立區塊
            if tools_description and tools_description.strip() and "無" not in tools_description[:10]:
                combined += f"\n\n---\n\n【補充：工具與技術擷取】\n{tools_description}"
            
            logger.info(f"圖片 {image_index + 1}/{total_images} 分析完成 (類型: {image_type})")
            
            return FrameDescription(
                timestamp=float(image_index),
                description=combined
            )
            
        except Exception as e:
            logger.error(f"分析圖片 {image_index + 1}/{total_images} 失敗: {e}")
            return FrameDescription(
                timestamp=float(image_index),
                description="[無法分析]"
            )

    async def analyze_images(self, image_paths: List[Path]) -> VisualAnalysisResult:
        """
        分析多張靜態圖片（用於 Instagram 貼文）
        
        每張圖片獨立分析，並以【圖片 1/N】格式組合描述
        
        Args:
            image_paths: 圖片檔案路徑列表
            
        Returns:
            VisualAnalysisResult: 視覺分析結果
        """
        if not image_paths:
            return VisualAnalysisResult(
                success=False,
                error_message="沒有提供圖片"
            )
        
        try:
            total_images = len(image_paths)
            logger.info(f"正在分析 {total_images} 張貼文圖片（並行度: {self.PARALLEL_ANALYSIS}）...")
            
            loop = asyncio.get_event_loop()
            
            # 使用 Semaphore 控制並行數量
            semaphore = asyncio.Semaphore(self.PARALLEL_ANALYSIS)
            
            async def analyze_with_limit(idx: int, image_path: Path):
                async with semaphore:
                    desc = await loop.run_in_executor(
                        None, self._analyze_image_sync, image_path, idx, total_images
                    )
                    logger.info(f"  圖片 {idx + 1}/{total_images}: {desc.description[:50]}...")
                    return (idx, desc)
            
            # 並行分析所有圖片
            tasks = [analyze_with_limit(i, path) for i, path in enumerate(image_paths)]
            results = await asyncio.gather(*tasks)
            
            # 按原始順序排列結果
            frame_descriptions = [desc for _, desc in sorted(results, key=lambda x: x[0])]
            
            # 以【圖片 1/N】格式組合整體描述
            visual_texts = []
            for i, fd in enumerate(frame_descriptions, 1):
                visual_texts.append(f"【圖片 {i}/{total_images}】\n{fd.description}")
            
            overall_summary = "\n\n".join(visual_texts)
            
            logger.info(f"貼文圖片分析完成，共 {total_images} 張")
            
            return VisualAnalysisResult(
                success=True,
                frame_descriptions=frame_descriptions,
                overall_visual_summary=overall_summary
            )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"圖片分析失敗: {error_msg}")
            
            return VisualAnalysisResult(
                success=False,
                error_message=f"圖片分析失敗: {error_msg}"
            )

    async def analyze(self, video_path: Path) -> VisualAnalysisResult:
        """
        分析影片視覺內容
        
        Args:
            video_path: 影片檔案路徑
            
        Returns:
            VisualAnalysisResult: 視覺分析結果
        """
        try:
            # 取得影片長度
            duration = self._get_video_duration(video_path)
            
            # 截取關鍵幀
            logger.info("正在截取影片關鍵幀...")
            frames = self._extract_frames(video_path)
            
            if not frames:
                return VisualAnalysisResult(
                    success=False,
                    error_message="無法從影片截取幀"
                )
            
            # 分析每一幀（並行處理）
            total_frames = len(frames)
            logger.info(f"正在並行分析 {total_frames} 個關鍵幀（並行度: {self.PARALLEL_ANALYSIS}）...")
            loop = asyncio.get_event_loop()
            
            # 使用 Semaphore 控制並行數量
            semaphore = asyncio.Semaphore(self.PARALLEL_ANALYSIS)
            
            async def analyze_with_limit(i: int, frame_path: Path):
                async with semaphore:
                    desc = await loop.run_in_executor(
                        None, self._analyze_frame_sync, frame_path, i, total_frames, duration
                    )
                    logger.info(f"  幀 {i+1}/{total_frames}: {desc.description[:30]}...")
                    return (i, desc)
            
            # 並行分析所有幀
            tasks = [analyze_with_limit(i, fp) for i, fp in enumerate(frames)]
            results = await asyncio.gather(*tasks)
            
            # 按原始順序排列結果
            frame_descriptions = [desc for _, desc in sorted(results, key=lambda x: x[0])]
            
            # 生成整體視覺摘要
            visual_texts = [
                f"[{fd.timestamp:.0f}秒] {fd.description}" 
                for fd in frame_descriptions
            ]
            overall_summary = "\n".join(visual_texts)
            
            # 清理暫存幀
            self._cleanup_frames(frames)
            
            logger.info("視覺分析完成")
            
            return VisualAnalysisResult(
                success=True,
                frame_descriptions=frame_descriptions,
                overall_visual_summary=overall_summary
            )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"視覺分析失敗: {error_msg}")
            
            return VisualAnalysisResult(
                success=False,
                error_message=f"視覺分析失敗: {error_msg}"
            )

    async def analyze_image(self, image_path: Path) -> ImageAnalysisResult:
        """
        分析單張靜態圖片
        
        Args:
            image_path: 圖片檔案路徑
            
        Returns:
            ImageAnalysisResult: 圖片分析結果
        """
        if not image_path or not image_path.exists():
            return ImageAnalysisResult(
                success=False,
                error_message="圖片檔案不存在"
            )
        
        try:
            loop = asyncio.get_event_loop()
            
            # 使用現有的 _analyze_image_sync 方法
            frame_desc = await loop.run_in_executor(
                None, self._analyze_image_sync, image_path, 0, 1
            )
            
            if frame_desc.description == "[無法分析]":
                return ImageAnalysisResult(
                    success=False,
                    error_message="無法分析圖片內容"
                )
            
            return ImageAnalysisResult(
                success=True,
                description=frame_desc.description
            )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"單張圖片分析失敗: {error_msg}")
            
            return ImageAnalysisResult(
                success=False,
                error_message=f"圖片分析失敗: {error_msg}"
            )
