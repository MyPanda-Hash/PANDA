// 人力地图的纯函数：把「MES 部门树 + 群成员」拼成可直接渲染的分组。
//
// 单独成模块是为了能在 node 里直接测（tests/org-view.test.mjs）：
// 分组一旦写错，界面上表现成「人掉出列表 / 出现两个同名部门」，很难靠肉眼发现。
//
// 约定：
//   - AI 助手不是真实员工，没有部门，但它是群成员；单独成「常驻助手」组，
//     既不混进部门层级，也不会让人以为它从成员列表里消失了。
//   - 没有部门的部门靠后，且永远是最后一组。
//   - 部门按 MES 给的顺序（已按树先序排好）输出，前端不重新排序。

export const UNASSIGNED_LABEL = '未分配部门';
export const ASSISTANT_LABEL = '常驻助手';

/** 成员行下方的部门副标题：「熔铸车间 · 车间员工」；没有部门时给「未分配部门」。 */
export function deptLine(member) {
  if (!member) return UNASSIGNED_LABEL;
  const name = String(member.dept_name || '').trim();
  if (!name) return UNASSIGNED_LABEL;
  const position = String(member.position || '').trim();
  return position ? `${name} · ${position}` : name;
}

/**
 * 组织架构树：把 MES 的部门树原样搬过来，成员挂在各自的部门节点上。
 *
 * 返回 `{roots, assistants, unassigned}`：
 *   - `roots` 是部门树的根（`公司` 一层），每个节点带 `members` / `children` / `total`；
 *   - `total` 是含下级的合计人数，父部门显示「这个部门体系里一共几个人」；
 *   - 空部门也保留：组织架构图要看得见编制，不能因为没人就凭空消失。
 * 传空 orgMap 时返回空树 + 全员未分配，界面不会白屏。
 */
export function buildOrgTree(orgMap, members) {
  const people = (members || []).filter((row) => row && row.role !== 'assistant');
  const assistants = (members || []).filter((row) => row && row.role === 'assistant');
  const byUser = new Map(people.map((row) => [row.user_id, row]));
  const departments = (orgMap && orgMap.departments) || [];
  // 群里只有 AI 助手时没必要铺一张空架构图
  if (!people.length) {
    return { roots: [], assistants, unassigned: [] };
  }

  const nodes = new Map();
  for (const dept of departments) {
    const key = String(dept.id);
    nodes.set(key, {
      id: dept.id,
      name: dept.name || '',
      path: dept.path || dept.name || '',
      depth: Number(dept.depth) || 0,
      members: [],
      children: [],
      total: 0,
    });
  }

  const placed = new Set();
  for (const dept of departments) {
    const node = nodes.get(String(dept.id));
    for (const userId of dept.member_ids || []) {
      const person = byUser.get(userId);
      if (!person) continue;
      node.members.push(person);
      placed.add(userId);
    }
  }

  // 先按 parent_id 建原始父子关系；父节点缺失（脏数据）或自指就当根
  const rawChildren = new Map();
  const roots = [];
  for (const dept of departments) {
    const node = nodes.get(String(dept.id));
    const parent = nodes.get(String(dept.parent_id ?? 0));
    if (!parent || parent === node) {
      roots.push(node);
      continue;
    }
    if (!rawChildren.has(parent)) rawChildren.set(parent, []);
    rawChildren.get(parent).push(node);
  }

  // 连边时剪掉闭环的那一条：成环的脏数据既不能让渲染无限递归，
  // 也不能把部门吞掉——剪掉后它自己会成为根。
  const linked = new Set();
  const link = (node, path) => {
    if (linked.has(node)) return;
    linked.add(node);
    const nextPath = new Set(path);
    nextPath.add(node);
    node.children = (rawChildren.get(node) || []).filter((child) => !path.has(child));
    for (const child of node.children) link(child, nextPath);
  };
  for (const root of roots) link(root, new Set());
  for (const node of nodes.values()) {
    if (!linked.has(node)) {
      roots.push(node);
      link(node, new Set());
    }
  }

  const tally = (node) => {
    node.total = node.members.length + node.children.reduce((sum, child) => sum + tally(child), 0);
    return node.total;
  };
  for (const root of roots) tally(root);

  const unassigned = people.filter((row) => !placed.has(row.user_id));
  return { roots, assistants, unassigned };
}

/**
 * 邀请弹窗按部门分组：`[{path, label, members}]`。
 * 「未分配部门」永远排最后，其余按部门路径字典序，方便从上往下扫。
 */
export function groupByDeptPath(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    if (!row) continue;
    const path = String(row.dept_path || '').trim() || UNASSIGNED_LABEL;
    if (!groups.has(path)) groups.set(path, []);
    groups.get(path).push(row);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => {
      if (left === right) return 0;
      if (left === UNASSIGNED_LABEL) return 1;
      if (right === UNASSIGNED_LABEL) return -1;
      return left.localeCompare(right, 'zh-Hans-CN');
    })
    .map(([path, items]) => ({ path, label: path, members: items }));
}
