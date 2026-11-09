@echo off
rem Detect a JDK and export JAVA_HOME to the calling script.
rem Preference: JDK 17 first (CI builds with temurin 17, the server installs
rem openjdk-17-jdk, so local builds must match). Fall back to any JDK 17+ with a warning.
rem NOTE: keep this file ASCII-only. cmd re-reads batch files by byte offset and
rem mis-parses lines containing multi-byte (UTF-8) text, which produced stray
rem "not recognized as a command" errors and broke the path operator in the loop.
setlocal EnableExtensions EnableDelayedExpansion
set "JDK_FOUND="
set "JDK_VER="
set "JDK_FALLBACK="
set "JDK_FALLBACK_VER="

rem An existing JAVA_HOME may point at 23 or newer; only accept it when it is 17,
rem otherwise remember it as a fallback candidate.
if defined JAVA_HOME call :try_candidate "%JAVA_HOME%"

rem Common install roots: try every child directory and let the version check
rem filter (jdk1.8 etc. is rejected). Stop as soon as a 17 is found.
if not defined JDK_FOUND for %%B in ("%USERPROFILE%\.jdks" "%USERPROFILE%\.gradle\jdks" "%USERPROFILE%\.sdkman\candidates\java" "C:\Program Files\Java" "D:\Program Files\Java" "C:\Program Files\Eclipse Adoptium" "D:\Program Files\Eclipse Adoptium" "C:\Program Files\Microsoft" "D:\Program Files\Microsoft") do (
  set "BASE=%%~B"
  for /f "delims=" %%D in ('dir /b /a:d "!BASE!" 2^>nul') do if not defined JDK_FOUND call :try_candidate "!BASE!\%%D"
)

rem JDKs registered under JavaSoft in the registry.
if not defined JDK_FOUND for /f "tokens=*" %%K in ('reg query "HKLM\SOFTWARE\JavaSoft\JDK" 2^>nul') do (
  for /f "tokens=2,*" %%V in ('reg query "%%K" /v JavaHome 2^>nul ^| findstr /i "JavaHome"') do if not defined JDK_FOUND call :try_candidate "%%W"
)

rem Last resort: javac on PATH (low versions are skipped by the version check).
if not defined JDK_FOUND for /f "delims=" %%J in ('where javac 2^>nul') do (
  for %%A in ("%%J") do if not defined JDK_FOUND call :try_candidate "%%~dpA.."
)

if not defined JDK_FOUND (
  if defined JDK_FALLBACK (
    echo [WARN] JDK 17 not found, falling back to %JDK_FALLBACK_VER%
    echo        CI and the server both use JDK 17; install JDK 17 for identical builds.
    set "JDK_FOUND=%JDK_FALLBACK%"
    set "JDK_VER=%JDK_FALLBACK_VER%"
  ) else (
    echo [ERROR] JDK 17 or newer not found. Please install JDK 17.
    exit /b 1
  )
)

endlocal & set "JAVA_HOME=%JDK_FOUND%" & set "JDK_VER=%JDK_VER%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
echo [JDK] using: %JAVA_HOME%  (java %JDK_VER%)
exit /b 0

:try_candidate
set "CANDIDATE=%~1"
if not exist "%CANDIDATE%\bin\javac.exe" exit /b 1
set "CANDIDATE_VER="
for /f "tokens=3" %%V in ('"%CANDIDATE%\bin\java.exe" -version 2^>^&1') do if not defined CANDIDATE_VER set "CANDIDATE_VER=%%~V"
if not defined CANDIDATE_VER exit /b 1
for /f "tokens=1 delims=." %%M in ("%CANDIDATE_VER%") do set "CANDIDATE_MAJOR=%%M"
rem Java 8 reports 1.x; modern JDKs start with the major version directly.
if "%CANDIDATE_MAJOR%"=="1" exit /b 1
if %CANDIDATE_MAJOR% LSS 17 exit /b 1
if %CANDIDATE_MAJOR% EQU 17 (
  set "JDK_FOUND=%CANDIDATE%"
  set "JDK_VER=%CANDIDATE_VER%"
) else if not defined JDK_FALLBACK (
  set "JDK_FALLBACK=%CANDIDATE%"
  set "JDK_FALLBACK_VER=%CANDIDATE_VER%"
)
exit /b 0
