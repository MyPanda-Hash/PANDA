import { useState } from 'react'
import type { MessageUsage } from '../../services/api/types.js'
import styles from './TurnTiming.module.css'

/**
 * 本轮耗时（隐式显示）。
 *
 * 默认只显示一个总耗时小字，点开才看到明细 —— 诊断用，不该盖过回答本身。
 * 数据来自服务端 message_delta 的 usage.timing（自定义扩展）。
 */
export function TurnTiming({ usage }: { usage?: MessageUsage }) {
  const [open, setOpen] = useState(false)
  const timing = usage?.timing
  if (!timing || !timing.total_ms) return null

  const total = (timing.total_ms / 1000).toFixed(1)
  const model = (timing.model_ms / 1000).toFixed(1)
  const tool = (timing.tool_ms / 1000).toFixed(1)

  return (
    <div className={styles.wrap}>
      <button
        type="button"
        className={styles.toggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="查看本轮耗时明细"
      >
        {total}s
      </button>
      {open ? (
        <span className={styles.detail}>
          模型 {model}s · 检索 {tool}s · {timing.rounds} 轮
          {timing.searches ? ` · 检索 ${timing.searches} 次` : ''}
        </span>
      ) : null}
    </div>
  )
}