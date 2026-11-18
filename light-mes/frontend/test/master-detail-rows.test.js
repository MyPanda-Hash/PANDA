import test from "node:test";
import assert from "node:assert/strict";

import { flattenDocDetailRows } from "../src/core/views/master-detail-rows.js";

const docs = [
  { 编号: "BOM-001", detail: { children: [{ 父件编码: "CP001", 子件编码: "CL001" }, { 父件编码: "CP001", 子件编码: "CL002" }] } },
  { 编号: "BOM-002", detail: { children: [{ 父件编码: "CP002", 子件编码: "CL003" }] } },
  { 编号: "BOM-003" },
  { 编号: "BOM-004", detail: { children: [] } },
];

test("把所有单据的明细行摊平，并记住它来自哪张单据", () => {
  const rows = flattenDocDetailRows(docs, "children");
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r["子件编码"]), ["CL001", "CL002", "CL003"]);
  assert.deepEqual(rows.map((r) => r._docNo), ["BOM-001", "BOM-001", "BOM-002"]);
});

test("没有明细或明细为空的单据被跳过，不产生空行", () => {
  const rows = flattenDocDetailRows(docs, "children");
  assert.ok(rows.every((r) => r["父件编码"]));
  assert.equal(rows.filter((r) => !r["父件编码"]).length, 0);
});

test("父件去重后仍能点回它所属单据", () => {
  const rows = flattenDocDetailRows(docs, "children");
  const parents = new Map();
  for (const row of rows) if (!parents.has(row["父件编码"])) parents.set(row["父件编码"], row._docNo);
  assert.deepEqual([...parents.keys()], ["CP001", "CP002"]);
  assert.equal(parents.get("CP001"), "BOM-001");
});

test("入参不是数组时返回空数组，不抛错", () => {
  assert.deepEqual(flattenDocDetailRows(undefined, "children"), []);
  assert.deepEqual(flattenDocDetailRows(null, "children"), []);
  assert.deepEqual(flattenDocDetailRows([], "children"), []);
});

test("明细字段名可配置（工序等其它页签）", () => {
  const rows = flattenDocDetailRows(
    [{ 编号: "MO-1", detail: { processes: [{ 工序编码: "PX001" }] } }],
    "processes",
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0]["工序编码"], "PX001");
  assert.equal(rows[0]._docNo, "MO-1");
});
