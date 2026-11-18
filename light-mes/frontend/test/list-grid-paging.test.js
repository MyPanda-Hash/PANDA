import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_PAGE_SIZE,
  ROWS_PER_PAGE_OPTIONS,
  clampPage,
  gridHeight,
  normalizePageSize,
  padRows,
  pageCount,
  pageSlice,
} from "../src/core/views/list-grid-paging.js";

test("每页行数只在 10/20/50 中取，非法值回落到默认 10", () => {
  assert.deepEqual(ROWS_PER_PAGE_OPTIONS, [10, 20, 50]);
  assert.equal(DEFAULT_PAGE_SIZE, 10);
  for (const size of ROWS_PER_PAGE_OPTIONS) assert.equal(normalizePageSize(size), size);
  assert.equal(normalizePageSize(undefined), 10);
  assert.equal(normalizePageSize(15), 10);
  assert.equal(normalizePageSize(0), 10);
  assert.equal(normalizePageSize("20"), 20);
});

test("表格高度按每页行数计算，表头与合计行固定", () => {
  // 32 表头 + 10 行 * 33 + 32 合计 = 394
  assert.equal(gridHeight(10, true), 394);
  // 50 行时高度线性增长
  assert.equal(gridHeight(50, true), 32 + 1650 + 32);
  // 汇总页签没有合计行
  assert.equal(gridHeight(10, false), 32 + 330);
  assert.equal(gridHeight(0, false), 32);
});

test("页数至少为 1，空数据也显示 1 页", () => {
  assert.equal(pageCount(0, 10), 1);
  assert.equal(pageCount(9, 10), 1);
  assert.equal(pageCount(10, 10), 1);
  assert.equal(pageCount(11, 10), 2);
  assert.equal(pageCount(50, 50), 1);
  assert.equal(pageCount(51, 50), 2);
});

test("页码越界时收敛到有效范围", () => {
  assert.equal(clampPage(0, 25, 10), 1);
  assert.equal(clampPage(1, 25, 10), 1);
  assert.equal(clampPage(2, 25, 10), 2);
  assert.equal(clampPage(3, 25, 10), 3);
  assert.equal(clampPage(9, 25, 10), 3);
  assert.equal(clampPage(5, 0, 10), 1);
});

test("分页切片只返回当前页的行，总数超出一页才分页", () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({ id: i }));
  const p1 = pageSlice(rows, 1, 10);
  assert.equal(p1.length, 10);
  assert.equal(p1[0].id, 0);
  assert.equal(p1[9].id, 9);

  const p2 = pageSlice(rows, 2, 10);
  assert.equal(p2[0].id, 10);
  assert.equal(p2.at(-1).id, 19);

  const p3 = pageSlice(rows, 3, 10);
  assert.equal(p3.length, 5);
  assert.equal(p3[0].id, 20);

  // 行数不足一页时原样返回，不分页
  const few = rows.slice(0, 4);
  assert.equal(pageSlice(few, 1, 10).length, 4);
  assert.equal(pageSlice(few, 2, 10).length, 4);
});

test("占位行补满到每页行数，表格高度不跳动", () => {
  const rows = [{ id: 1 }, { id: 2 }];
  const padded = padRows(rows, 10);
  assert.equal(padded.length, 10);
  assert.equal(padded[0].id, 1);
  assert.ok(padded.slice(2).every((r) => r._placeholder === true));

  // 满页不再补
  const full = padRows(Array.from({ length: 10 }, (_, i) => ({ id: i })), 10);
  assert.equal(full.length, 10);
  assert.ok(full.every((r) => !r._placeholder));

  // 原来的行是引用，补位不复制已存在的行
  assert.equal(padded[0], rows[0]);
});
