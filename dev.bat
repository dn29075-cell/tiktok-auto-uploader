@echo off
title TikTok Uploader V2 - DEV MODE
echo.
echo  ============================================
echo   TikTok Auto Uploader V2 - DEV MODE
echo  ============================================
echo.

:: Start Python backend in background
echo [1/2] Khoi dong Python FastAPI backend...
start "Backend" cmd /k "cd /d K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\backend && python -m uvicorn api:app --host 127.0.0.1 --port 8765 --reload"

:: Wait for backend to start
echo      Doi backend san sang (3 giay)...
timeout /t 3 /nobreak > nul

:: Start Electron
echo [2/2] Khoi dong Electron...
cd /d K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\electron
npm start -- --dev

pause
