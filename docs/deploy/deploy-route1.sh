#!/usr/bin/env bash
# ============================================================
# light-mes 路线① 一键部署脚本（Ubuntu/Debian 系服务器）
# 用法: sudo bash deploy-route1.sh [你的域名，可留空=用IP访问]
# 前置: 已上传这三个文件到服务器:
#   /opt/light-mes/light-mes-backend-0.1.0.jar   （后端 jar）
#   /opt/light-mes/init.sql                      （数据库脚本）
#   /var/www/light-mes/dist/                     （前端 dist 目录）
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
JAR_SRC=/opt/light-mes/light-mes-backend-0.1.0.jar
DIST=/var/www/light-mes/dist
SQL=/opt/light-mes/init.sql

if [ ! -f "$JAR_SRC" ] || [ ! -f "$SQL" ] || [ ! -d "$DIST" ]; then
  echo "[错误] 缺少文件，请先上传:"
  echo "  jar    -> $JAR_SRC"
  echo "  sql    -> $SQL"
  echo "  dist   -> $DIST"
  exit 1
fi

echo "== 1/5 安装依赖 (nginx + JDK17 + MySQL) =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx openjdk-17-jdk-headless mysql-server

echo "== 2/5 初始化数据库 =="
systemctl enable --now mysql
# 幂等：root 密码可能已是 root（重跑场景），先探测无密码连接，失败再走 ALTER
if mysql -uroot -e "SELECT 1" >/dev/null 2>&1; then
  echo "MySQL root 无密码可连，跳过密码设置（已初始化过）"
else
  mysql -uroot <<'SQL'
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'root';
FLUSH PRIVILEGES;
SQL
fi
mysql -uroot -proot < "$SQL"
echo "数据库初始化完成: light_mes"

echo "== 3/5 后端 systemd 服务 =="
mkdir -p /opt/light-mes
if [ "$(realpath "$JAR_SRC")" != "/opt/light-mes/light-mes-backend-0.1.0.jar" ]; then
  cp "$JAR_SRC" /opt/light-mes/light-mes-backend-0.1.0.jar
  echo "jar 已复制到 /opt/light-mes/"
else
  echo "jar 已在目标位置，跳过复制"
fi
# 用 printf 写 systemd 配置（不依赖 heredoc/stdin，nohup 后台执行也安全）
printf '%s\n' \
'[Unit]' \
'Description=light-mes backend' \
'After=network.target mysql.service' \
'' \
'[Service]' \
'WorkingDirectory=/opt/light-mes' \
'ExecStart=/usr/bin/java -jar /opt/light-mes/light-mes-backend-0.1.0.jar' \
'Restart=on-failure' \
'RestartSec=5' \
'Environment=SPRING_DATASOURCE_URL=jdbc:mysql://localhost:3306/light_mes?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai' \
'Environment=SPRING_DATASOURCE_USERNAME=root' \
'Environment=SPRING_DATASOURCE_PASSWORD=root' \
'' \
'[Install]' \
'WantedBy=multi-user.target' \
> /etc/systemd/system/light-mes.service
systemctl daemon-reload
systemctl enable --now light-mes

echo "== 4/5 nginx 站点 =="
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
# 用 printf 写配置（不依赖 heredoc/stdin，nohup 后台执行也安全）
printf '%s\n' \
'server {' \
'    listen 80;' \
"    server_name ${DOMAIN:-_};" \
"    root $DIST;" \
'    index index.html;' \
'    client_max_body_size 200m;' \
'' \
'    location / {' \
'        try_files $uri $uri/ /index.html;' \
'    }' \
'    location /assets/ {' \
'        expires 30d;' \
'        add_header Cache-Control "public, immutable";' \
'    }' \
'    location /api/ {' \
'        proxy_pass http://127.0.0.1:8080;' \
'        proxy_set_header Host $host;' \
'        proxy_set_header X-Real-IP $remote_addr;' \
'        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
'        proxy_read_timeout 120s;' \
'    }' \
'}' \
> /etc/nginx/sites-available/light-mes
ln -sf /etc/nginx/sites-available/light-mes /etc/nginx/sites-enabled/light-mes
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "== 5/5 验证后端登录 =="
sleep 4
RESP=$(curl -s -m 10 http://127.0.0.1:8080/api/auth/login -X POST -H "Content-Type: application/json" -d '{"userName":"admin","password":"123456"}' || true)
echo "后端响应: $(echo "$RESP" | head -c 160)"

echo ""
echo "=========================================="
echo " ✅ 部署完成！"
echo " 浏览器访问: http://${DOMAIN:-<服务器公网IP>}/"
echo " 登录账号: admin / 123456"
echo ""
echo " ⚠️ 接下来 3 件事（脚本管不了）:"
echo "  1. 云控制台「安全组」放行 80 / 443 端口（有些还要放 8080 先测试）"
echo "  2. 有域名则加 A 记录指向服务器公网 IP"
echo "  3. HTTPS: 执行 certbot --nginx -d 你的域名"
echo " ⚠️ 安全提醒: MySQL root 密码当前是 root，生产请修改"
echo "=========================================="
