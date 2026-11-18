#!/usr/bin/env node
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  getLayoutRecord,
  inspectLayout,
} from '../project/scripts/skill-workflow-utils.mjs';

const CALLER_CWD = process.env.INIT_CWD || process.cwd();
const [goalArg, deckArg, ...extraArgs] = process.argv.slice(2);

// 下面这段 try 是模块顶层执行（写完 sidecar 就结束），所以推断用的常量必须声明在
// 它之前——放到文件末尾会踩 const 的暂时性死区。
//
// 数组语义角色 → 页面意图。只在字段名/条目形状能证明语义时命中。
const ARRAY_ROLE_TO_PAGE_ROLE = [
  ['step', 'process'],
  ['metric', 'metric'],
  ['distribution', 'metric'],
  ['quadrant', 'comparison'],
  ['chapter', 'section'],
];

// 版式语义词（inspectLayout().roles）→ 页面意图，仅在结构证据缺失时兜底。
// 刻意不收 metrics/data/report 这类宽泛词：它们是「数据味」，不是「指标版式」，
// 收进来会让大数字页之类的版面被误判成指标页。
const LAYOUT_ROLE_TO_PAGE_ROLE = [
  ['process', 'process'],
  ['trend', 'timeline'],
  ['comparison', 'comparison'],
  ['case', 'case'],
  ['risks', 'risk'],
];

if (!goalArg || !deckArg) {
  console.error('Usage: node write_deck_sidecar.mjs <goal.json> <final-deck-file> [--workflow-run-id <id>]');
  process.exit(2);
}

try {
  const goalPath = path.resolve(CALLER_CWD, goalArg);
  const deckPath = path.resolve(CALLER_CWD, deckArg);
  const goal = JSON.parse(readFileSync(goalPath, 'utf8'));
  const workflowRunId = optionValue(extraArgs, '--workflow-run-id')
    ?? text(goal.workflowRunId)
    ?? '';
  const slides = Array.isArray(goal.slides) ? goal.slides : [];
  if (!slides.length) throw new Error('goal.json 没有 slides，无法生成标准内容文件');

  const pages = slides.map((slide, index) => pageFromSlide(slide, index));
  const sidecar = {
    schemaVersion: 1,
    title: text(goal.title) ?? '',
    audience: text(goal.audience) ?? '',
    workflowRunId,
    pages,
  };
  validateSidecar(sidecar);
  const sidecarPath = deckSidecarPath(deckPath);
  mkdirSync(path.dirname(sidecarPath), { recursive: true });
  const temporaryPath = `${sidecarPath}.tmp-${process.pid}`;
  writeFileSync(temporaryPath, `${JSON.stringify(sidecar, null, 2)}\n`, 'utf8');
  renameSync(temporaryPath, sidecarPath);
  console.log(`Wrote ${pages.length} page(s): ${sidecarPath}`);
} catch (error) {
  console.error(`Could not write deck sidecar: ${error?.message || error}`);
  process.exit(1);
}

function pageFromSlide(slide, index) {
  const layout = text(slide?.layout);
  const inspected = layout ? inspectLayout(layout, { compact: true }) : null;
  if (!inspected) throw new Error(`第 ${index + 1} 页的 layout 无法识别`);

  const entries = visibleCopyEntries(slide?.props || {}, inspected);
  if (!entries.length) throw new Error(`第 ${index + 1} 页没有可见文案`);
  const titleEntries = entries.filter(entry => entry.role === 'title');
  const title = unique(titleEntries.map(entry => entry.value)).join(' ')
    || entries[0].value;
  const titleValues = new Set(titleEntries.length ? titleEntries.map(entry => entry.value) : [title]);
  const points = unique(entries
    .map(entry => entry.value)
    .filter(value => !titleValues.has(value)));
  const role = pageRole({ layout, inspected, index, points });

  return {
    id: pageId(slide, index),
    role,
    title,
    points,
    note: '',
  };
}

function visibleCopyEntries(props, inspected) {
  const entries = [];
  for (const copyPath of inspected.copyKeys || []) {
    const role = inspected.copyRoles?.[copyPath] || 'eyebrow';
    for (const value of valuesAtCopyPath(props, copyPath, inspected)) {
      const copy = text(value);
      if (copy) entries.push({ role, value: copy });
    }
  }
  return entries;
}

function valuesAtCopyPath(root, copyPath, inspected) {
  const segments = copyPath.split('.').filter(Boolean);
  let values = [root];
  let traversed = '';
  for (const rawSegment of segments) {
    const isArray = rawSegment.endsWith('[]');
    const key = isArray ? rawSegment.slice(0, -2) : rawSegment;
    traversed = traversed ? `${traversed}.${key}` : key;
    const next = [];
    for (const value of values) {
      const child = value && typeof value === 'object' ? value[key] : undefined;
      if (isArray && Array.isArray(child)) {
        next.push(...child.slice(0, visibleArrayCount(traversed, child, root, inspected)));
      } else if (!isArray && child !== undefined) {
        next.push(child);
      }
    }
    values = next;
  }
  return values.flatMap(visibleLeafValues);
}

function visibleArrayCount(arrayPath, values, props, inspected) {
  const metadata = (inspected.arrayMeta || []).find(item => item.key === arrayPath);
  const count = metadata?.countKey ? Number(props?.[metadata.countKey]) : NaN;
  return Number.isFinite(count) ? Math.max(0, Math.min(values.length, Math.trunc(count))) : values.length;
}

function visibleLeafValues(value) {
  if (typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))) {
    return [String(value)];
  }
  if (Array.isArray(value)) return value.flatMap(visibleLeafValues);
  if (!value || typeof value !== 'object') return [];
  if (value.type && value.props && Object.hasOwn(value.props, 'children')) {
    return visibleLeafValues(value.props.children);
  }
  return [];
}

function pageRole({ layout, inspected, index, points }) {
  // 这份 deck 是刚渲染好的成品，第 1 页就是封面。以前靠版式启发式判断，猜错会让
  // 整份重排被 DeckSpec 的「必须有封面」校验拒掉；不猜了。
  if (index === 0) return 'cover';
  if (isClosingLayout(layout)) return 'closing';
  if (!points.length) return 'section';

  // 专用版式（流程/时间线/指标/对比）要真的命中，模板的专属版面才用得上。
  // 判据分两层：先看数组的语义角色（结构证据，准），再看版式语义词（启发式，兜底）。
  const arrayRoles = new Set((inspected.arrayMeta || []).map(meta => meta.role));
  const layoutRoles = new Set(inspected.roles || []);
  for (const [arrayRole, mappedRole] of ARRAY_ROLE_TO_PAGE_ROLE) {
    if (!arrayRoles.has(arrayRole)) continue;
    // 步骤数组落在「趋势/时间轴」版式上时，它是时间线不是普通流程。
    return arrayRole === 'step' && layoutRoles.has('trend') ? 'timeline' : mappedRole;
  }
  for (const [layoutRole, mappedRole] of LAYOUT_ROLE_TO_PAGE_ROLE) {
    if (layoutRoles.has(layoutRole)) return mappedRole;
  }
  // 不再「末页一律结束页」：正文页垫底时会被套上结束版式，白丢内容。
  // closing 只认版式本身是封底/结语（见 isClosingLayout）。
  return 'content';
}

// 与上游 isClosingLikePage 同一套判定（该函数未导出，这里按 layout 复刻）。
function isClosingLayout(layout) {
  const record = getLayoutRecord(layout);
  if (!record) return false;
  const slot = String(record.page?.slot || '').toLowerCase();
  const label = String(record.page?.label || '').toLowerCase();
  const text = `${slot} ${label}`;
  return /(^|[\s_-])(closing|contact|join|end|endcap|colophon|appendix)([\s_-]|$)/.test(text)
    || label.startsWith('封底')
    || label.startsWith('结语')
    || label.startsWith('致谢')
    || label.startsWith('谢谢');
}

function pageId(slide, index) {
  const candidate = text(slide?.id) || text(slide?.key) || text(slide?.slideKey);
  return candidate ? candidate.slice(0, 64) : `p${index + 1}`;
}

function deckSidecarPath(deckPath) {
  const extension = path.extname(deckPath);
  const basename = path.basename(deckPath, extension);
  return path.join(path.dirname(deckPath), `${basename}.deck.json`);
}

function optionValue(args, name) {
  const index = args.indexOf(name);
  if (index < 0) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} 缺少值`);
  return String(value).trim();
}

function text(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return normalized || null;
}

function validateSidecar(sidecar) {
  if (sidecar.pages.length > 40) throw new Error('标准内容最多支持 40 页');
  if (sidecar.title.length > 200) throw new Error('汇报标题超过 200 字');
  if (sidecar.audience.length > 200) throw new Error('汇报对象超过 200 字');
  if (sidecar.workflowRunId.length > 64) throw new Error('任务标识超过 64 字');
  let totalChars = sidecar.title.length + sidecar.audience.length;
  for (const [index, page] of sidecar.pages.entries()) {
    if (page.title.length > 200) throw new Error(`第 ${index + 1} 页标题超过 200 字`);
    totalChars += page.title.length + (page.note?.length || 0);
    for (const point of page.points) {
      if (point.length > 2000) throw new Error(`第 ${index + 1} 页要点超过 2000 字`);
      totalChars += point.length;
    }
  }
  if (totalChars > 100_000) throw new Error('标准内容文字总量超过 100000 字');
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}
