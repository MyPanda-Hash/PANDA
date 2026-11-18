@echo off
setlocal
set PATH=C:\Program Files\Git\bin;%PATH%
cd /d F:\INCER\light-mes
echo ==================== %date% %time% ==================== >> tools\git-auto-push.log
git add -A >> tools\git-auto-push.log 2>&1
git diff --cached --quiet
if %errorlevel%==0 (
  echo no changes, skip >> tools\git-auto-push.log
  exit /b 0
)
git commit -m "auto-sync %date% %time%" >> tools\git-auto-push.log 2>&1
git push origin master >> tools\git-auto-push.log 2>&1
echo pushed >> tools\git-auto-push.log