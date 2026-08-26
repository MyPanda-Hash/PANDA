@echo off
chcp 65001 >nul
rem 自动探测 JDK（要求 17 或更高），并在当前调用环境设置 JAVA_HOME。
setlocal EnableExtensions EnableDelayedExpansion
set "JDK_FOUND="
set "JDK_VER="

rem 已配置的 JAVA_HOME 可能指向过期 JDK；只有通过版本校验才接受。
if defined JAVA_HOME call :try_candidate "%JAVA_HOME%"

rem 常见安装目录：只要找到一个符合要求的候选即可。
if not defined JDK_FOUND for %%B in ("C:\Program Files\Java" "D:\Program Files\Java" "C:\Program Files\Eclipse Adoptium" "D:\Program Files\Eclipse Adoptium" "C:\Program Files\Microsoft" "D:\Program Files\Microsoft") do (
  for /f "delims=" %%D in ('dir /b /a:d "%%~B\jdk-*" 2^>nul') do if not defined JDK_FOUND call :try_candidate "%%~B\%%D"
)

rem 注册表中的 JavaSoft JDK 安装。
if not defined JDK_FOUND for /f "tokens=*" %%K in ('reg query "HKLM\SOFTWARE\JavaSoft\JDK" 2^>nul') do (
  for /f "tokens=2,*" %%V in ('reg query "%%K" /v JavaHome 2^>nul ^| findstr /i "JavaHome"') do if not defined JDK_FOUND call :try_candidate "%%W"
)

rem 最后检查 PATH 中的 javac，跳过低版本候选。
if not defined JDK_FOUND for /f "delims=" %%J in ('where javac 2^>nul') do (
  for %%A in ("%%J") do if not defined JDK_FOUND call :try_candidate "%%~dpA.."
)

if not defined JDK_FOUND (
  echo [错误] 未找到 JDK 17+：请安装 JDK 17 或更高版本
  exit /b 1
)

endlocal & set "JAVA_HOME=%JDK_FOUND%" & set "JDK_VER=%JDK_VER%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
echo [JDK] 使用: %JAVA_HOME%  (java %JDK_VER%)
exit /b 0

:try_candidate
set "CANDIDATE=%~1"
if not exist "%CANDIDATE%\bin\javac.exe" exit /b 1
set "CANDIDATE_VER="
for /f "tokens=3" %%V in ('"%CANDIDATE%\bin\java.exe" -version 2^>^&1') do if not defined CANDIDATE_VER set "CANDIDATE_VER=%%~V"
if not defined CANDIDATE_VER exit /b 1
for /f "tokens=1 delims=." %%M in ("%CANDIDATE_VER%") do set "CANDIDATE_MAJOR=%%M"
rem Java 8 使用 1.x 版本格式；现代 JDK 直接以主版本号开头。
if "%CANDIDATE_MAJOR%"=="1" exit /b 1
if %CANDIDATE_MAJOR% LSS 17 exit /b 1
set "JDK_FOUND=%CANDIDATE%"
set "JDK_VER=%CANDIDATE_VER%"
exit /b 0
