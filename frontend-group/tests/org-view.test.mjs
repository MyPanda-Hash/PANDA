// 人力地图（组织架构树）自检：node frontend-group/tests/org-view.test.mjs
//
// 不引入测试框架，断言失败直接非零退出（与 audio-wav.test.mjs 一致）。
import assert from 'node:assert/strict';

import {
  ASSISTANT_LABEL,
  UNASSIGNED_LABEL,
  buildOrgTree,
  deptLine,
  groupByDeptPath,
} from '../org-view.js';

const members = [
  { user_id: 'u1', display_name: '赵车间', role: 'member', dept_name: '熔铸车间', dept_path: '公司 / 生产部 / 熔铸车间', position: '车间员工' },
  { user_id: 'u2', display_name: '孙质检', role: 'member', dept_name: '质检部', dept_path: '公司 / 质检部', position: '质检员' },
  { user_id: 'u3', display_name: '管理员', role: 'owner', dept_name: '总经办', dept_path: '公司 / 总经办', position: '超级管理员' },
  { user_id: 'u4', display_name: '无部门', role: 'member', dept_name: '', dept_path: '', position: '' },
  { user_id: 'ai', display_name: 'AI 助手', role: 'assistant' },
];

// MES 返回的是树先序，含空部门；parent_id 0 是根
const orgMap = {
  departments: [
    { id: 1, parent_id: 0, name: '公司', path: '公司', depth: 0, member_ids: [] },
    { id: 2, parent_id: 1, name: '总经办', path: '公司 / 总经办', depth: 1, member_ids: ['u3'] },
    { id: 3, parent_id: 1, name: '销售部', path: '公司 / 销售部', depth: 1, member_ids: [] },
    { id: 4, parent_id: 1, name: '采购部', path: '公司 / 采购部', depth: 1, member_ids: [] },
    { id: 5, parent_id: 1, name: '生产部', path: '公司 / 生产部', depth: 1, member_ids: [] },
    { id: 6, parent_id: 5, name: '熔铸车间', path: '公司 / 生产部 / 熔铸车间', depth: 2, member_ids: ['u1'] },
    { id: 10, parent_id: 1, name: '质检部', path: '公司 / 质检部', depth: 1, member_ids: ['u2'] },
  ],
  unassigned: ['u4'],
  members: {},
};

// ── deptLine ───────────────────────────────────────────────────────────
assert.equal(deptLine(members[0]), '熔铸车间 · 车间员工', '部门 + 岗位');
assert.equal(deptLine({ dept_name: '质检部' }), '质检部', '没有岗位就只显示部门');
assert.equal(deptLine(members[3]), UNASSIGNED_LABEL, '没挂部门要给出明确提示');
assert.equal(deptLine(null), UNASSIGNED_LABEL, '空成员不能抛错');

// ── buildOrgTree：层级 ─────────────────────────────────────────────────
const { roots, assistants, unassigned } = buildOrgTree(orgMap, members);

assert.deepEqual(roots.map((node) => node.name), ['公司'], '只有「公司」是根');
const company = roots[0];
assert.deepEqual(
  company.children.map((node) => node.name),
  ['总经办', '销售部', '采购部', '生产部', '质检部'],
  '子部门保持 MES 顺序，空部门也要在架构图上',
);

const production = company.children.find((node) => node.name === '生产部');
assert.deepEqual(production.children.map((node) => node.name), ['熔铸车间'], '车间挂在生产部下面');
assert.deepEqual(production.children[0].members.map((row) => row.user_id), ['u1']);

// 合计人数含下级
assert.equal(production.total, 1, '生产部自身没有直属成员，但车间里有人');
assert.equal(production.children[0].total, 1);
assert.equal(company.total, 3, '公司合计 = 总经办 + 熔铸车间 + 质检部');
assert.equal(company.children.find((node) => node.name === '采购部').total, 0, '空部门合计 0');

// AI 助手不进部门树，单独一组
assert.deepEqual(assistants.map((row) => row.user_id), ['ai'], 'AI 助手单列');
assert.equal(
  [...company.children, production].some((node) => node.members.some((row) => row.role === 'assistant')),
  false,
  'AI 助手不能出现在任何部门节点里',
);
assert.deepEqual(unassigned.map((row) => row.user_id), ['u4'], '没部门的人进未分配');

// ── 降级 / 脏数据 ──────────────────────────────────────────────────────
const degraded = buildOrgTree(null, members);
assert.deepEqual(degraded.roots, [], '组织接口挂了就没有部门树');
assert.equal(degraded.unassigned.length, 4, '但人不丢，全部进未分配');
assert.deepEqual(degraded.assistants.map((row) => row.user_id), ['ai']);

// 父节点缺失 → 当根
const orphan = buildOrgTree(
  { departments: [{ id: 99, parent_id: 404, name: '孤立车间', path: '孤立车间', depth: 0, member_ids: ['u9'] }] },
  [{ user_id: 'u9', display_name: '临时工', role: 'member', dept_name: '孤立车间', position: '' }],
);
assert.deepEqual(orphan.roots.map((node) => node.name), ['孤立车间'], '父节点不存在时自己当根');

// 互相指向 → 不能死循环，节点也不能丢
const cyclic = buildOrgTree(
  {
    departments: [
      { id: 1, parent_id: 2, name: '甲', depth: 0, member_ids: ['u8'] },
      { id: 2, parent_id: 1, name: '乙', depth: 0, member_ids: [] },
    ],
  },
  [{ user_id: 'u8', display_name: '临时工2', role: 'member', dept_name: '甲', position: '' }],
);
const cyclicNames = new Set();
const walk = (node) => {
  cyclicNames.add(node.name);
  node.children.forEach(walk);
};
cyclic.roots.forEach(walk);
assert.deepEqual([...cyclicNames].sort(), ['乙', '甲'], '成环时两个部门都还在');

// 只有 AI 助手时不该出现部门树
const onlyAssistant = buildOrgTree(orgMap, [members[4]]);
assert.deepEqual(onlyAssistant.roots, []);
assert.deepEqual(onlyAssistant.assistants.map((row) => row.user_id), ['ai']);

assert.equal(ASSISTANT_LABEL, '常驻助手');

// ── groupByDeptPath（邀请弹窗） ────────────────────────────────────────
const directory = [
  { id: 'a', display_name: '王销售', username: 'sales', dept_path: '公司 / 销售部', position: '销售员' },
  { id: 'b', display_name: '李计划', username: 'planner', dept_path: '公司 / 生产部', position: '计划员' },
  { id: 'c', display_name: '张仓管', username: 'keeper', dept_path: '公司 / 仓储部', position: '仓管员' },
  { id: 'd', display_name: '临时账号', username: 'tmp', dept_path: '', position: '' },
  { id: 'e', display_name: '王销售2', username: 'sales2', dept_path: '公司 / 销售部', position: '销售员' },
];

const grouped = groupByDeptPath(directory);
assert.deepEqual(
  grouped.map((item) => item.path),
  ['公司 / 仓储部', '公司 / 生产部', '公司 / 销售部', UNASSIGNED_LABEL],
  '按部门路径排序，未分配最后',
);
assert.deepEqual(grouped.at(-2).members.map((item) => item.id), ['a', 'e'], '同部门要合并成一组');

console.log('org-view: 全部断言通过');
