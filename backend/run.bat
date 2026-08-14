@echo off
chcp 65001 >nul
setlocal
call "%~dp0..\tools\detect-jdk.bat"
if errorlevel 1 goto :fail

cd /d "%~dp0"
if not exist target\light-mes-backend-0.1.0.jar (
  echo 未找到 jar，请先运行 build.bat
  goto :fail
)
echo 启动轻MES后端: http://localhost:8080  （需先初始化 MySQL，见 README）
"%JAVA_HOME%\bin\java" -jar target\light-mes-backend-0.1.0.jar

:fail
pause
