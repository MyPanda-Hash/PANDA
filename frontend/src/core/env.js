// ==================== 运行模式开关（通用层） ====================
// 由 Vite 环境变量决定数据源：mock / 本地后端 / PanelX 直连 / PanelX 后端代理。
// 业务层通过 `import { USE_* } from '@core/env'` 读取，禁止在本层 import 业务文件。
export const USE_PANELX_DIRECT = import.meta.env.VITE_PANELX === 'true'
export const USE_PANELX_PROXY = import.meta.env.VITE_PANELX_PROXY === 'true'
export const USE_PANELX = USE_PANELX_DIRECT || USE_PANELX_PROXY
export const USE_MOCK = import.meta.env.VITE_MOCK !== 'false' && !USE_PANELX
// 门户（登录/菜单/角标/通知）在 PanelX 模式下仍走 mock，避免依赖本地后端
export const USE_PORTAL_MOCK = USE_MOCK || USE_PANELX