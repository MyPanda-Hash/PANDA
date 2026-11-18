#!/usr/bin/env bash
# ============================================================
# light-mes 路线① 一键部署脚本（Ubuntu/Debian 系服务器）
# 用法: sudo bash deploy-route1.sh [你的域名，可留空=用IP访问]
# 前置: 已上传到服务器:
#   /opt/light-mes/light-mes-backend-0.1.0.jar   （后端 jar，内含 Flyway 结构脚本）
#   /opt/light-mes/light_mes.pg.dump             （可选：pg_dump -Fc 的数据包）
#   /var/www/light-mes/dist/                     （前端 dist 目录）
# 结构由 jar 内的 Flyway（db/migration/V1__baseline.sql）在首次启动时自动创建。
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
JAR_SRC=/opt/light-mes/light-mes-backend-0.1.0.jar
DIST=/var/www/light-mes/dist
DUMP=/opt/light-mes/light_mes.pg.dump

if [ ! -f "$JAR_SRC" ] || [ ! -d "$DIST" ]; then
  echo "[错误] 缺少文件，请先上传:"
  echo "  jar    -> $JAR_SRC"
  echo "  dist   -> $DIST"
  echo "  数据包（可选）-> $DUMP"
  exit 1
fi

echo "== 1/5 安装依赖 (nginx + JDK17 + PostgreSQL 16) =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx openjdk-17-jdk-headless postgresql postgresql-client

echo "== 2/5 初始化数据库 =="
# PostgreSQL：库名 light_mes、角色 postgres。口令由 MES_DB_PASSWORD 传入（默认 root，部署后请改）
DB_PASSWORD="${MES_DB_PASSWORD:-root}"
systemctl enable --now postgresql
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='light_mes'" | grep -q 1; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE light_mes ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;"
  echo "已创建数据库 light_mes"
else
  echo "数据库 light_mes 已存在，跳过创建"
fi
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER USER postgres WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -d light_mes -tAc "ALTER DATABASE light_mes SET timezone TO 'Asia/Shanghai';" >/dev/null

if [ -f "$DUMP" ]; then
  echo "检测到数据包 $DUMP，执行【备份旧库 → 整体恢复】"
  mkdir -p /opt/light-mes/backup
  if sudo -u postgres psql -tAc "SELECT 1" light_mes >/dev/null 2>&1 && [ "$(sudo -u postgres psql -d light_mes -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")" != "0" ]; then
    BK="/opt/light-mes/backup/light_mes_$(date +%Y%m%d_%H%M%S).dump"
    sudo -u postgres pg_dump -Fc -d light_mes > "$BK" && echo "旧库已备份: $BK" || echo "[警告] 旧库备份失败，继续恢复"
    sudo -u postgres psql -d light_mes -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
  fi
  sudo -u postgres pg_restore --no-owner --no-privileges -d light_mes "$DUMP"
  echo "数据恢复完成: light_mes"
else
  echo "[提示] 未提供数据包，首次启动由 Flyway 建空库结构；面板与业务数据需另行导入"
fi

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
'After=network.target postgresql.service' \
'' \
'[Service]' \
'WorkingDirectory=/opt/light-mes' \
'ExecStart=/usr/bin/java -jar /opt/light-mes/light-mes-backend-0.1.0.jar' \
'Restart=on-failure' \
'RestartSec=5' \
'Environment=SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/light_mes' \
'Environment=SPRING_DATASOURCE_USERNAME=postgres' \
"Environment=SPRING_DATASOURCE_PASSWORD=${DB_PASSWORD}" \
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
'        proxy_pass http://127.0.0.1:3308;' \
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
RESP=$(curl -s -m 10 http://127.0.0.1:3308/api/auth/login -X POST -H "Content-Type: application/json" -d '{"userName":"admin","password":"123456"}' || true)
echo "后端响应: $(echo "$RESP" | head -c 160)"

echo ""
echo "=========================================="
echo " ✅ 部署完成！"
echo " 浏览器访问: http://${DOMAIN:-<服务器公网IP>}/"
echo " 登录账号: admin / 123456"
echo ""
echo " ⚠️ 接下来 3 件事（脚本管不了）:"
echo "  1. 云控制台「安全组」放行 80 / 443 端口（有些还要放 3308 先测试）"
echo "  2. 有域名则加 A 记录指向服务器公网 IP"
echo "  3. HTTPS: 执行 certbot --nginx -d 你的域名"
echo " ⚠️ 安全提醒: PostgreSQL postgres 口令当前是 ${DB_PASSWORD}，生产请改（MES_DB_PASSWORD 传入）"
echo "=========================================="
