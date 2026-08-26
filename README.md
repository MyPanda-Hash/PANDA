# 轻MES（Light MES）· 生产制造执行系统

| 属性 | 内容 |
|---|---|
| 文档类型 | 项目说明 |
| 适用场景 | 项目入门 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](docs/README.md) |

参考畅捷通 T+ 门户形态的网页端轻量 MES。技术栈：Vue3 + Element Plus（前端）、Spring Boot 3 + MySQL（后端）。

> 开发规范、场景设计、部署文档和工作日志统一从 [文档中心](docs/README.md) 进入。

## 数据源两模式

表单引擎（`src/business/engine.js`）支持两种数据源，通过环境变量切换（2026-08-20 起仅两模式，Mock 与 PanelX 直连已移除）：

| 模式 | 环境变量 | 数据来源 | 适用场景 |
|---|---|---|---|
| **本地后端（默认）** | `VITE_PANELX_PROXY` 空 | `/api` 代理 → 本地 Spring Boot（`/px/*` 面板引擎 + MySQL） | 本地全栈开发 / 生产自建（需 MySQL + 后端） |
| **PanelX 后端代理** | `VITE_PANELX_PROXY=true` | `/api/panelx/*` → Spring Boot 网关服务端直连平台 | 平台凭据由后端持有，浏览器不加载 SDK |

- 门户（登录/菜单/角标/通知）在 PanelX 代理模式下仍走前端内置演示数据（`USE_PORTAL_MOCK`），仅表单引擎走平台/后端
- 生产看板 / 返修工作台原为 mock 派生数据，Mock 模式移除后恒空（页面显示提示文案）
- 参照字段：表头/明细参照均弹窗拉取基础档案面板数据（后端 `PxService.buildMeta` 已透传参照信息，2026-08-20）

### PanelX 后端代理模式（VITE_PANELX_PROXY=true）

浏览器不再加载 SDK，表单引擎改走本地 Spring Boot 网关：`/api/panelx/*` → 服务端直连平台（`PanelxGateway` 自动登录、缓存 JWT、HTTP 401 自动重登重试一次）。平台凭据配置在 `backend/src/main/resources/application.yml` 的 `mes.panelx` 段（`panel-map` 管理本地面板码 → 平台面板码）。

实测平台接口协议（2026-08-14 抓包确认，业务域 SdkTest）：

| 引擎调用（SDK） | 平台 HTTP 接口 | 说明 |
|---|---|---|
| `user.login` | `POST wp-core/api/user/login` | `{userName,password}` → `data.token`（JWT，有效期 30 天） |
| `api.getPanelConfig` | `GET wp-core/api/cdp/getPanelConfig?busDomainCode=&panelCode=` | `data` 为 JSON 字符串，后端已解析为对象 |
| `api.queryFormDataList` | `POST wp-core/api/queryFormDataList` | `{panelCode,pageNo,pageSize,keyword,condition}` → `data.{totalSize,list,privilege}` |
| `api.getPermMatrix` | `GET wp-core/api/permMatrix?panelCode=` | 行级按钮/字段权限 |
| `api.getNewFormPermMatrix` | `GET wp-core/api/newFormPermMatrix?panelCode=&operationName=` | 新增表单权限 |
| `api.getFormDescriptor` | `GET wp-core/api/formDescriptor?panelCode=&code=` | code=行「编号」；`data.{data,meta}` |
| `api.callButton` | `POST wp-core/api/callButton2` | `{panelCode,buttonName,formData,buttonParam}` |
| `api.deleteForms` | `POST wp-core/api/callButton2` | 即「删除」按钮：`buttonParam.rowCodes` |

公共请求头：`Authorization: Bearer <JWT>`、`BusDomainCode`、`AppCode`；平台成功封装 `{state:"200",msg,data}`，HTTP 错误体 `{errorDescription,errorCode,error}`。

运行方式（Windows PowerShell）：

```bash
# 后端：build.bat 打包后 run.bat 启动（网关随后端提供 /api/panelx/*）
# 前端：切到代理模式
cd frontend
$env:VITE_PANELX_PROXY='true'; npm run dev
```

注意：`/api/panelx/**` 已在 SecurityConfig 放行（平台凭据由后端持有）；生产环境应改为要求本系统登录并收紧网关账号。

## 目录结构

```
light-mes/
├── frontend/            # 前端（Vite + Vue3 + Element Plus + Pinia + Vue Router）
│   ├── src/core/        # 通用引擎层（跨项目可复用）：面板引擎内核 / SDK 封装 / 引擎视图（详见 docs/架构分层.md）
│   ├── src/business/    # 业务层：engine.js 面板配置注册表 / api.js 接口封装 / menus.js 菜单树
│   ├── src/layout/      # 门户壳：TopBar / SideMenu / TabsBar
│   ├── src/views/       # 业务视图：login / dashboard / modules
│   └── src/stores/      # user（业务）/ tabs、app（通用）
├── backend/             # 后端（Spring Boot 3 + MyBatis-Plus + JWT）
│   ├── src/main/resources/db/init.sql   # MySQL 初始化脚本（建库建表+种子数据）
│   ├── build.bat        # 一键构建（自动探测 JDK 17+ + 便携 Maven）
│   └── run.bat          # 一键启动
├── docs/                # 设计文档（docs/design/）+ 调研取证（docs/ref/）+ 架构分层规范
└── tools/               # 便携 Maven + settings.xml（阿里云镜像）+ detect-jdk.bat（JDK 自动探测）
```

## 快速开始

### 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173（默认本地后端模式，需先启动后端）
```

登录页演示账号：`admin / 123456`（本地后端 / 门户演示数据）。

### 后端

1. 初始化 MySQL：
   ```bash
   mysql -uroot -p < backend/src/main/resources/db/init.sql
   ```
2. 按需修改 `backend/src/main/resources/application.yml` 中数据库账号密码
3. 构建并启动：
   ```bash
   backend/build.bat    # 打包
   backend/run.bat      # 启动 http://localhost:8080
   ```

### 前后端联调

默认即本地后端模式（`frontend/.env.local` 中 `VITE_PANELX_PROXY` 为空），vite 已配置 `/api` 代理到 `http://localhost:8080`。

### 提交前校验

```bash
node tools/verify-project.mjs
```

该命令统一执行文档检查、SDK 单元测试、前端生产构建和后端 Maven 打包，并自动选择本机可用的 JDK 17+。

## 已实现功能（门户壳 v0.2 · T+ 保真形态）

- 顶部导航（T+ 三段式）：Logo+分隔线+工厂（账套）切换（带刷新）｜中区：账号/企业认证状态/登录日期/服务到期｜右侧：内嵌全局搜索（"搜索-产品功能"下拉直达）、更新公告（new 角标+详情弹窗）、移动端入口、待办/消息/预警角标、全屏、帮助下拉（帮助文档/AI 智能帮助/新手引导/关于）、用户下拉（账号管理/修改密码/界面设置/换肤/工作台设置/初始化向导/退出）
- 左侧菜单：分组菜单（平铺/手风琴两种模式）、折叠、菜单搜索、顶部快捷区（展开菜单/单据查询/新增单据）
- 多页签（T+ 形态）：打开/关闭/右键菜单/keep-alive；右侧按钮组：快速查找单据（按单号直达加工单）、最大化/恢复（隐藏侧栏）、关闭全部、更多下拉；激活页签紫色下划线（T+ #663ECF）
- T+ 设计 token 化：主蓝 #289be5 / 顶栏 #f0f0f0 / 侧栏 #fafafa / 内容区 #f9f9f9 / 角标红 #fc5c5e，Element Plus 主色同步对齐
- 浮层：右侧帮助面板（动态/消息/知识库/帮助教程 四 tab）、MES 初始化向导（三步：选行业细分&经营业态 → 选启用模块&报工方式 → 完成匹配专属桌面菜单，首次登录自动弹出）
- 登录页 + 路由守卫 + JWT（后端）
- 我的桌面：KPI 卡片、生产进度、待办时间线、快捷入口（可配置显隐）
- 后端：登录（BCrypt + JWT）、菜单树、工厂列表、角标接口；MySQL 种子数据

## 模块开发规划（占位页 → 真实功能）

生产管理（工单/报工/工艺/BOM/排班）→ 库存（出入库/领退料）→ 质量（来料/过程/完工/不良）→ 设备（台账/点检/OEE）→ 报表看板 → 基础资料 → 系统管理。

每个模块页位于 `frontend/src/views/modules/`，按 `ModuleView.vue` 的 code 路由自动挂载，后续按模块逐个替换为真实页面；后端按模块加 controller/service/mapper 与建表 SQL。
