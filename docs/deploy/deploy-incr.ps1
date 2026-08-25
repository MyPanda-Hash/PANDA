<#
.SYNOPSIS
  light-mes 增量部署（2026-08-25）：只更新 jar + dist + 增量面板 SQL，不触碰服务器业务数据
#>
param(
  [string]$ServerIp = "8.134.255.221",
  [string]$Password = "Xtf5201314@",
  [string]$ProjectRoot = "F:\INCER\light-mes",
  [string]$WorkDir = "C:\Users\R7000P\light-mes-incr-deploy"
)
$ErrorActionPreference = 'Stop'
function Log($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }
Import-Module Posh-SSH

$jar  = Join-Path $ProjectRoot 'backend\target\light-mes-backend-0.1.0.jar'
$sql  = Join-Path $ProjectRoot 'run\incr-panels.sql'
$dist = Join-Path $ProjectRoot 'frontend\dist'
foreach ($p in @($jar,$sql,$dist)) { if (-not (Test-Path $p)) { throw "缺少产物: $p" } }
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path "$WorkDir\dist" -Force | Out-Null
Copy-Item $jar "$WorkDir\light-mes-backend.jar"
Copy-Item $sql "$WorkDir\incr-panels.sql"
Copy-Item -Recurse "$dist\*" "$WorkDir\dist\"
$zip = "$WorkDir.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$WorkDir\*" -DestinationPath $zip -Force
Log "部署包: $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)"

$sec = ConvertTo-SecureString $Password -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('root', $sec)
Log "连接 $ServerIp ..."
$s = New-SSHSession -ComputerName $ServerIp -Credential $cred -AcceptKey -ConnectionTimeout 30
try {
  Log "上传部署包..."
  Set-SCPItem -ComputerName $ServerIp -Credential $cred -AcceptKey -Path $zip -Destination '/opt/light-mes/' -Force
  $secret = -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
  $cmd = @"
set -e
cd /opt/light-mes
unzip -oq light-mes-incr-deploy.zip
# 备份旧 jar 并替换
if [ -f light-mes-backend-0.1.0.jar ]; then cp -f light-mes-backend-0.1.0.jar `"light-mes-backend-0.1.0.jar.bak-`$(date +%Y%m%d_%H%M%S)`"; fi
mv -f light-mes-backend.jar light-mes-backend-0.1.0.jar
# 替换前端 dist
rm -rf /var/www/light-mes/dist
mv -f dist /var/www/light-mes/dist
# 增量面板 SQL（仅 panel_config，幂等）
mysql -uroot -proot light_mes < /opt/light-mes/incr-panels.sql
echo "SQL_OK: $(mysql -uroot -proot -N -e "SELECT COUNT(*) FROM light_mes.panel_config WHERE panel_code IN ('TRANSFER','OUTSOURCE_ORDER','QUOTE_ORDER','PU_INVOICE','STOCK_CHECK','SERIAL_NO','SERIAL_STATUS')") new panels"
# 确保 systemd 带 MES_JWT_SECRET（缺失则重写 service 文件）
if ! grep -q MES_JWT_SECRET /etc/systemd/system/light-mes.service 2>/dev/null; then
  cp -f /etc/systemd/system/light-mes.service /etc/systemd/system/light-mes.service.bak 2>/dev/null || true
  printf '%s\n' \
'[Unit]' 'Description=light-mes backend' 'After=network.target mysql.service' '' \
'[Service]' 'WorkingDirectory=/opt/light-mes' \
'ExecStart=/usr/bin/java -jar /opt/light-mes/light-mes-backend-0.1.0.jar' \
'Restart=on-failure' 'RestartSec=5' \
'Environment=SPRING_DATASOURCE_URL=jdbc:mysql://localhost:3306/light_mes?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai' \
'Environment=SPRING_DATASOURCE_USERNAME=root' 'Environment=SPRING_DATASOURCE_PASSWORD=root' \
"Environment=MES_JWT_SECRET=$secret" 'Environment=MES_JWT_EXPIRE_HOURS=24' '' \
'[Install]' 'WantedBy=multi-user.target' \
> /etc/systemd/system/light-mes.service
  systemctl daemon-reload
  echo "JWT_SECRET_ADDED"
else
  echo "JWT_SECRET_EXISTS"
fi
systemctl restart light-mes
sleep 10
systemctl is-active light-mes
echo "DEPLOY_DONE"
"@
  Log "执行服务器部署命令..."
  $r = Invoke-SSHCommand -SessionId $s.SessionId -Command $cmd -TimeOut 300
  $r.Output | ForEach-Object { Log "  $_" }
  if ($r.ExitStatus -ne 0) { $r.Error | ForEach-Object { Log "  ERR $_" } }
  Log "部署命令完成 exit=$($r.ExitStatus)"
}
finally { Remove-SSHSession -SessionId $s.SessionId | Out-Null }