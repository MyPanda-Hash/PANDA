// ==================== 运行模式开关（通用层） ====================
// 由 Vite 环境变量决定数据源：本地后端 / PanelX 后端代理（2026-08-20 起仅两模式，
// Mock 模式与 PanelX 直连模式已移除）。
// 业务层通过 `import { USE_* } from '@core/env'` 读取，禁止在本层 import 业务文件。
export const USE_PANELX = import.meta.env.VITE_PANELX_PROXY === 'true'
// 门户（登录/菜单/角标/通知）在 PanelX 模式下仍走本地 mock 数据，避免依赖本地后端
export const USE_PORTAL_MOCK = USE_PANELX
