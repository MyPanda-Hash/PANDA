import test from "node:test";
import assert from "node:assert/strict";

import { computeSums, summaryColumnsOf } from "../src/core/views/report-sums.js";

const fields = {
  单据日期: { dataType: "日期" },
  收入数量: { dataType: "小数" },
  收入单价: { dataType: "小数" },
  收入金额: { dataType: "小数" },
  结存金额: { dataType: "小数" },
  备注: { dataType: "文本" },
};
const fieldDefOf = (name) => fields[name] || { dataType: "文本" };

const columns = ["单据日期", "收入数量", "收入单价", "收入金额", "结存金额", "备注"].map((p) => ({ property: p }));

const rows = [
  { 单据日期: "2026-11-06", 收入数量: 5000, 收入单价: 4.2, 收入金额: 21000, 结存金额: 21000, 备注: "" },
  { 单据日期: "2026-11-06", 收入数量: 3000, 收入单价: 12.8, 收入金额: 38400, 结存金额: 38400, 备注: "" },
  { _placeholder: true, 单据日期: "", 收入数量: "", 收入单价: "", 收入金额: "", 结存金额: "", 备注: "" },
];

test("未声明合计列时：所有数值列都合计，占位行不参与", () => {
  const sums = computeSums(columns, rows, fieldDefOf, null);
  assert.equal(sums[0], "合计");
  assert.equal(sums[1], 8000); // 数量
  assert.equal(sums[2], 17); // 单价（原逻辑会加，业务上没意义）
  assert.equal(sums[3], 59400); // 金额
  assert.equal(sums[4], 59400);
  assert.equal(sums[5], ""); // 文本不合计
});

test("库存台账声明只合计金额时：数量与单价留空", () => {
  const sums = computeSums(columns, rows, fieldDefOf, ["收入金额", "结存金额"]);
  assert.equal(sums[0], "合计");
  assert.equal(sums[1], "", "不同计量单位的数量不能相加");
  assert.equal(sums[2], "", "单价合计没有意义");
  assert.equal(sums[3], 59400);
  assert.equal(sums[4], 59400);
});

test("summaryColumnsOf：只认非空数组", () => {
  assert.deepEqual(summaryColumnsOf({ summaryColumns: ["收入金额"] }), ["收入金额"]);
  assert.equal(summaryColumnsOf({ summaryColumns: [] }), null);
  assert.equal(summaryColumnsOf({}), null);
  assert.equal(summaryColumnsOf(undefined), null);
});