"""Google Sheets 服務 - 將地點同步到 Google Sheets"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import gspread
from google.oauth2.service_account import Credentials

from app.config import settings


logger = logging.getLogger(__name__)


class GoogleSheetsService:
    """
    Google Sheets 服務
    
    將地點寫入 Google Sheets，可連動 Google My Maps
    """
    
    # Google Sheets API 範圍
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # 表頭欄位
    HEADERS = [
        "名稱",
        "地址", 
        "城市",
        "國家",
        "地點類型",
        "亮點",
        "價位",
        "推薦原因",
        "Google Maps 連結",
        "IG 來源",
        "新增時間"
    ]
    
    def __init__(self):
        self._client: Optional[gspread.Client] = None
        self._sheet: Optional[gspread.Spreadsheet] = None
        self._worksheet: Optional[gspread.Worksheet] = None
    
    def _get_client(self) -> Optional[gspread.Client]:
        """取得 Google Sheets 客戶端"""
        if self._client is not None:
            return self._client
        
        credentials_path = Path(settings.google_credentials_path)
        if not credentials_path.exists():
            logger.warning(f"找不到 Google 憑證檔案: {credentials_path}")
            return None
        
        try:
            credentials = Credentials.from_service_account_file(
                str(credentials_path),
                scopes=self.SCOPES
            )
            self._client = gspread.authorize(credentials)
            logger.info("✅ Google Sheets 客戶端初始化成功")
            return self._client
        except Exception as e:
            logger.error(f"初始化 Google Sheets 客戶端失敗: {e}")
            return None
    
    def _get_worksheet(self) -> Optional[gspread.Worksheet]:
        """取得工作表"""
        if self._worksheet is not None:
            return self._worksheet
        
        client = self._get_client()
        if client is None:
            return None
        
        spreadsheet_id = settings.google_sheets_id
        if not spreadsheet_id:
            logger.warning("未設定 GOOGLE_SHEETS_ID")
            return None
        
        try:
            self._sheet = client.open_by_key(spreadsheet_id)
            
            # 嘗試取得第一個工作表
            self._worksheet = self._sheet.sheet1
            
            # 檢查是否需要初始化表頭
            first_row = self._worksheet.row_values(1)
            if not first_row or first_row[0] != self.HEADERS[0]:
                logger.info("初始化 Google Sheets 表頭...")
                self._worksheet.update('A1', [self.HEADERS])
                # 凍結第一行
                self._worksheet.freeze(rows=1)
            
            logger.info(f"✅ 連接到 Google Sheets: {self._sheet.title}")
            return self._worksheet
            
        except gspread.SpreadsheetNotFound:
            logger.error(f"找不到 Spreadsheet ID: {spreadsheet_id}")
            return None
        except Exception as e:
            logger.error(f"取得工作表失敗: {e}")
            return None
    
    async def add_place(
        self,
        name: str,
        address: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        place_types: Optional[List[str]] = None,
        highlights: Optional[List[str]] = None,
        price_range: Optional[str] = None,
        recommendation: Optional[str] = None,
        google_maps_url: Optional[str] = None,
        source_url: Optional[str] = None
    ) -> bool:
        """
        新增地點到 Google Sheets
        
        Returns:
            bool: 是否成功
        """
        worksheet = self._get_worksheet()
        if worksheet is None:
            logger.warning("Google Sheets 未設定，跳過寫入")
            return False
        
        try:
            # 準備資料列
            row = [
                name or "",
                address or "",
                city or "",
                country or "",
                ", ".join(place_types) if place_types else "",
                ", ".join(highlights) if highlights else "",
                price_range or "",
                recommendation or "",
                google_maps_url or "",
                source_url or "",
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ]
            
            # 插入到第 2 行（表頭下方），新資料在最上面
            worksheet.insert_row(row, index=2, value_input_option='USER_ENTERED')
            
            logger.info(f"✅ 已寫入 Google Sheets: {name}")
            return True
            
        except Exception as e:
            logger.error(f"寫入 Google Sheets 失敗: {e}")
            return False
    
    def is_configured(self) -> bool:
        """檢查是否已設定 Google Sheets"""
        credentials_path = Path(settings.google_credentials_path)
        return (
            credentials_path.exists() and 
            bool(settings.google_sheets_id)
        )
