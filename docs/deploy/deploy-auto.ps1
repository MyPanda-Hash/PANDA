<#
.SYNOPSIS
  light-mes 一键自动部署（Windows 侧驱动，服务器只需 Ubuntu 22.04+ 与 root SSH）
.DESCRIPTION
  流程：本机打包部署 zip -> SCP 上传 -> 服务器解压 -> 运行 deploy-route1.sh
        -> 兜底补 systemd/nginx 配置 -> 全量验证（服务/登录/前端/数据库）。
  依赖：Posh-SSH 模块（首次自动安装）
.EXAMPLE
  .\deploy-auto.ps1 -ServerIp <服务器IP> -Password '<你的服务器root密码>'
  .\deploy-auto.ps1 -ServerIp 1.2.3.4 -Password 'pwd' -Domain mes.example.com
#>
param(
  [Parameter(Mandatory=$true)][string]$ServerIp,
  [Parameter(Mandatory=$true)][string]$Password,
  [string]$Domain = "",
  [string]$ProjectRoot = "E:\INCER\light-mes",
  [string]$WorkDir = "C:\Users\x1787\light-mes-deploy"
)

$ErrorActionPreference = 'Stop'
function Log($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }

# ---------- 0. 依赖检查 ----------
if (-not (Get-Module -ListAvailable Posh-SSH)) {
  Log "安装 Posh-SSH 模块..."
  Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser | Out-Null
  Install-Module Posh-SSH -Force -Scope CurrentUser -SkipPublisherCheck
}
Import-Module Posh-SSH

# ---------- 1. 本机打包（复用已构建产物） ----------
Log "打包部署包..."
$jar  = Join-Path $ProjectRoot 'backend\target\light-mes-backend-0.1.0.jar'
$sql  = Join-Path $ProjectRoot 'backend\src\main\resources\db\init.sql'
$full = Join-Path $ProjectRoot 'docs\deploy\light_mes_deploy.sql'
$dist = Join-Path $ProjectRoot 'frontend\dist'
$sh   = Join-Path $ProjectRoot 'docs\deploy\deploy-route1.sh'
foreach ($p in @($jar,$sql,$dist,$sh)) { if (-not (Test-Path $p)) { throw "缺少产物: $p" } }
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path "$WorkDir\dist" -Force | Out-Null
Copy-Item $jar $WorkDir
Copy-Item $sql $WorkDir
if (Test-Path $full) { Copy-Item $full $WorkDir; Log "已加入全量数据: light_mes_deploy.sql" }
Copy-Item -Recurse "$dist\*" "$WorkDir\dist\"
Copy-Item $sh $WorkDir
$zip = "$WorkDir.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$WorkDir\*" -DestinationPath $zip -Force
Log "部署包: $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)"

# ---------- 2. 连接 + 上传 ----------
$sec = ConvertTo-SecureString $Password -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential('root', $sec)
Log "连接 $ServerIp ..."
$s = New-SSHSession -ComputerName $ServerIp -Credential $cred -AcceptKey -ConnectionTimeout 30
try {
  Log "上传部署包..."
  Set-SCPItem -ComputerName $ServerIp -Credential $cred -AcceptKey -Path $zip -Destination '/opt/light-mes/' -Force

  # ---------- 3. 解压 + 移动 dist（先删旧目录，避免 mv 嵌套成 dist/dist） ----------
  Log "解压部署包..."
  $r = Invoke-SSHCommand -SessionId $s.SessionId -Command "apt-get install -y unzip >/dev/null 2>&1; cd /opt/light-mes && unzip -oq light-mes-deploy.zip; mkdir -p /var/www/light-mes; if [ -d /var/www/light-mes/dist ]; then rm -rf /var/www/light-mes/dist; fi; [ -d /opt/light-mes/dist ] && mv -f /opt/light-mes/dist /var/www/light-mes/dist; ls /var/www/light-mes/dist | head -3; echo UNZIP_DONE"
  $r.Output | ForEach-Object { Log "  $_" }

  # ---------- 4. 运行部署脚本（前台，超时给足） ----------
  Log "运行 deploy-route1.sh（安装 nginx/JDK17/MySQL + 建库，约 3-6 分钟）..."
  $r = Invoke-SSHCommand -SessionId $s.SessionId -Command "cd /opt/light-mes && bash deploy-route1.sh ${Domain}" -TimeOut 900
  $r.Output | Select-Object -Last 15 | ForEach-Object { Log "  $_" }
  if ($r.ExitStatus -ne 0) { Log "脚本退出码 $($r.ExitStatus)，继续兜底步骤"; $r.Error | ForEach-Object { Log "  ERR $_" } }
  else { Log "部署脚本完成 OK" }
}
finally { Remove-SSHSession -SessionId $s.SessionId | Out-Null }

# ---------- 5. 兜底：确保 systemd 服务与 nginx 配置就位 ----------
Log "兜底检查 systemd / nginx ..."
$s = New-SSHSession -ComputerName $ServerIp -Credential $cred -AcceptKey -ConnectionTimeout 30
try {
  $r = Invoke-SSHCommand -SessionId $s.SessionId -Command @'
if ! systemctl is-active --quiet light-mes; then
  echo "light-mes 未运行，重建配置..."
  printf '%s\n' \
'[Unit]' 'Description=light-mes backend' 'After=network.target mysql.service' '' \
'[Service]' 'WorkingDirectory=/opt/light-mes' \
'ExecStart=/usr/bin/java -jar /opt/light-mes/light-mes-backend-0.1.0.jar' \
'Restart=on-failure' 'RestartSec=5' \
'Environment=SPRING_DATASOURCE_URL=jdbc:mysql://localhost:3306/light_mes?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai' \
'Environment=SPRING_DATASOURCE_USERNAME=root' 'Environment=SPRING_DATASOURCE_PASSWORD=root' '' \
'[Install]' 'WantedBy=multi-user.target' \
> /etc/systemd/system/light-mes.service
  systemctl daemon-reload && systemctl enable --now light-mes && sleep 6
fi
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
if ! nginx -t 2>/dev/null; then
  echo "nginx 配置重建..."
  printf '%s\n' \
'server {' '    listen 80;' '    server_name _;' '    root /var/www/light-mes/dist;' \
'    index index.html;' '    client_max_body_size 200m;' \
'    location / { try_files $uri $uri/ /index.html; }' \
'    location /assets/ { expires 30d; add_header Cache-Control "public, immutable"; }' \
'    location /api/ { proxy_pass http://127.0.0.1:3308; proxy_set_header Host $host;' \
'        proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
'        proxy_read_timeout 120s; }' '}' \
> /etc/nginx/sites-available/light-mes
  ln -sf /etc/nginx/sites-available/light-mes /etc/nginx/sites-enabled/light-mes
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
fi
echo FALLBACK_DONE
'@
  $r.Output | ForEach-Object { Log "  $_" }
  $r.Error | ForEach-Object { Log "  ERR $_" }
}
finally { Remove-SSHSession -SessionId $s.SessionId | Out-Null }

# ---------- 6. 全量验证 ----------
Log "全量验证..."
$s = New-SSHSession -ComputerName $ServerIp -Credential $cred -AcceptKey -ConnectionTimeout 30
try {
  $r = Invoke-SSHCommand -SessionId $s.SessionId -Command @'
echo "== 服务 =="; systemctl is-active light-mes mysql nginx | tr '\n' ' '; echo
echo "== 登录 =="
TOKEN=$(curl -s -m 10 http://127.0.0.1:3308/api/auth/login -X POST -H "Content-Type: application/json" -d '{"userName":"admin","password":"123456"}' | grep -o '"code":[0-9]*' | head -1)
echo "登录 code: $TOKEN"
echo "== 前端 =="; curl -s -o /dev/null -w "http_code: %{http_code}\n" http://127.0.0.1/
echo "== 数据库 =="; mysql -uroot -proot -N -e "USE light_mes; SELECT CONCAT('menus=',(SELECT COUNT(*) FROM sys_menu),' panels=',(SELECT COUNT(*) FROM panel_config),' forms=',(SELECT COUNT(*) FROM form_data),' users=',(SELECT COUNT(*) FROM sys_user));" 2>/dev/null
'@
  $r.Output | ForEach-Object { Log "  $_" }
}
finally { Remove-SSHSession -SessionId $s.SessionId | Out-Null }

Log "=========================================="
Log " 部署完成！浏览器访问: http://$ServerIp/  (admin / 123456)"
Log " 公网打不开 -> 阿里云安全组放行 TCP 80"
Log "=========================================="
