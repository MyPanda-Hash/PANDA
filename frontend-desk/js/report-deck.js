/**
 * 报表的「文档版式」渲染：把 Markdown 正文排成接近 PPT 的观感。
 *
 * 为什么不一上来就用 Markdown：报表是拿去过会的东西，
 * 纯文本流看不出层级；这里按小节语义换版式——
 *   概览 → 指标卡   *分布 → 条形列表   重点关注/风险 → 警示条目   建议 → 责任人卡片
 * 解析规则依赖报表的固定结构（render_report 产出的 ## / ### / - / 1.），
 * 换结构就要同步改这里。
 */
import { el } from './util.js'

/** 把正文切成 { title, sections:[{name, lines}] } */
export function parseReport(markdown) {
  const text = String(markdown == null ? '' : markdown).replace(/\r\n?/g, '\n')
  let title = ''
  const sections = []
  let current = null

  for (const raw of text.split('\n')) {
    const line = raw.replace(/\s+$/, '')
    const h2 = /^##\s+(.*)$/.exec(line)
    const h3 = /^###\s+(.*)$/.exec(line)
    if (h2) {
      if (!title) title = h2[1].trim()
      continue
    }
    if (h3) {
      current = { name: h3[1].trim(), lines: [] }
      sections.push(current)
      continue
    }
    if (!current) {
      // 标题之前的内容（模型有时先写一段）也收进首节
      current = { name: '', lines: [] }
      sections.push(current)
    }
    if (line.trim()) current.lines.push(line.trim())
  }
  return { title, sections: sections.filter((s) => s.name || s.lines.length) }
}

/** 去掉 ** 与 ` 标记，取纯文本 */
function plain(value) {
  return String(value == null ? '' : value)
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .trim()
}

/** 从「新发现 **9** 条待办」这类句子里抽 (标签, 数值) */
function extractMetrics(lines) {
  const out = []
  const re = /([\u4e00-\u9fa5A-Za-z]{2,6})\s*\*\*(\d+(?:\.\d+)?)\*\*/g
  for (const line of lines) {
    let m
    while ((m = re.exec(line)) !== null) {
      const label = m[1].replace(/^(发现|累计|共)$/, m[1])
      if (out.some((item) => item.label === label)) continue
      out.push({ label, value: m[2] })
      if (out.length >= 4) return out
    }
  }
  return out
}

/** 「待审批：3 条」/「李计划 14 条、赵车间 13 条」→ [{label,value}] */
function extractBars(lines) {
  const out = []
  for (const line of lines) {
    const row = /^[-*]\s*(.+?)[：:]\s*(\d+)\s*条/.exec(line)
    if (row) {
      out.push({ label: plain(row[1]), value: Number(row[2]) })
      continue
    }
    // 一行里塞了多个人/多类：甲 14 条、乙 13 条
    const inline = /([^、，,：:\s]+)\s*(\d+)\s*条/g
    let m
    while ((m = inline.exec(plain(stripMarker(line)))) !== null) {
      out.push({ label: plain(m[1]), value: Number(m[2]) })
    }
  }
  return out
}

function isList(lines) {
  return lines.length > 0 && lines.every((l) => /^([-*]|\d+[.、])\s+/.test(l))
}

function stripMarker(line) {
  return line.replace(/^([-*]|\d+[.、])\s+/, '')
}

function renderCover(title, periodText) {
  return el('div', { cls: 'rdeck__cover' }, [
    el('div', { cls: 'rdeck__kicker', text: 'PRODUCTION / MES REPORT' }),
    el('h1', { cls: 'rdeck__title', text: plain(title) || '报表' }),
    periodText ? el('div', { cls: 'rdeck__period', text: periodText }) : null,
  ])
}

function renderMetricSection(name, metrics, rest) {
  return el('section', { cls: 'rdeck__sec' }, [
    el('div', { cls: 'rdeck__sec-head' }, [
      el('span', { cls: 'rdeck__num', text: '' }),
      el('h2', { text: name }),
    ]),
    el('div', { cls: 'rdeck__metrics' }, metrics.map((item) =>
      el('div', { cls: 'rdeck__metric' }, [
        el('div', { cls: 'rdeck__metric-value', text: item.value }),
        el('div', { cls: 'rdeck__metric-label', text: item.label }),
      ]),
    )),
    ...rest.map((line) => el('p', { cls: 'rdeck__p', text: plain(stripMarker(line)) })),
  ])
}

function renderBarsSection(name, bars) {
  const max = Math.max(...bars.map((b) => b.value), 1)
  return el('section', { cls: 'rdeck__sec' }, [
    el('div', { cls: 'rdeck__sec-head' }, [el('h2', { text: name })]),
    el('div', { cls: 'rdeck__bars' }, bars.map((bar) =>
      el('div', { cls: 'rdeck__bar-row' }, [
        el('span', { cls: 'rdeck__bar-label', text: bar.label }),
        el('span', { cls: 'rdeck__bar-track' }, [
          el('span', {
            cls: 'rdeck__bar-fill',
            attrs: { style: 'width:' + Math.max(6, Math.round((bar.value / max) * 100)) + '%' },
          }),
        ]),
        el('span', { cls: 'rdeck__bar-value', text: String(bar.value) }),
      ]),
    )),
  ])
}

function renderAlertsSection(name, items, severe) {
  return el('section', { cls: 'rdeck__sec' }, [
    el('div', { cls: 'rdeck__sec-head' }, [el('h2', { text: name })]),
    el('ul', { cls: 'rdeck__alerts' + (severe ? ' is-severe' : '') }, items.map((line) =>
      el('li', { text: plain(stripMarker(line)) }),
    )),
  ])
}

function renderAdviceSection(name, items) {
  return el('section', { cls: 'rdeck__sec' }, [
    el('div', { cls: 'rdeck__sec-head' }, [el('h2', { text: name })]),
    el('div', { cls: 'rdeck__advice' }, items.map((line, index) => {
      // 去掉 ⚠️🔥 这类前缀符号，否则会跟着进责任人字段
      const text = plain(stripMarker(line)).replace(/^[\u2600-\u27BF\uD83C-\uD83E\uDD00-\uDDFF\uFE0F\s]+/u, '').trim()
      // 「生产计划负责人（李计划）：尽快…」→ 责任人单独一行，便于扫
      const m = /^([^：:]{2,20})[：:]\s*(.+)$/.exec(text)
      const owner = m ? m[1].trim() : ''
      const body = m ? m[2].trim() : text
      return el('div', { cls: 'rdeck__advice-item' }, [
        el('span', { cls: 'rdeck__advice-idx', text: String(index + 1).padStart(2, '0') }),
        el('div', { cls: 'rdeck__advice-main' }, [
          owner ? el('div', { cls: 'rdeck__advice-owner', text: owner }) : null,
          el('div', { cls: 'rdeck__advice-body', text: body }),
        ]),
      ])
    })),
  ])
}

function renderProseSection(name, lines) {
  const paras = []
  const bullets = []
  for (const line of lines) {
    if (/^([-*]|\d+[.、])\s+/.test(line)) bullets.push(plain(stripMarker(line)))
    else paras.push(plain(line))
  }
  return el('section', { cls: 'rdeck__sec' }, [
    name ? el('div', { cls: 'rdeck__sec-head' }, [el('h2', { text: name })]) : null,
    ...paras.map((p) => el('p', { cls: 'rdeck__p', text: p })),
    bullets.length ? el('ul', { cls: 'rdeck__bullets' }, bullets.map((b) => el('li', { text: b }))) : null,
  ])
}

/** 渲染整份报表 */
export function renderReportDeck(target, markdown) {
  const { title, sections } = parseReport(markdown)
  const period = /（(.+?)）/.exec(title)
  const periodText = period ? period[1] : ''
  const cleanTitle = title.replace(/（.*?）/, '').trim()

  target.append(renderCover(cleanTitle, periodText))

  sections.forEach((section, index) => {
    const name = section.name
    const lines = section.lines
    const numbered = String(index + 1).padStart(2, '0')

    if (/概览|总览/.test(name)) {
      const metrics = extractMetrics(lines)
      if (metrics.length >= 2) {
        target.append(renderMetricSection(name, metrics, []))
        return
      }
    }
    if (/分布|构成|已处理|新增/.test(name)) {
      const bars = extractBars(lines)
      if (bars.length) {
        target.append(renderBarsSection(name, bars))
        return
      }
    }
    if (/重点关注|风险/.test(name) && isList(lines)) {
      target.append(renderAlertsSection(name, lines, /重点关注/.test(name)))
      return
    }
    if (/建议|措施/.test(name) && isList(lines)) {
      target.append(renderAdviceSection(name, lines))
      return
    }
    target.append(renderProseSection(name, lines))
  })

  // 给每个小节补编号（封面之后从 01 起）
  target.querySelectorAll('.rdeck__sec-head .rdeck__num').forEach((node, i) => {
    node.textContent = String(i + 1).padStart(2, '0')
  })

  return target
}