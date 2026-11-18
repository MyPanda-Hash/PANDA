// 简报看板（#/brief）：管理者的第一屏。
//
// 与「待办」的区别是取舍，不是排版：
//   · 待办按「有多少件事」组织（执行视角，42 条）；
//   · 简报按「有几件要你定」组织（管理视角，最多 3 条 + 三个数字）。
// 数字位拿不到样本时显示「暂无样本」而不是 0%，避免被读成「全都不按期」。

import { BRIEFING, NOTIFICATIONS } from './api.js';
import { el, clear, toast } from './util.js';
import { openReport } from './report-view.js';

/** 把接口错误翻译成一句人话（与其它视图保持同一口径）。 */
function describe(err) {
  if (!err) return '操作失败';
  if (err.status === 401) return '登录已失效，请重新登录';
  if (err.status === 0) return '连不上服务，检查后端是否在运行';
  return err.message || '操作失败';
}

const ASK_CHIPS = ['这周哪条线最不稳', '谁手上的单积压最多', '今天能交几单', '这个月毛利怎么样'];

let host = null;
let data = null;
let loading = false;
let error = null;

export async function mount(root, ctx) {
  host = root;
  clear(host);
  ctx = ctx || {};
  const role = (ctx.state && ctx.state.role) || 'manager';
  await load(role, ctx);
  // 打开简报就等于看过：把「今日简报」这条站内通知标已读，角标随之消失
  markBriefRead(ctx);
}

async function markBriefRead(ctx) {
  try {
    const payload = await NOTIFICATIONS.list(true, 20);
    const items = (payload && payload.data && payload.data.items) || [];
    const ids = items.filter((i) => i && i.refType === 'briefing').map((i) => i.id);
    if (!ids.length) return;
    await NOTIFICATIONS.markRead(ids);
    if (ctx && typeof ctx.refreshCounts === 'function') ctx.refreshCounts();
  } catch (_) {
    /* 标已读失败不影响看简报 */
  }
}

export async function unmount() {
  host = null;
  data = null;
  error = null;
}

async function load(role, ctx) {
  // 允许只传 ctx：有调用点写成 load(ctx)，把主机壳当 role 传进来，
  // 于是内部去读 ctx.state 时 ctx 已是 undefined（2026-11-17 点「重新生成」白屏）。
  if (role && typeof role === 'object') {
    ctx = role;
    role = (ctx && ctx.state && ctx.state.role) || 'manager';
  }
  if (!role) role = 'manager';
  loading = true;
  error = null;
  render(ctx);
  try {
    const payload = await BRIEFING.board({ role });
    data = (payload && payload.data) || payload || null;
  } catch (err) {
    error = err;
  } finally {
    loading = false;
    render(ctx);
  }
}

function render(ctx) {
  if (!host) return;
  clear(host);
  const box = el('div', { cls: 'brief' });

  if (loading && !data) {
    box.append(el('div', { cls: 'skeleton', attrs: { style: 'height:120px;border-radius:8px' } }));
    host.append(box);
    return;
  }

  if (error && !data) {
    box.append(
      el('div', { cls: 'empty' }, [
        el('h3', { text: '简报暂时打不开' }),
        el('p', { text: error.message || '加载失败' }),
        el('button', {
          cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' },
          on: { click: () => load((ctx.state && ctx.state.role) || 'manager', ctx) },
        }),
      ])
    );
    host.append(box);
    return;
  }

  const d = data || {};
  const brand = (ctx.state && ctx.state.brand) || {};
  const who = (ctx.user && (ctx.user.realName || ctx.user.real_name || ctx.user.username)) || '';
  const roleLabel = { manager: '管理者', supervisor: '车间主任', staff: '一线员工' }[d.role] || '管理者';

  // ① 页头
  box.append(
    el('div', { cls: 'brief__head' }, [
      el('div', { cls: 'brief__hello', text: '早上好' + (who ? '，' + who : '') }),
      el('div', { cls: 'brief__meta', text: (d.day || '') + ' · 一号工厂 · ' + roleLabel + '视图' }),
    ])
  );

  // ② 结论
  box.append(el('div', { cls: 'brief__headline', text: d.headline || '今天没有需要汇报的异常。' }));

  // ③ 四个数字
  const grid = el('div', { cls: 'brief__stats' });
  for (const s of d.stats || []) {
    grid.append(
      el('div', { cls: 'brief__stat' }, [
        el('div', { cls: 'brief__stat-n', attrs: { 'data-tone': s.tone || '' }, text: String(s.value) }),
        el('div', { cls: 'brief__stat-l', text: s.label }),
        s.hint ? el('div', { cls: 'brief__stat-h', text: s.hint }) : null,
      ].filter(Boolean))
    );
  }
  box.append(grid);

  // ④ 待拍板
  const decisions = d.decisions || [];
  const sectHead = el('div', { cls: 'brief__sect-h' }, [
    el('span', { text: '需要你决定' }),
    el('span', { text: decisions.length ? '其余 ' + (d.hidden || 0) + ' 条已交给执行层' : '' }),
  ]);
  box.append(sectHead);

  if (!decisions.length) {
    box.append(
      el('div', { cls: 'brief__item' }, [
        el('div', { cls: 'brief__item-body' }, [
          el('div', { cls: 'brief__item-t', text: '现在没有需要你拍板的事' }),
          el('div', { cls: 'brief__item-d', text: '异常都由执行层在处理；有新的决策项会出现在这里。' }),
        ]),
      ])
    );
  }

  for (const item of decisions) {
    const acts = el('div', { cls: 'brief__item-acts' });
    // 「问一句」把问题带进对话视图，省得用户自己复述单据号
    acts.append(
      el('button', {
        cls: 'btn btn--sm', text: '问一句', attrs: { type: 'button' },
        on: {
          click: () => {
            const q = '为什么「' + (item.title || '') + '」还没处理完？';
            try { sessionStorage.setItem('dnx-ask-seed', q); } catch (_) {}
            ctx.navigate && ctx.navigate('#/chat');
          },
        },
      })
    );
    // 决策项的实际处理在 MES 里完成；工作台只负责把人送到待办清单
    acts.append(
      el('button', {
        cls: 'btn btn--sm btn--primary', text: item.actionLabel || '去处理', attrs: { type: 'button' },
        on: { click: () => ctx.navigate && ctx.navigate('#/todos') },
      })
    );
    box.append(
      el('div', { cls: 'brief__item' }, [
        el('div', { cls: 'brief__item-body' }, [
          el('div', { cls: 'brief__item-t', text: item.title || '' }),
          item.reason ? el('div', { cls: 'brief__item-r', text: item.reason }) : null,
          item.advice ? el('div', { cls: 'brief__item-d', text: item.advice }) : null,
        ].filter(Boolean)),
        acts,
      ])
    );
  }

  // ⑤ 本期报表：日报/周报/月报
  const reports = d.reports || []
  if (reports.length) {
    box.append(el('div', { cls: 'brief__sect-h' }, [
      el('span', { text: '本期报表' }),
      el('span', { text: '与简报同一套口径，点「查看」直接读正文' }),
    ]))
    const row = el('div', { cls: 'brief__reports' })
    for (const r of reports) {
      const btn = el('button', {
        cls: 'btn btn--sm' + (r.generated ? '' : ' btn--primary'),
        attrs: { type: 'button' },
        text: r.generated ? '重新生成' : '生成',
      })
      btn.addEventListener('click', async () => {
        btn.disabled = true
        btn.textContent = '生成中…'
        try {
          const res = await BRIEFING.generateReport(r.kind)
          const payload = (res && res.data) || res || {}
          toast(r.label + '已生成：' + (payload.title || ''))
          await load((ctx.state && ctx.state.role) || 'manager', ctx)
        } catch (err) {
          toast(describe(err), 'error')
          btn.disabled = false
          btn.textContent = r.generated ? '重新生成' : '生成'
        }
      })
      const viewBtn = r.generated && r.sessionId
        ? el('button', { cls: 'btn btn--sm', attrs: { type: 'button' }, text: '查看' })
        : null
      if (viewBtn) {
        viewBtn.addEventListener('click', () => {
          openReport({ label: r.label, title: r.title, sessionId: r.sessionId, kind: r.kind })
        })
      }
      row.append(el('div', { cls: 'brief__report' }, [
        el('div', { cls: 'brief__report-main' }, [
          el('div', { cls: 'brief__report-t' }, [
            el('span', { text: r.label }),
            el('span', {
              cls: 'tag',
              attrs: { 'data-tone': r.generated ? 'accent' : 'muted' },
              text: r.generated ? '已生成' : '未生成',
            }),
          ]),
          el('div', { cls: 'brief__report-s', text: r.title }),
        ]),
        el('div', { cls: 'brief__report-acts' }, [viewBtn, btn]),
      ]))
    }
    box.append(row)
  }

  // ⑥ 追问入口
  const input = el('input', {
    cls: 'brief__ask-input',
    attrs: { type: 'text', placeholder: '比如：为什么 SO-2026-08-0004 会拖 84 天？' },
  });
  const ask = () => {
    const q = (input.value || '').trim();
    try { sessionStorage.setItem('dnx-ask-seed', q); } catch (_) {}
    ctx.navigate && ctx.navigate('#/chat');
  };
  const chips = el('div', { cls: 'brief__chips' });
  for (const c of ASK_CHIPS) {
    chips.append(el('button', {
      cls: 'brief__chip', text: c, attrs: { type: 'button' },
      on: {
        click: () => {
          try { sessionStorage.setItem('dnx-ask-seed', c); } catch (_) {}
          ctx.navigate && ctx.navigate('#/chat');
        },
      },
    }));
  }
  box.append(
    el('div', { cls: 'brief__ask' }, [
      el('div', { cls: 'brief__ask-label', text: '想追问什么？直接问，能下钻到单据和工序' }),
      el('div', { cls: 'brief__ask-row' }, [
        input,
        el('button', { cls: 'btn btn--primary', text: '提问', attrs: { type: 'button' }, on: { click: ask } }),
      ]),
      chips,
    ])
  );

  host.append(box);
}
