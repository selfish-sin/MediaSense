@echo off
chcp 65001 >nul
echo ========================================
echo   YouTube Tool V2 - 启动服务
echo ========================================
echo.

cd /d "%~dp0"

set PYTHON=python

echo [1] 检查 Python...
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo Python 未找到: %PYTHON%
    pause
    exit /b 1
)
%PYTHON% --version

echo [2] Checking dependencies...
%PYTHON% -c "import fastapi,uvicorn,socketio,pydantic,httpx,multipart,aiofiles,yt_dlp,faster_whisper,ctranslate2,docx,bilibili_api,PIL,requests,fpdf,websockets" >nul 2>&1
if errorlevel 1 (
    echo   [!] Missing dependencies detected, auto installing requirements.txt ...
    %PYTHON% -m pip install -r requirements.txt
    %PYTHON% -c "import fastapi,uvicorn,socketio,pydantic,httpx,multipart,aiofiles,yt_dlp,faster_whisper,ctranslate2,docx,bilibili_api,PIL,requests,fpdf,websockets" >nul 2>&1
    if errorlevel 1 (
        echo   [x] Some dependencies still missing, service may be degraded!
        echo       Manual fix: %PYTHON% -m pip install -r requirements.txt
    ) else (
        echo   [ok] Dependencies repaired
    )
) else (
    echo   [ok] All dependencies ready
)

set PORT=7001
for /f "delims=" %%P in ('%PYTHON% -c "from config import SERVER_PORT; print(SERVER_PORT)" 2^>nul') do set PORT=%%P

echo [3] 启动后端服务 (端口 %PORT%)...
echo    Swagger UI: http://localhost:%PORT%/docs
echo    API 根路径: http://localhost:%PORT%/api
echo.

echo [4] 等待服务就绪后打开浏览器...
rem 后台等2秒确保服务启动，然后打开浏览器
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:%PORT%"

%PYTHON% -m uvicorn main:socket_app --host 0.0.0.0 --port %PORT%

pause
