# 轻MES（Light MES）· 生产制造执行系统

参考畅捷通 T+ 门户形态的网页端轻量 MES。技术栈：Vue3 + Element Plus（前端）、Spring Boot 3 + MySQL（后端）。

## 数据源三模式

表单引擎（`src/panelx/engine.js`）支持三种数据源，通过环境变量切换：

| 模式 | 环境变量 | 数据来源 | 适用场景 |
|---|---|---|---|
| **Mock（默认）** | 无（或 `VITE_MOCK=true`） | 前端内置演示数据 | 纯前端演示、无后端依赖 |
| **本地后端** | `VITE_MOCK=false` | `/api` 代理 → 本地 Spring Boot（`/px/*` 面板引擎） | 本地全栈开发（需 MySQL + 后端） |
| **PanelX 平台** | `VITE_PANELX=true` | 浏览器 PanelX SDK 直连 `https://demo.kwaidoo.com/VF_DEV/`（业务域 `SdkTest`） | 对接 PanelX 低代码平台 |
| **PanelX 后端代理** | `VITE_PANELX_PROXY=true` | `/api/panelx/*` → Spring Boot 网关服务端直连平台 | 平台凭据由后端持有，浏览器不加载 SDK |

**PanelX 平台模式说明：**
- 页面自动加载 `/panelx/preload.js` 拉取 SDK（localhost 走 `devSdkUrl`，生产自动 ping 探测 baseURL）
- 进入面板页时若未登录 PanelX 平台，自动弹出登录框（`PanelxLogin.vue`，演示账号 `admin / 123456`）
- 本地面板码 `MANU_ORDER` 映射到平台面板 `SdkTest_IML_00002`（`engine.js` 的 `PANEL_MAP`），平台面板配置变更前端自动跟随
- 门户（登录/菜单/角标/通知）在平台模式下仍走 mock，仅表单引擎走平台

运行方式：

```bash
# PanelX 平台模式（Windows PowerShell）
cd frontend
$env:VITE_PANELX='true'; npm run dev
```

**对接实测（2026-08-14 已验证）：**
- 链路：preload.js 从 `demo.kwaidoo.com/VF_DEV/wp-core/api/getPanelXSdk` 拉取并执行 SDK → 进入面板页若平台未登录自动弹出「登录 PanelX 平台」→ `admin / 123456` 登录 → 平台配置/数据实时渲染（面板 `MANU_ORDER` 映射平台 `SdkTest_IML_00002`，实测返回「测试流程面板」+ 18 条数据）
- 面板码规则：`engine.js` 的 `PANEL_MAP` 做本地码→平台码映射（当前仅 MANU_ORDER），其余码原样透传（平台侧需存在对应面板）
- 平台会话：SDK 登录态存 localStorage（`SdkTest_SdkTest_jwt_token`）；token 失效时平台 API 返回非 200，前端已统一归一为「未登录」并自动弹登录框（`platformCall` 包装）
- 注意事项：平台模式仅表单引擎走平台，门户壳（登录/菜单/角标）仍走 mock；本地 mock 的报表/看板/返修工作台为前端派生数据，平台模式下不适用；平台面板的字段/工具栏以平台配置为准（适配器 `adaptPanelConfig`/`adaptMeta`）

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

### 前端（可独立运行，默认 mock 数据）

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

登录页演示账号：`admin / 123456`（mock 模式任意账号密码均可）。

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

将前端切到真实接口模式：新建 `frontend/.env.local` 写入：

```
VITE_MOCK=false
```

（vite 已配置 `/api` 代理到 `http://localhost:8080`）

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
