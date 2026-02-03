# Instagram to Maps - 啟動腳本
# ================================

# 設定 UTF-8 編碼
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Instagram Place to Maps Bot" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Change to project directory
Set-Location $PSScriptRoot

# 步驟 1: 檢查 .env
Write-Host "[1/6] Checking .env..." -ForegroundColor Yellow
if (-not (Test-Path ".\.env")) {
    Write-Host "      Error: .env not found!" -ForegroundColor Red
    Write-Host "      Run: Copy-Item .env.example .env" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "      .env OK" -ForegroundColor Green

# 步驟 2: 檢查 Ollama 是否運行
Write-Host "[2/6] Checking Ollama..." -ForegroundColor Yellow
$ollamaProcess = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaProcess) {
    Write-Host "      Starting Ollama..." -ForegroundColor Gray
    $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $ollamaPath) {
        Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    } else {
        # 嘗試用系統 PATH 中的 ollama
        try {
            Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
            Start-Sleep -Seconds 3
        } catch {
            Write-Host "      Warning: Ollama not found, LLM features may not work" -ForegroundColor Yellow
        }
    }
}
Write-Host "      Ollama OK" -ForegroundColor Green

# 步驟 3: 檢查 LLM 模型
Write-Host "[3/6] Checking LLM models..." -ForegroundColor Yellow

# 從 .env 讀取模型設定（排除 # 註解）
$envContent = Get-Content ".\.env" -Raw
$ollamaModel = "qwen2.5:7b"  # 預設值
$visionModel = "minicpm-v"   # 預設值

if ($envContent -match 'OLLAMA_MODEL=([^#\r\n]+)') {
    $ollamaModel = $matches[1].Trim()
}
if ($envContent -match 'OLLAMA_VISION_MODEL=([^#\r\n]+)') {
    $visionModel = $matches[1].Trim()
}

# 檢查主要 LLM 模型
Write-Host "      Checking $ollamaModel..." -ForegroundColor Gray
$modelList = & ollama list 2>&1
if ($modelList -notmatch [regex]::Escape($ollamaModel)) {
    Write-Host "      Pulling $ollamaModel (this may take a while)..." -ForegroundColor Yellow
    & ollama pull $ollamaModel
}
Write-Host "      $ollamaModel OK" -ForegroundColor Green

# 檢查視覺模型
Write-Host "      Checking $visionModel..." -ForegroundColor Gray
if ($modelList -notmatch [regex]::Escape($visionModel)) {
    Write-Host "      Pulling $visionModel (this may take a while)..." -ForegroundColor Yellow
    & ollama pull $visionModel
}
Write-Host "      $visionModel OK" -ForegroundColor Green

# 步驟 4: 檢查虛擬環境
Write-Host "[4/6] Checking venv..." -ForegroundColor Yellow
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "      Creating virtual environment..." -ForegroundColor Gray
    python -m venv .venv
    Write-Host "      Installing dependencies..." -ForegroundColor Gray
    & ".\.venv\Scripts\pip.exe" install -r requirements.txt
}
Write-Host "      Venv OK" -ForegroundColor Green

# 步驟 5: 檢查 Playwright 瀏覽器
Write-Host "[5/6] Checking Playwright browsers..." -ForegroundColor Yellow
$playwrightCheck = & ".\.venv\Scripts\python.exe" -c "from playwright.sync_api import sync_playwright; print('ok')" 2>&1
if ($playwrightCheck -ne "ok") {
    Write-Host "      Installing Playwright browsers..." -ForegroundColor Gray
    & ".\.venv\Scripts\playwright.exe" install chromium
}
Write-Host "      Playwright OK" -ForegroundColor Green

# 步驟 6: 啟動服務
Write-Host "[6/6] Starting FastAPI..." -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Running at http://localhost:8080" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8080
