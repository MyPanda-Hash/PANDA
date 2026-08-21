@echo off
cd /d "%~dp0"
set BIN=F:\INCER\light-mes
set RUN=%BIN%\run
if not exist "%RUN%" mkdir "%RUN%"

echo [1/4] 检查 MySQL (3308)...
netstat -ano | findstr ":3308" | findstr "LISTENING" >nul 2>&1 && (echo   MySQL 运行中) || (echo   MySQL 未运行 - 请先在 phpstudy 面板点"启动" MySQL)

echo [2/4] 启动后端 (8080)...
netstat -ano | findstr ":8080" | findstr "LISTENING" >nul 2>&1 && (echo   后端已在运行) || start "light-mes-backend" /min "C:\Program Files\Java\jdk-26.0.2\bin\java.exe" -jar "%BIN%\backend\target\light-mes-backend-0.1.0.jar"

echo [3/4] 启动前端预览 (4173)...
netstat -ano | findstr ":4173" | findstr "LISTENING" >nul 2>&1 && (echo   前端已在运行) || start "light-mes-frontend" /min cmd /c "cd /d %BIN%\frontend && npm run preview"

echo [4/4] 等待服务就绪...
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:4173/
echo 打开浏览器: http://127.0.0.1:4173/   账号 admin / 123456
