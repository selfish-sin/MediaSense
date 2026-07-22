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

echo [2] 检查依赖（跳过安装，避免 Python 版本冲突）...
echo   如果需要安装新依赖，请手动执行:
echo   %PYTHON% -m pip install -r requirements.txt

echo [3] 启动后端服务 (端口 7000)...
echo    Swagger UI: http://localhost:7000/docs
echo    API 根路径: http://localhost:7000/api
echo.

echo [4] 等待服务就绪后打开浏览器...
rem 后台等2秒确保服务启动，然后打开浏览器
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:7000"

%PYTHON% -m uvicorn main:socket_app --host 0.0.0.0 --port 7000

pause
