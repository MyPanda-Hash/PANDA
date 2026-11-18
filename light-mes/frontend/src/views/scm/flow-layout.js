/**
 * flow-layout.js — 业务总览流程图的排版计算
 *
 * <p>模块配置里只写「谁排在谁后面」（pos 的纵向先后 + 行内左右），真正的坐标由这里算：
 * 同一水平带归成一行，行内等距铺开并整体居中；宽度取最宽行的宽度，保证各行居中对齐、
 * 单卡行落在双卡行中间。抽成纯函数是为了能直接测几何（不重叠、行数、画布高度）。
 *
 * 与页面样式绑定：卡片 176×56（.fn-card）、横向间距 72、纵向间距 56，改样式必须同步改这里。
 */

export const CARD_W = 176
export const CARD_H = 56
export const GAP_X = 72
export const GAP_Y = 56
/** 行吸附网格：配置里的 Y 常是 40/120/520 这类手摆值，按 60 取整才能把同层归到一行。 */
export const ROW_SNAP = 60

/**
 * 把一个模块的节点排成行列。
 *
 * <p>两种排版模式，由 module.layout 决定：
 * - 默认（行模式）：pos 写像素，按 Y 吸附分行、行内等距居中。适合「一条主链 + 少量分支」，
 *   单卡行会自动落在双卡行正中间。
 * - `columns`（列模式）：pos 写 [列序号, 行序号]，按网格摆放，空位留白。适合几条独立链并列，
 *   因为行模式会把「只有 2 张卡」的行重新居中，列就对不齐了。
 *
 * @param {{nodes?: Array<{code: string}>, pos?: Record<string, number[]>,
 *          layout?: string, gutter?: number}} module 模块配置
 * @returns {Record<string, {x: number, y: number}>} code → 左上角坐标
 */
export function layoutNodes(module) {
  const positions = (module && module.pos) || {}
  const nodes = (module && module.nodes) || []
  if (!nodes.length) return {}

  if (module && module.layout === 'columns') {
    // 左侧留白给「旁路连线」绕行（网格从 x=0 起就没地方绕了）
    const gutter = Number(module.gutter) || 0
    const out = {}
    for (const n of nodes) {
      const p = positions[n.code] || [0, 0]
      out[n.code] = {
        x: Math.round(gutter + p[0] * (CARD_W + GAP_X)),
        y: Math.round(p[1] * (CARD_H + GAP_Y)),
      }
    }
    return out
  }

  const raw = nodes.map((n) => {
    const p = positions[n.code] || [0, 0]
    return { code: n.code, x: p[0], y: p[1] }
  })

  // 1) 按 Y 吸附到网格后分行：同层节点归入同一行
  const sorted = [...raw].sort((a, b) => a.y - b.y || a.x - b.x)
  const rows = []
  for (const item of sorted) {
    const band = Math.round(item.y / ROW_SNAP)
    const row = rows[rows.length - 1]
    if (row && row.band === band) row.items.push(item)
    else rows.push({ band, items: [item] })
  }

  // 2) 行内按 X 排序、等距铺开并居中：单卡行正好落在双卡行的中间
  const widest = Math.max(...rows.map((r) => r.items.length))
  const rowWidth = widest * CARD_W + (widest - 1) * GAP_X
  const out = {}
  rows.forEach((row, ri) => {
    row.items.sort((a, b) => a.x - b.x)
    const count = row.items.length
    const used = count * CARD_W + (count - 1) * GAP_X
    const offset = (rowWidth - used) / 2
    row.items.forEach((item, ci) => {
      out[item.code] = {
        x: Math.round(offset + ci * (CARD_W + GAP_X)),
        y: Math.round(ri * (CARD_H + GAP_Y)),
      }
    })
  })
  return out
}

/** 连线方向对应的锚点：竖直支链下出上进，横向按目标在左还是在右选边。 */
export const HANDLES = Object.freeze({
  down: { sourceHandle: 'out-b', targetHandle: 'in-t', sourcePosition: 'bottom', targetPosition: 'top' },
  up: { sourceHandle: 'out-t', targetHandle: 'in-b', sourcePosition: 'top', targetPosition: 'bottom' },
  right: { sourceHandle: 'out', targetHandle: 'in', sourcePosition: 'right', targetPosition: 'left' },
  left: { sourceHandle: 'out-l', targetHandle: 'in-r', sourcePosition: 'left', targetPosition: 'right' },
})

/**
 * 默认锚点：跨行一律竖直（下出上进 / 上出下进），同一行按横坐标方向选边。
 *
 * <p>「同一行一律右出左进」是错的：目标在左边时，右出左进会让折线先往右伸、再绕过整张卡片
 * 回到左侧，画面上留下一条伸出去的残线（销售图的「费用单 → 分摊单」就是这样）。
 * 目标在左必须「左出右进」。
 */
export function defaultHandles(a, b) {
  const from = a || { x: 0, y: 0 }
  const to = b || { x: 0, y: 0 }
  const dy = to.y - from.y
  if (dy > 0) return HANDLES.down
  if (dy < 0) return HANDLES.up
  return to.x < from.x ? HANDLES.left : HANDLES.right
}

/**
 * 画布高度：按排完版的最后一行算，上下各留 40px。
 * 不能用原始 pos 的最大 Y——手摆坐标往往比实际行距大几百像素，会留出一大片空白。
 *
 * @param {Record<string, {y: number}>} layout layoutNodes 的结果
 * @param {number} min 最小高度，窄图不会塌成一条
 */
export function canvasHeightOf(layout, min = 420) {
  let maxBottom = 0
  for (const code of Object.keys(layout || {})) {
    maxBottom = Math.max(maxBottom, layout[code].y + CARD_H)
  }
  return Math.max(min, maxBottom + 80)
}
