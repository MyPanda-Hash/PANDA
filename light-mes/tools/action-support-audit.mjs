/**
 * 按钮动作支持表一致性校验。
 *
 * 前端 core/action-support.js 的 BACKEND_ACTIONS 必须与后端 PxService.callButton 的
 * switch 分支一一对应：前端显示一个动作，后端就得有实现；否则点了会抛
 *「未定义按钮规则」。任何一个方向漂移都算失败。
 *
 * 用法：node tools/action-support-audit.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { BACKEND_ACTIONS, EXTRA_ACTIONS } from '../frontend/src/core/action-support.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const pxService = path.join(root, 'backend', 'src', 'main', 'java', 'com', 'mes', 'service', 'PxService.java')
const panelDir = path.join(root, 'backend', 'src', 'main', 'java', 'com', 'mes', 'panel')

function javaSwitchActions() {
  const source = fs.readFileSync(pxService, 'utf8')
  const start = source.indexOf('switch (buttonName)')
  if (start < 0) throw new Error('PxService.callButton 里找不到 switch (buttonName)')
  const end = source.indexOf('default:', start)
  if (end < 0) throw new Error('switch 里找不到 default 分支')
  const block = source.slice(start, end)
  return new Set([...block.matchAll(/case\s+"([^"]+)":/g)].map((m) => m[1]))
}

function handlerActions() {
  const found = new Set()
  if (!fs.existsSync(panelDir)) return found
  for (const name of fs.readdirSync(panelDir)) {
    if (!name.endsWith('.java')) continue
    const source = fs.readFileSync(path.join(panelDir, name), 'utf8')
    for (const match of source.matchAll(/"([^"]+)"\.equals\(action\)/g)) found.add(match[1])
  }
  return found
}

const problems = []

const backend = javaSwitchActions()
const handlers = handlerActions()

const missingInFrontend = [...backend].filter((action) => !BACKEND_ACTIONS.has(action))
const missingInJava = [...BACKEND_ACTIONS].filter((action) => !backend.has(action))
if (missingInFrontend.length) problems.push(`后端有 switch 分支但前端支持表没登记：${missingInFrontend.join('、')}`)
if (missingInJava.length) problems.push(`前端支持表登记了后端没有实现的动作：${missingInJava.join('、')}`)

const missingHandlers = [...handlers].filter((action) => !EXTRA_ACTIONS.has(action) && !backend.has(action))
if (missingHandlers.length) problems.push(`PanelActionHandler 注册的动作没进 EXTRA_ACTIONS：${missingHandlers.join('、')}`)

if (problems.length) {
  console.error('按钮动作支持表校验失败：')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exitCode = 1
} else {
  console.log(`Action support audit passed: ${backend.size} backend actions, ${EXTRA_ACTIONS.size} handler actions`)
}