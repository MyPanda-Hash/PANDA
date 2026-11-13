#!/usr/bin/env bash
# MySQL → PostgreSQL 数据搬迁（一次性，2026-11-12 迁移用）。
#
# 原理：mysqldump --tab 导出的制表符文件就是 COPY text 格式（\t 分隔、\N 表示 NULL、
# 反斜杠转义），因此不需要 pgloader 或其他第三方依赖，也不需要主机上有 psql。
# 注意：不能用 `mysql -B` 代替——批处理模式把 NULL 打成字面量 "NULL"，COPY 会当成字符串。
#
# 用法（工作区根目录）：
#   set -a && . ./.env.local && set +a
#   bash light-mes/tools/mysql2pg-copy.sh
#
# 依赖：MySQL 容器（light-mes-db）与目标 PG 容器（light-mes-pg）都在跑，且 PG 已有结构。
set -euo pipefail

MYSQL_CONTAINER=${MES_MYSQL_CONTAINER:-light-mes-db}
PG_CONTAINER=${MES_PG_CONTAINER:-light-mes-pg}
DB=${MES_DB_NAME:-light_mes}
DUMP_DIR=/var/lib/mysql-files/pgcopy
: "${SPRING_DATASOURCE_PASSWORD:?需要先 source .env.local 提供数据库口令}"

mysql_sh() {
  docker exec -i -e MYSQL_PWD="$SPRING_DATASOURCE_PASSWORD" "$MYSQL_CONTAINER" sh -c "$1"
}
mysql_q() {
  mysql_sh "mysql -uroot -N -B --default-character-set=utf8mb4 $DB -e \"$1\""
}
psql_q() {
  docker exec -i "$PG_CONTAINER" psql -U postgres -d "$DB" -t -A -v ON_ERROR_STOP=1 -c "$1"
}

TABLES=$(mysql_q "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type='BASE TABLE' ORDER BY table_name;")
[ -n "$TABLES" ] || { echo "MySQL 里没有可搬迁的表" >&2; exit 1; }
echo "源表：$(echo "$TABLES" | wc -l | tr -d ' ') 张，导出到 $MYSQL_CONTAINER:$DUMP_DIR"

# SELECT INTO OUTFILE 以 mysql 用户写文件，目录必须归 mysql
mysql_sh "rm -rf $DUMP_DIR && mkdir -p $DUMP_DIR && chown mysql:mysql $DUMP_DIR" >/dev/null
mysql_sh "mysqldump -uroot --tab=$DUMP_DIR --no-create-info --skip-triggers --default-character-set=utf8mb4 $DB" >/dev/null

fail=0
for t in $TABLES; do
  psql_q "TRUNCATE $t RESTART IDENTITY;" >/dev/null
  # MySQL 的 INTO OUTFILE 不转义回车，COPY text 要求 \r 成对出现 → 这里补一次转义
  mysql_sh "cat $DUMP_DIR/$t.txt" | perl -pe 's/\r/\\r/g' \
    | docker exec -i "$PG_CONTAINER" psql -U postgres -d "$DB" -q -v ON_ERROR_STOP=1 -c "\copy $t FROM STDIN"
  # identity / serial 列回填：空表下一次从 1 开始，否则从 max+1 开始
  psql_q "SELECT setval(pg_get_serial_sequence('$t','id'), COALESCE(MAX(id), 0) + 1, false) FROM $t;" >/dev/null 2>&1 || true
  mysqln=$(mysql_q "SELECT COUNT(*) FROM $t;" | tr -d ' \r')
  pgn=$(psql_q "SELECT COUNT(*) FROM $t;" | tr -d ' \r')
  if [ "$mysqln" = "$pgn" ]; then
    echo "  ok   $t  $pgn 行"
  else
    echo "  FAIL $t  mysql=$mysqln pg=$pgn" >&2
    fail=1
  fi
done

mysql_sh "rm -rf $DUMP_DIR" >/dev/null
echo "序列核对（nextval 已回填）："
psql_q "SELECT string_agg(sequencename || '=' || COALESCE(last_value::text,'?'), ', ' ORDER BY sequencename) FROM pg_sequences;" || true

exit "$fail"
