@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

rem U 盘多机环境：各机端口不同时不改任何文件，只设环境变量（cmd 或系统环境变量均可）：
rem   set MES_DB_PORT=3306        （MySQL 端口，默认 3308）
rem   set MES_HTTP_PORT=8081      （后端 HTTP 端口，默认 8080）
rem   set SPRING_DATASOURCE_PASSWORD=xxx （MySQL 密码，必填）
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

netstat -ano | findstr ":%MES_DB_PORT%" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [错误] MySQL 未在 %MES_DB_PORT% 端口运行，请先启动 MySQL（或 set MES_DB_PORT=实际端口）。
  goto :fail
)
echo [1/3] MySQL 已运行（%MES_DB_PORT%）

if not defined SPRING_DATASOURCE_PASSWORD (
  echo [警告] 未设置 SPRING_DATASOURCE_PASSWORD，后端可能连不上数据库。
)

netstat -ano | findstr ":%MES_HTTP_PORT%" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [2/3] 启动后端（%MES_HTTP_PORT%）...
  start "light-mes-backend" /min "%JAVA_HOME%\bin\java.exe" -jar "%ROOT%backend\target\light-mes-backend-0.1.0.jar"
) else (
  echo [2/3] 后端已运行（%MES_HTTP_PORT%）
)

netstat -ano | findstr ":4173" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [3/3] 启动前端预览（4173）...
  start "light-mes-frontend" /min /D "%ROOT%frontend" cmd /c npm run preview
) else (
  echo [3/3] 前端已运行（4173）
)

timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:4173/
echo 访问地址: http://127.0.0.1:4173/  账号: admin / 123456
goto :end

:fail
echo.
echo 启动中止。

:end
pause
