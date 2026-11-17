import { el, clear } from './util.js'

/**
 * 会话正文渲染：标题、列表、粗体、行内代码、代码块、引用、分隔线、表格、任务列表。
 *
 * 仍然只用 createTextNode / textContent 拼装，模型输出永远不当 HTML 解析——
 * 这里支持的是「结构」，不是「执行任意标签」。
 *
 * 之前只认段落/列表/粗体，模型输出 ### 概览、**粗体** 这类内容时，
 * 标题会原样显示成「### 概览」，看着像没渲染（2026-11-17 用户截图反馈）。
 */
export function renderMarkdown(target, text) {
  clear(target);
  const source = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
  if (!source.trim()) return;

  const lines = source.split('\n');
  let paragraph = [];
  let list = null;      // { type: 'ul'|'ol', node }
  let quote = null;     // 引用块累积行
  let codeFence = null; // { lang, lines: [] }
  let table = null;     // { head: [], rows: [] }

  const flushParagraph = () => {
    if (!paragraph.length) return;
    target.append(inlineNode('p', paragraph.join('\n')));
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    target.append(list.node);
    list = null;
  };
  const beginList = (type, start) => {
    if (list && list.type === type) return;
    flushList();
    const node = document.createElement(type);
    if (type === 'ol' && start && start !== 1) node.setAttribute('start', String(start));
    list = { type, node };
  };
  const flushQuote = () => {
    if (!quote) return;
    const box = document.createElement('blockquote');
    box.className = 'chat-quote';
    for (const chunk of quote) box.append(inlineNode('p', chunk));
    target.append(box);
    quote = null;
  };
  const flushCode = () => {
    if (!codeFence) return;
    target.append(buildCodeBlock(codeFence.lang, codeFence.lines.join('\n')));
    codeFence = null;
  };
  const flushTable = () => {
    if (!table) return;
    target.append(buildTable(table.head, table.rows));
    table = null;
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
    flushTable();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].replace(/\s+$/, '');

    // 代码块：围栏内原样保留（含空行与缩进）
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (codeFence) {
      if (fence) {
        flushCode();
      } else {
        codeFence.lines.push(lines[i]);
      }
      continue;
    }
    if (fence) {
      flushAll();
      codeFence = { lang: fence[1] || '', lines: [] };
      continue;
    }

    if (!line.trim()) {
      flushAll();
      continue;
    }

    // 表格：首行 + 分隔行（|---|）才认，避免把普通竖线文本当表
    const isTableRow = /^\s*\|.*\|\s*$/.test(line);
    if (isTableRow) {
      const cells = splitTableRow(line);
      if (!table && isTableSeparator(lines[i + 1])) {
        flushAll();
        table = { head: cells, rows: [] };
        i += 1; // 跳过分隔行
        continue;
      }
      if (table) {
        if (!isTableSeparator(line)) table.rows.push(cells);
        continue;
      }
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushAll();
      const level = Math.min(5, Math.max(3, heading[1].length + 2)); // h1-h2 也压到 h3，避免会话里字号失控
      target.append(inlineNode('h' + level, heading[2]));
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushAll();
      const hr = document.createElement('hr');
      hr.className = 'chat-hr';
      target.append(hr);
      continue;
    }

    const quoteLine = line.match(/^\s*>\s?(.*)$/);
    if (quoteLine) {
      flushParagraph();
      flushList();
      flushTable();
      if (!quote) quote = [];
      quote.push(quoteLine[1]);
      continue;
    }

    const task = line.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/);
    if (task) {
      flushParagraph();
      flushQuote();
      flushTable();
      beginList('ul', 1);
      const item = document.createElement('li');
      item.className = 'chat-task';
      const box = document.createElement('span');
      box.className = 'chat-task__box';
      box.textContent = task[1].toLowerCase() === 'x' ? '☑' : '☐';
      item.append(box);
      appendInline(item, task[2]);
      list.node.append(item);
      continue;
    }

    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const ordered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (bullet || ordered) {
      flushParagraph();
      flushQuote();
      flushTable();
      beginList(bullet ? 'ul' : 'ol', ordered ? Number(ordered[1]) : 1);
      const item = document.createElement('li');
      appendInline(item, bullet ? bullet[1] : ordered[2]);
      list.node.append(item);
      continue;
    }

    flushList();
    flushQuote();
    flushTable();
    paragraph.push(line.trim());
  }

  flushAll();
  flushCode();
}

function isTableSeparator(line) {
  return typeof line === 'string' && /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.includes('-');
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^|/, '')
    .replace(/|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

/** 代码块：等宽、可横向滚动，右上角一个复制按钮。 */
function buildCodeBlock(lang, code) {
  const wrap = document.createElement('div');
  wrap.className = 'chat-pre';
  const bar = document.createElement('div');
  bar.className = 'chat-pre__bar';
  const label = document.createElement('span');
  label.className = 'chat-pre__lang';
  label.textContent = lang || 'text';
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'chat-pre__copy';
  copy.textContent = '复制';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.textContent = '已复制';
      setTimeout(() => { copy.textContent = '复制'; }, 1600);
    } catch (_) {
      copy.textContent = '复制失败';
    }
  });
  bar.append(label, copy);
  const pre = document.createElement('pre');
  const codeEl = document.createElement('code');
  codeEl.textContent = code;
  pre.append(codeEl);
  wrap.append(bar, pre);
  return wrap;
}

function buildTable(head, rows) {
  const wrap = document.createElement('div');
  wrap.className = 'chat-table-wrap';
  const table = document.createElement('table');
  table.className = 'chat-table';
  const thead = document.createElement('thead');
  const htr = document.createElement('tr');
  for (const cell of head) {
    const th = document.createElement('th');
    appendInline(th, cell);
    htr.append(th);
  }
  thead.append(htr);
  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (let c = 0; c < head.length; c += 1) {
      const td = document.createElement('td');
      appendInline(td, row[c] == null ? '' : row[c]);
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

/** 行内节点：**粗体** 与 \`代码\` 交替拆分，全部走文本节点。 */
function inlineNode(tag, text) {
  const node = document.createElement(tag);
  appendInline(node, text);
  return node;
}

function appendInline(node, text) {
  // 捕获组进 split 结果：普通文本与 **粗体**/\`代码\` 交替出现
  for (const part of String(text).split(/(\*\*[^*]+\*\*|\x60[^\x60]+\x60)/g)) {
    if (!part) continue;
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
      node.append(el('strong', { text: part.slice(2, -2) }));
    } else if (part.length > 2 && part.startsWith('\x60') && part.endsWith('\x60')) {
      node.append(el('code', { cls: 'chat-code', text: part.slice(1, -1) }));
    } else {
      node.append(document.createTextNode(part));
    }
  }
}
