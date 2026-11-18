#!/usr/bin/env node
/**
 * 工艺路线迁移：单单据 → 一单一条路线（主从结构）。
 *
 * 背景：ROUTE 原来是 singleDoc，三条路线的工序全混在一张 form_data 里，
 * 靠行内「工艺路线编码」区分。改造后编码/名称/停用升为表头，明细只放工序。
 *
 * 做法（可重复执行，已迁移的跳过）：
 *   1. 读原单单据 detail.processes，按 工艺路线编码 分组
 *   2. 每组生成一条新 ROUTE 单据（form_no = ROUTE-001/002/...）
 *   3. 原单据状态置「已关闭」保留回滚路径，不删除
 *
 * 用法：
 *   node tools/migrate-route-panel.mjs --check     # 只看会做什么
 *   node tools/migrate-route-panel.mjs --apply     # 真跑
 */
import { execFile } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { promisify } from "node:util";

const run = promisify(execFile);
const ROOT = new URL("..", import.meta.url).pathname;
// .env.local 在工作区根（light-mes 的上一级），也可能在 light-mes 内
const ENV_CANDIDATES = [`${ROOT}/.env.local`, `${ROOT}/../.env.local`];
const APPLY = process.argv.includes("--apply");

function dbPassword() {
  const path = ENV_CANDIDATES.find((p) => existsSync(p));
  if (!path) throw new Error(`未找到 .env.local（找过：${ENV_CANDIDATES.join(", ")}）`);
  const text = readFileSync(path, "utf8");
  const line = text.split("\n").find((l) => l.trim().startsWith("export SPRING_DATASOURCE_PASSWORD=")
    || l.trim().startsWith("SPRING_DATASOURCE_PASSWORD="));
  if (!line) throw new Error("未在 .env.local 找到 SPRING_DATASOURCE_PASSWORD");
  return line.split("=").slice(1).join("=").trim().replace(/^['"]|['"]$/g, "");
}

async function sql(query) {
  const { stdout } = await run("docker", [
    "exec", "-e", `MYSQL_PWD=${dbPassword()}`, "light-mes-db",
    "mysql", "-uroot", "--default-character-set=utf8mb4", "-B", "-N", "-e", query,
  ], { maxBuffer: 64 * 1024 * 1024 });
  return stdout.trim();
}

function esc(v) {
  if (v === null || v === undefined) return "NULL";
  return `'${String(v).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

/** 行内去掉已升为表头的三个字段，并按行序重排加工顺序。 */
function normalizeRows(rows) {
  return rows.map((r, i) => {
    const copy = { ...r };
    delete copy["工艺路线编码"];
    delete copy["工艺路线名称"];
    delete copy["停用"];
    copy["加工顺序"] = i + 1;
    return copy;
  });
}

async function main() {
  const raw = await sql(
    "SELECT id, form_no, status, data, detail_data FROM light_mes.form_data WHERE panel_code='ROUTE' ORDER BY id;"
  );
  if (!raw) {
    console.log("没有 ROUTE 单据，无需迁移。");
    return;
  }

  // 已是主从结构的单据（表头带工艺路线编码）与旧的单单据分开
  const docs = raw.split("\n").map((line) => {
    const [id, formNo, status, data, detailData] = line.split("\t");
    return { id, formNo, status, data: JSON.parse(data || "{}"), detail: JSON.parse(detailData || "{}") };
  });

  const alreadyMigrated = docs.filter((d) => String(d.data["工艺路线编码"] || "").trim() !== "");
  const legacyDocs = docs.filter((d) => !String(d.data["工艺路线编码"] || "").trim());
  console.log(`ROUTE 单据 ${docs.length} 张：已迁移 ${alreadyMigrated.length}，待迁移 ${legacyDocs.length}`);
  if (!legacyDocs.length) {
    console.log("没有需要拆分的单单据。");
    return;
  }

  // 按 工艺路线编码 分组（保留首次出现顺序）
  const groups = new Map();
  for (const doc of legacyDocs) {
    for (const row of doc.detail.processes || []) {
      const code = String(row["工艺路线编码"] || "").trim();
      if (!code) continue;
      if (!groups.has(code)) {
        groups.set(code, { name: String(row["工艺路线名称"] || "").trim(), stop: row["停用"] === true, rows: [] });
      }
      groups.get(code).rows.push(row);
    }
  }
  if (!groups.size) {
    console.log("单单据里没有带编码的工序行，无需迁移。");
    return;
  }

  console.log(`\n将拆成 ${groups.size} 条工艺路线：`);
  for (const [code, g] of groups) {
    console.log(`  ${code} | ${g.name} | ${g.rows.length} 道工序`);
  }

  // 新单号：避开已占用的 ROUTE-xxx
  const used = new Set((await sql(
    "SELECT form_no FROM light_mes.form_data WHERE panel_code='ROUTE';"
  )).split("\n").filter(Boolean));
  let seq = 0;
  const nextNo = () => {
    let no;
    do { no = `ROUTE-${String(++seq).padStart(3, "0")}`; } while (used.has(no));
    used.add(no);
    return no;
  };

  if (!APPLY) {
    console.log("\n[check] 未写入。加 --apply 执行。");
    for (const [code] of groups) console.log(`  ${nextNo()}  <- ${code}`);
    return;
  }

  const status = legacyDocs[0].status || "已审核";
  const statements = [];
  for (const [code, g] of groups) {
    const formNo = nextNo();
    const head = { 备注: "工艺路线", 工艺路线编码: code, 工艺路线名称: g.name, 停用: g.stop };
    const detail = { processes: normalizeRows(g.rows) };
    statements.push(
      "INSERT INTO light_mes.form_data (panel_code, form_no, status, data, detail_data, create_by, create_time, update_time) " +
      `VALUES ('ROUTE', ${esc(formNo)}, ${esc(status)}, ${esc(JSON.stringify(head))}, ${esc(JSON.stringify(detail))}, 'admin', NOW(), NOW());`
    );
    console.log(`  + ${formNo}  <- ${code} (${g.rows.length} 行)`);
  }
  // 原单单据归档（改 panel_code，不再出现在 ROUTE 列表；数据完整保留可回滚）
  for (const doc of legacyDocs) {
    statements.push(
      `UPDATE light_mes.form_data SET panel_code='ROUTE_LEGACY', status='已关闭', update_time=NOW() WHERE id=${Number(doc.id)};`
    );
    console.log(`  ~ 归档原单单据 id=${doc.id} → panel_code=ROUTE_LEGACY（数据保留，可回滚）`);
  }

  await sql(statements.join("\n"));
  console.log("\n迁移完成。");

  const verify = await sql(
    "SELECT form_no, status FROM light_mes.form_data WHERE panel_code='ROUTE' ORDER BY form_no;"
  );
  console.log("\n当前 ROUTE 单据：");
  console.log(verify);
}

main().catch((e) => {
  console.error("迁移失败：", e.message);
  process.exit(1);
});
