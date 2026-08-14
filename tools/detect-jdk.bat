@echo off
chcp 65001 >nul
rem 自动探测 JDK（要求 17 或更高），探测成功后设置 JAVA_HOME 并返回 0，失败返回 1
rem 优先级：已配置且有效的 JAVA_HOME > 常见安装目录（含隐藏/软链接目录）> 注册表 JavaSoft > PATH 中的 java
setlocal EnableDelayedExpansion
set "JDK_FOUND="

if defined JAVA_HOME (
  if exist "%JAVA_HOME%\bin\javac.exe" set "JDK_FOUND=%JAVA_HOME%"
)

rem 遍历常见安装目录，dir /a:d 可匹配隐藏目录与目录联接（junction），同目录下按名称升序、取最后命中即版本最高
if not defined JDK_FOUND (
  for %%B in ("C:\Program Files\Java" "D:\Program Files\Java" "C:\Program Files\Eclipse Adoptium" "D:\Program Files\Eclipse Adoptium" "C:\Program Files\Microsoft" "D:\Program Files\Microsoft") do (
    for /f "delims=" %%D in ('dir /b /a:d "%%~B\jdk-*" 2^>nul') do (
      if exist "%%~B\%%D\bin\javac.exe" set "JDK_FOUND=%%~B\%%D"
    )
  )
)

rem 注册表：HKLM\SOFTWARE\JavaSoft\JDK\<版本>\JavaHome
if not defined JDK_FOUND (
  for /f "tokens=*" %%k in ('reg query "HKLM\SOFTWARE\JavaSoft\JDK" 2^>nul') do (
    for /f "tokens=2,*" %%v in ('reg query "%%k" /v JavaHome 2^>nul ^| findstr /i "JavaHome"') do (
      if exist "%%~w\bin\javac.exe" set "JDK_FOUND=%%~w"
    )
  )
)

rem 兜底：PATH 中的 java，取其上一级目录（要求存在 release 文件确认是 JDK 根目录）
if not defined JDK_FOUND (
  for /f "delims=" %%J in ('where java 2^>nul') do (
    for %%A in ("%%J") do set "JDK_BIN=%%~dpA"
    if exist "!JDK_BIN!..\release" set "JDK_FOUND=!JDK_BIN!.."
  )
)

if not defined JDK_FOUND (
  echo [错误] 未找到 JDK：请安装 JDK 17 或更高版本
  exit /b 1
)

for /f "tokens=3" %%v in ('"!JDK_FOUND!\bin\java" -version 2^>^&1') do if not defined VER set "VER=%%~v"
if not defined VER set "VER=未知"
for /f "tokens=1 delims=." %%m in ("!VER!") do set "MAJOR=%%m"
if !MAJOR! LSS 17 (
  echo [错误] JDK 版本过低（!VER!），需要 17 或更高版本
  exit /b 1
)

endlocal & set "JAVA_HOME=%JDK_FOUND%" & set "JDK_VER=%VER%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
echo [JDK] 使用: %JAVA_HOME%  (java %JDK_VER%)
exit /b 0
