// 一线员工看板（#/my）：只回答「我手上还有什么要干」。
//
// 与车间主任「现场」的区别：
//   现场 → 全车间在制与卡点（要铺开看）
//   我的 → 还没报工的工序（要一眼看完、点进去就干）
// 数据现实：派工单没有「工人/班组」字段，报工的工人名也多为空，
// 所以这里如实标注「未登记工人」，不把别人的产量算到你头上。

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
    const payload = await BRIEFING.mine();
    data = (payload && payload.data) || payload || null;
  } catch (err) {
    error = err;
  } finally {
    loading = false;
    render(ctx);
  }
}

function stat(s) {
  return el('div', { cls: 'my__stat' }, [
    el('div', { cls: 'my__stat-n', attrs: { 'data-tone': s.tone || '' }, text: String(s.value) }),
    el('div', { cls: 'my__stat-l', text: s.label }),
    s.hint ? el('div', { cls: 'my__stat-h', text: s.hint }) : null,
  ].filter(Boolean));
}

function render(ctx) {
  if (!host) return;
  clear(host);
  const box = el('div', { cls: 'my' });

  if (loading && !data) {
    box.append(el('div', { cls: 'skeleton', attrs: { style: 'height:120px;border-radius:8px' } }));
    host.append(box);
    return;
  }
  if (error && !data) {
    box.append(el('div', { cls: 'empty' }, [
      el('h3', { text: '我的活儿暂时打不开' }),
      el('p', { text: error.message || '加载失败' }),
      el('button', { cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' }, on: { click: () => load(ctx) } }),
    ]));
    host.append(box);
    return;
  }

  const d = data || {};
  const who = (ctx.user && (ctx.user.displayName || ctx.user.display_name || ctx.user.username)) || '';

  box.append(el('div', { cls: 'my__head' }, [
    el('div', { cls: 'my__hello', text: (who ? who + '，' : '') + '今天要干的活' }),
    el('div', { cls: 'my__meta', text: (d.day || '') + ' · 一号工厂 · 一线视图' }),
  ]));
  box.append(el('div', { cls: 'my__headline', text: d.headline || '暂无派工。' }));

  const stats = el('div', { cls: 'my__stats' });
  for (const s of d.stats || []) stats.append(stat(s));
  box.append(stats);

  // 待报工：一屏能扫完，一项一行
  const pend = d.pending || [];
  box.append(el('div', { cls: 'my__sect-h' }, [
    el('span', { text: '待报工' }),
    el('span', { text: pend.length ? (d.hidden ? '还有 ' + d.hidden + ' 道未显示' : '按工单排') : '都报完了' }),
  ]));

  if (!pend.length) {
    box.append(el('div', { cls: 'my__row' }, [
      el('div', { cls: 'my__row-s', text: '当前没有待报工的工序。' }),
    ]));
  }
  for (const o of pend) {
    box.append(el('div', { cls: 'my__row' }, [
      el('div', { cls: 'my__row-main' }, [
        el('div', { cls: 'my__row-t' }, [
          el('span', { text: (o.process || '工序') + ' · ' + o.qty + ' 件' }),
          o.dueDate ? el('span', { cls: 'my__tag', text: '预完工 ' + o.dueDate }) : null,
        ].filter(Boolean)),
        el('div', { cls: 'my__row-s', text: (o.mo || '') + (o.product ? ' · ' + o.product : '') + (o.reported ? ' · 已报 ' + o.reported : '') }),
      ]),
      el('div', { cls: 'my__row-acts' }, [
        // 报工仍在 MES 里完成：这里只负责把人送到对应单据
        el('a', {
          cls: 'btn btn--sm btn--primary',
          attrs: { href: '/mes/#/panelx/list/DISPATCH', target: '_blank', rel: 'noopener noreferrer' },
          text: '去报工',
        }),
      ]),
    ]));
  }

  // 我的产出：没有记录时不单开一节，否则和下面「未归属的报工」读起来像两段空话
  const rs = d.reports || [];
  if (rs.length) {
    box.append(el('div', { cls: 'my__sect-h' }, [
      el('span', { text: '我的报工' }),
      el('span', { text: '近 7 天 ' + rs.length + ' 条' }),
    ]));
  }
  for (const r of rs) {
    box.append(el('div', { cls: 'my__row' }, [
      el('div', { cls: 'my__row-main' }, [
        el('div', { cls: 'my__row-t', text: (r.process || '报工') + ' · ' + r.qty + ' 件' }),
        el('div', { cls: 'my__row-s', text: (r.day || '') + ' · ' + (r.mo || '') + (r.worker ? ' · ' + r.worker : ' · 未登记工人') }),
      ]),
    ]));
  }

  // 未归属的报工：确实有产量，但没填工人，算不到人。
  // 单独列出来而不是并进「我的产出」——并进去等于替系统掩盖了数据问题。
  const un = d.unassignedReports || [];
  if (un.length) {
    box.append(el('div', { cls: 'my__sect-h' }, [
      el('span', { text: '未归属的报工' }),
      el('span', { text: '没填工人，系统算不到是谁' }),
    ]));
    for (const r of un) {
      box.append(el('div', { cls: 'my__row my__row--muted' }, [
        el('div', { cls: 'my__row-main' }, [
          el('div', { cls: 'my__row-t', text: (r.process || '报工') + ' · ' + r.qty + ' 件' }),
          el('div', { cls: 'my__row-s', text: (r.day || '') + ' · ' + (r.mo || '') }),
        ]),
      ]));
    }
    const total = d.unassigned || un.length;
    const scope = total > un.length ? '共 ' + total + ' 条，上面显示前 ' + un.length + ' 条' : '以上 ' + un.length + ' 条';
    box.append(el('div', { cls: 'my__note', text: scope + '报工没填「工人」。报工时填上工人名，产量才会记到具体的人头上。' }));
  }

  host.append(box);
}
