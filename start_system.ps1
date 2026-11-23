# Launch EDRAK System: Backend, Mock NVIDIA Service, and Frontend

$ROOT_DIR = $PSScriptRoot

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " Starting EDRAK Decision Intelligence System" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

# 1. Start Mock NVIDIA Enterprise Operations Service (Port 8088)
Write-Host "[1/3] Starting Mock NVIDIA Service on http://127.0.0.1:8088 ..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT_DIR'; `$env:PYTHONPATH='mock_services/nvidia'; .venv\Scripts\python.exe -m uvicorn mock_nvidia_api:app --host 127.0.0.1 --port 8088"

# 2. Start EDRAK Backend API (Port 8000)
Write-Host "[2/3] Starting EDRAK Backend API on http://127.0.0.1:8000 ..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT_DIR'; `$env:PYTHONPATH='backend/src'; .venv\Scripts\python.exe -m uvicorn edrak.main:app --host 127.0.0.1 --port 8000 --reload"

# 3. Start Frontend UI (Port 5173)
Write-Host "[3/3] Starting Frontend UI on http://localhost:5173 ..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT_DIR\frontend'; npm run dev"

Write-Host "------------------------------------------------------" -ForegroundColor Green
Write-Host "All 3 services launched in separate terminal windows!" -ForegroundColor Green
Write-Host "  - Mock Service : http://127.0.0.1:8088/docs" -ForegroundColor White
Write-Host "  - Backend API  : http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host "  - Frontend UI  : http://localhost:5173" -ForegroundColor White
Write-Host "======================================================" -ForegroundColor Green
