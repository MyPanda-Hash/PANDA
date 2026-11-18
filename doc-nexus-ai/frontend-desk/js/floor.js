// 车间现场看板（#/floor）：车间主任打开就该看到「现在产线什么情况、哪里卡住」。
//
// 与管理者简报的分工：
//   简报  → 一天几条要拍板的（决策）
//   现场  → 在制工单进度、今日报工、停滞与逾期（执行）
// 与「待办」的分工：待办是「要谁去做」，现场是「做到哪了」。

import { BRIEFING } from './api.js';
import { el, clear } from './util.js';

let host = null;
let data = null;
let loading = false;
let error = null;

export async function mount(root, ctx) {
  host = root;
  clear(host);
  ctx = ctx || {};
  await load(ctx);
}

export async function unmount() {
  host = null;
  data = null;
  error = null;
}

async function load(ctx) {
  loading = true;
  error = null;
  render(ctx);
  try {
    const payload = await BRIEFING.floor();
    data = (payload && payload.data) || payload || null;
  } catch (err) {
    error = err;
  } finally {
    loading = false;
    render(ctx);
  }
}

function statCard(s) {
  return el('div', { cls: 'floor__stat' }, [
    el('div', { cls: 'floor__stat-n', attrs: { 'data-tone': s.tone || '' }, text: String(s.value) }),
    el('div', { cls: 'floor__stat-l', text: s.label }),
    s.hint ? el('div', { cls: 'floor__stat-h', text: s.hint }) : null,
  ].filter(Boolean));
}

function progressBar(pct) {
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  return el('div', { cls: 'floor__bar', attrs: { role: 'progressbar', 'aria-valuenow': String(clamped), 'aria-valuemin': '0', 'aria-valuemax': '100' } }, [
    el('div', { cls: 'floor__bar-fill', attrs: { style: 'width:' + clamped + '%' } }),
  ]);
}

function orderRow(o) {
  const late = (o.overdueDays || 0) > 0;
  return el('div', { cls: 'floor__row' + (late ? ' is-late' : '') }, [
    el('div', { cls: 'floor__row-main' }, [
      el('div', { cls: 'floor__row-t' }, [
        el('span', { text: o.product || o.docNo }),
        late ? el('span', { cls: 'floor__tag floor__tag--late', text: '逾期 ' + o.overdueDays + ' 天' }) : null,
      ].filter(Boolean)),
      el('div', { cls: 'floor__row-s', text: o.docNo + (o.workshop ? ' · ' + o.workshop : '') + (o.dueDate ? ' · 预完工 ' + o.dueDate : '') }),
    ]),
    el('div', { cls: 'floor__progress' }, [
      progressBar(o.progress),
      el('div', { cls: 'floor__progress-n', text: o.doneQty + ' / ' + o.planQty }),
    ]),
  ]);
}

function render(ctx) {
  if (!host) return;
  clear(host);
  const box = el('div', { cls: 'floor' });

  if (loading && !data) {
    box.append(el('div', { cls: 'skeleton', attrs: { style: 'height:120px;border-radius:8px' } }));
    host.append(box);
    return;
  }
  if (error && !data) {
    box.append(el('div', { cls: 'empty' }, [
      el('h3', { text: '现场看板暂时打不开' }),
      el('p', { text: error.message || '加载失败' }),
      el('button', { cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' }, on: { click: () => load(ctx) } }),
    ]));
    host.append(box);
    return;
  }

  const d = data || {};
  const who = (ctx.user && (ctx.user.realName || ctx.user.real_name || ctx.user.username)) || '';

  box.append(el('div', { cls: 'floor__head' }, [
    el('div', { cls: 'floor__hello', text: (who ? who + '，' : '') + '现场情况' }),
    el('div', { cls: 'floor__meta', text: (d.day || '') + ' · 一号工厂 · 车间主任视图' }),
  ]));
  box.append(el('div', { cls: 'floor__headline', text: d.headline || '暂无现场数据。' }));

  const stats = el('div', { cls: 'floor__stats' });
  for (const s of d.stats || []) stats.append(statCard(s));
  box.append(stats);

  // 停滞优先：主任最先要知道「哪张单没动」
  if ((d.stalled || []).length) {
    box.append(el('div', { cls: 'floor__sect' }, [
      el('div', { cls: 'floor__sect-h' }, [
        el('span', { text: '卡住了' }),
        el('span', { text: '开工后一直没报工，建议先催这里' }),
      ]),
      ...d.stalled.map((o) => el('div', { cls: 'floor__alert' }, [
        el('div', { cls: 'floor__alert-t', text: o.product + ' · ' + o.docNo }),
        el('div', { cls: 'floor__alert-d', text: '已闲置 ' + o.idleDays + ' 天未报工，计划 ' + o.planQty + ' 件' }),
      ])),
    ]));
  }

  box.append(el('div', { cls: 'floor__sect' }, [
    el('div', { cls: 'floor__sect-h' }, [
      el('span', { text: '在制工单' }),
      el('span', { text: (d.hiddenOrders ? '还有 ' + d.hiddenOrders + ' 张未显示' : '进度低的排在前面') }),
    ]),
    ...(d.orders || []).length
      ? d.orders.map(orderRow)
      : [el('div', { cls: 'floor__row' }, [el('div', { cls: 'floor__row-s', text: '当前没有在制工单。' })])],
  ]));

  box.append(el('div', { cls: 'floor__sect' }, [
    el('div', { cls: 'floor__sect-h' }, [
      el('span', { text: '今日报工' }),
      el('span', { text: (d.reports || []).length ? '共 ' + d.reports.length + ' 道工序' : '今天还没有报工' }),
    ]),
    ...(d.reports || []).length
      ? d.reports.map((r) => el('div', { cls: 'floor__row' }, [
        el('div', { cls: 'floor__row-main' }, [
          el('div', { cls: 'floor__row-t', text: (r.process || '报工') + ' · ' + r.qty + ' 件' }),
          el('div', { cls: 'floor__row-s', text: (r.mo || '') + (r.worker ? ' · ' + r.worker : '') }),
        ]),
      ]))
      : [el('div', { cls: 'floor__row' }, [el('div', { cls: 'floor__row-s', text: '今天还没有报工记录。' })])],
  ]));

  host.append(box);
}
