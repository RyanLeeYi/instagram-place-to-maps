# init.ps1 — 一鍵恢復可開發、可驗證狀態
# 目標：全新 clone 或換機後跑這一支，就能到「可開發、可驗證」狀態

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[1/5] 檢查 .env..." -ForegroundColor Yellow
if (-not (Test-Path ".\.env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "      已從 .env.example 建立 .env — 請填入 token 與 API key 後重跑" -ForegroundColor Red
    exit 1
}
Write-Host "      .env OK" -ForegroundColor Green

Write-Host "[2/5] 檢查 venv 與依賴..." -ForegroundColor Yellow
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}
& ".\.venv\Scripts\pip.exe" install -q -r requirements.txt
Write-Host "      venv OK" -ForegroundColor Green

Write-Host "[3/5] 檢查 Ollama 模型..." -ForegroundColor Yellow
$envContent = Get-Content ".\.env" -Raw
$ollamaModel = if ($envContent -match 'OLLAMA_MODEL=([^#\r\n]+)') { $matches[1].Trim() } else { "qwen3:8b" }
$visionModel = if ($envContent -match 'OLLAMA_VISION_MODEL=([^#\r\n]+)') { $matches[1].Trim() } else { "minicpm-v:8b" }
# 注意：$modelList 是字串陣列，對集合用 -notmatch 是「過濾器」不是布林值
# （會回傳不符合的元素，ollama list 的標題列永遠不 match → 恆為 truthy → 每次都 pull）
$modelList = & ollama list 2>&1
foreach ($m in @($ollamaModel, $visionModel)) {
    if (-not ($modelList | Select-String -SimpleMatch $m -Quiet)) {
        Write-Host "      拉取 $m（可能需要一段時間）..." -ForegroundColor Gray
        & ollama pull $m
    }
}
Write-Host "      模型 OK ($ollamaModel / $visionModel)" -ForegroundColor Green

Write-Host "[4/5] 檢查 Playwright..." -ForegroundColor Yellow
# 要測的是「chromium 執行檔在不在」，不是「python 套件裝了沒」——
# pip install playwright 之後 import 一定成功，但瀏覽器可能還沒下載（全新 clone 的常態）
$pwProbe = & ".\.venv\Scripts\python.exe" -c @"
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        p.chromium.launch(headless=True).close()
    print('ok')
except Exception as e:
    print(f'missing: {e}')
"@ 2>&1
if ($pwProbe -notcontains "ok") {
    Write-Host "      安裝 chromium..." -ForegroundColor Gray
    & ".\.venv\Scripts\playwright.exe" install chromium
}
Write-Host "      Playwright OK" -ForegroundColor Green

Write-Host "[5/5] 冒煙測試（唯讀，約 1-3 分鐘）..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" "scripts\smoke_pipeline.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "init FAILED — 冒煙測試未通過，見上方輸出" -ForegroundColor Red
    exit 1
}

Write-Host "init OK" -ForegroundColor Green
