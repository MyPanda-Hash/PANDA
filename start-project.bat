@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

rem U 盘多机环境：各机端口不同时不改任何文件，只设环境变量（cmd 或系统环境变量均可）：
rem   set MES_DB_PORT=3306        （MySQL 端口，默认 3308）
rem   set MES_HTTP_PORT=8081      （后端 HTTP 端口，默认 8080）
rem   set SPRING_DATASOURCE_PASSWORD=xxx （MySQL 密码，必填）
rem   set MES_JWT_SECRET=xxx       （至少 64 位密码学安全随机值，必填）
rem   set MES_AI_SSO_SECRET=xxx    （与 AI 数字员工 DOC_NEXUS_MES_SSO_SECRET 相同；不设则 AI 入口降级）
rem   set MES_AI_DIR=xxx           （AI 数字员工目录，默认 ..\doc-nexus-ai）
if not defined MES_DB_PORT set "MES_DB_PORT=3308"
if not defined MES_HTTP_PORT set "MES_HTTP_PORT=8080"

call "%ROOT%tools\detect-jdk.bat"
if errorlevel 1 goto :fail

where npm >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 npm，请确认 Node.js 已加入 PATH。
  goto :fail
)

if not exist "%ROOT%backend\target\light-mes-backend-0.1.0.jar" (
  echo [错误] 未找到后端 jar，请先运行 backend\build.bat。
  goto :fail
)
if not exist "%ROOT%frontend\dist\index.html" (
  echo [错误] 未找到前端构建产物，请先在 frontend 目录运行 npm run build。
  goto :fail
)

rem 端口探测用真实 TCP 连接：本机 netstat 对某些监听（如 phpstudy MySQL）时有时无，
rem 用 netstat|findstr LISTENING 判断会误报「未运行」。
call :portup %MES_DB_PORT%
if errorlevel 1 (
  echo [错误] MySQL 未在 %MES_DB_PORT% 端口运行，请先启动 MySQL（或 set MES_DB_PORT=实际端口）。
  goto :fail
)
echo [1/4] MySQL 已运行（%MES_DB_PORT%）

if not defined SPRING_DATASOURCE_PASSWORD (
  echo [错误] 未设置 SPRING_DATASOURCE_PASSWORD，请先配置数据库密码。
  goto :fail
)
if not defined MES_JWT_SECRET (
  echo [错误] 未设置 MES_JWT_SECRET，请先配置至少 64 位随机密钥。
  goto :fail
)

call :portup %MES_HTTP_PORT%
if errorlevel 1 (
  echo [2/4] 启动后端（%MES_HTTP_PORT%）...
  start "light-mes-backend" /min "%JAVA_HOME%\bin\java.exe" -jar "%ROOT%backend\target\light-mes-backend-0.1.0.jar"
) else (
  echo [2/4] 后端已运行（%MES_HTTP_PORT%）
)

call :portup 4173
if errorlevel 1 (
  echo [3/4] 启动前端预览（4173）...
  start "light-mes-frontend" /min /D "%ROOT%frontend" cmd /c npm run preview
) else (
  echo [3/4] 前端已运行（4173）
)

echo [4/4] AI 数字员工（doc-nexus-ai / Docker）
call "%ROOT%tools\start-ai-service.bat"

timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:4173/
echo 访问地址: http://127.0.0.1:4173/
echo AI 数字员工: http://localhost:18091/chat （MES 顶栏按钮免密进入）
goto :end

:fail
echo.
echo 启动中止。
goto :end

rem 真实建立一次 TCP 连接判断端口是否可用，%1=端口；成功 exit /b 0，失败 exit /b 1
:portup
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$t=$c.ConnectAsync('127.0.0.1',%1);if($t.Wait(1500) -and $c.Connected){exit 0}else{exit 1}}catch{exit 1}finally{if($c){$c.Dispose()}}" >nul 2>&1
exit /b %errorlevel%

:end
pause
