# core/ —— 通用引擎层（跨项目可复用）

| 属性 | 内容 |
|---|---|
| 文档类型 | 包说明 |
| 适用场景 | 通用架构与 SDK |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../docs/README.md) |

本目录是面板引擎的通用部分，**禁止 import 任何业务文件**（`@/business/**`、`@/config/**` 等）。

## 内容

| 文件 | 职责 |
|---|---|
| `env.js` | 运行模式开关（唯一入口；2026-08-20 起仅两模式：本地后端 / PanelX 后端代理） |
| `request.js` | axios 基建 |
| `sdk/` | 可独立复用的 PanelX 网关 SDK（HTTP 客户端注入、API 映射、错误模型、单元测试） |
| `sdk.js` | MES 项目适配入口：注入 request 和模式开关，兼容 `initSdk` / `requireAuthed` / `sdkLogin` |
| `panel-engine.js` | 面板引擎内核（纯函数适配器 + platformCall） |
| `views/` | 引擎视图（列表/表单/参照/登录弹窗） |

## 规则

1. 不 import 业务；需要业务数据时由调用方注入（见 docs/架构分层.md 第四节）
2. 面板码映射、面板注册表一律留在 `business/engine.js`
3. 新增面板控件能力：先在此层扩展视图/内核，再由业务层配置启用
4. 改动 SDK 后必须 `npm run test:sdk`，改动 core 后必须 `npm run build` 验证
