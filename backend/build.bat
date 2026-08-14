@echo off
chcp 65001 >nul
setlocal
call "%~dp0..\tools\detect-jdk.bat"
if errorlevel 1 goto :fail

set "MAVEN_HOME=%~dp0..\tools\apache-maven-3.9.9"
if not exist "%MAVEN_HOME%\bin\mvn.cmd" (
  echo [错误] 未找到便携 Maven：%MAVEN_HOME%
  goto :fail
)

cd /d "%~dp0"
echo [构建] 正在打包后端（跳过测试）...
call "%MAVEN_HOME%\bin\mvn.cmd" -s "%~dp0..\tools\settings.xml" -q clean package -DskipTests
if errorlevel 1 (
  echo [FAIL] 构建失败，请查看上方错误信息
  goto :fail
)

echo.
echo [OK] 构建成功: target\light-mes-backend-0.1.0.jar
goto :end

:fail
echo.
echo 构建中止。
:end
pause
