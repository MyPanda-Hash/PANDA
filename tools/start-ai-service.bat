@echo off
chcp 65001 >nul
setlocal
rem 启动 AI 数字员工（doc-nexus-ai，docker compose: 后端 18000 + nginx 18091）。
rem 幂等：已在跑就只报告；没有 docker / Docker Desktop 没开就跳过，不影响 MES。
rem 目录可用 MES_AI_DIR 覆盖，默认取 light-mes 的同级目录 ..\doc-nexus-ai。
rem 注意：Docker Desktop 发布的端口在 netstat 里不一定出现，所以以 /api/health 为准。

set "ROOT=%~dp0.."
if defined MES_AI_DIR (
  set "AI_DIR=%MES_AI_DIR%"
) else (
  set "AI_DIR=%ROOT%\..\doc-nexus-ai"
)

call :probe
if not errorlevel 1 (
  echo [AI] 已在运行（18091 健康检查通过）
  exit /b 0
)

if not exist "%AI_DIR%\docker-compose.yml" (
  echo [AI] 未找到 "%AI_DIR%\docker-compose.yml"，跳过 AI 数字员工。
  exit /b 0
)

where docker >nul 2>&1
if errorlevel 1 (
  echo [AI] 未安装 docker，跳过 AI 数字员工。
  exit /b 0
)
docker info >nul 2>&1
if errorlevel 1 (
  echo [AI] Docker 未启动（先打开 Docker Desktop），跳过 AI 数字员工。
  exit /b 0
)

echo [AI] 启动 AI 数字员工: %AI_DIR%
pushd "%AI_DIR%" || exit /b 0
docker compose up -d
set "AI_UP_EXIT=%errorlevel%"
popd
if not "%AI_UP_EXIT%"=="0" (
  echo [AI] docker compose up -d 失败，退出码 %AI_UP_EXIT%，AI 入口将退回普通对话页。
  exit /b 0
)

rem 最多等 90 秒：冷启动要起后端并等 /api/health
for /l %%i in (1,1,45) do (
  call :probe
  if not errorlevel 1 (
    echo [AI] 就绪: http://localhost:18091/chat
    exit /b 0
  )
  ping -n 3 127.0.0.1 >nul
)
echo [AI] 90 秒内未通过健康检查，请查看 docker compose logs backend。
exit /b 0

:probe
curl -fsS -m 2 http://localhost:18091/api/health >nul 2>&1
exit /b %errorlevel%
