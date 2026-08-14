# core/ —— 通用引擎层（跨项目可复用）

本目录是面板引擎的通用部分，**禁止 import 任何业务文件**（`@/business/**`、`@/config/**` 等）。

## 内容

| 文件 | 职责 |
|---|---|
| `env.js` | 运行模式开关（唯一入口） |
| `request.js` | axios 基建 |
| `sdk.js` | PanelX SDK 封装 / 后端代理伪 SDK |
| `panel-engine.js` | 面板引擎内核（纯函数适配器 + platformCall） |
| `views/` | 引擎视图（列表/表单/参照/登录弹窗） |

## 规则

1. 不 import 业务；需要业务数据时由调用方注入（见 docs/架构分层.md 第四节）
2. 面板码映射、面板注册表、mock 数据一律留在 `business/engine.js`
3. 新增面板控件能力：先在此层扩展视图/内核，再由业务层配置启用
4. 改动后必须 `vite build` 验证