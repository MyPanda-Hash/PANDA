# 轻 MES（Light MES）

| 属性 | 内容 |
|---|---|
| 文档类型 | 项目说明 |
| 适用场景 | 项目入门 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-27 |
| 文档导航 | [文档中心](docs/README.md) |

轻 MES 是参考畅捷通 T+ 工作台形态实现的网页端制造执行系统。前端使用 Vue 3，后端使用 Spring Boot，面板配置和业务数据统一存储在 MySQL。

## 系统结构

```text
浏览器
  -> Vue 3 / Element Plus
  -> /api/*
  -> Spring Boot /api/px/* 与业务接口
  -> MySQL light_mes
```

项目只保留 SQL 后端模式，不包含 Mock、外部 PanelX 直连或 PanelX 代理模式。`PanelxList`、`PanelxForm` 和 `/panelx/...` 是内部通用面板命名。

## 文档入口

| 主题 | 文档 |
|---|---|
| 前端页面、控件和面板接入 | [前端面板设计](docs/frontend/前端面板设计.md) |
| SQL、接口、审批、权限和生单 | [后端逻辑设计](docs/backend/后端逻辑设计.md) |
| MySQL、Spring Boot 和 nginx 上线 | [服务器部署](docs/deploy/服务器部署.md) |
| 开发流程、测试和安全 | [开发与质量](docs/development/开发与质量.md) |

## 目录

```text
light-mes/
├── frontend/
│   └── src/
│       ├── core/       # 通用面板视图和请求基础能力
│       ├── business/   # SQL 接口适配、菜单和业务编排
│       ├── layout/     # 门户布局
│       ├── views/      # Dashboard、业务模块和系统页面
│       └── stores/     # 用户、页签和应用状态
├── backend/
│   └── src/main/
│       ├── java/com/mes/          # Controller、Service、Mapper、Entity
│       └── resources/db/init.sql  # 新环境数据库初始化基线
├── docs/               # 四份当前主题文档和原始证据
└── tools/              # 项目校验、JDK 探测、Maven 和维护工具
```

## 本地启动

前置条件：Node.js、JDK 17+、MySQL 运行在本机 3308。

1. 初始化数据库：

   ```powershell
   mysql -uroot -p < backend/src/main/resources/db/init.sql
   ```

2. 在当前终端注入本地凭据：

   ```powershell
   $env:SPRING_DATASOURCE_PASSWORD = '<数据库密码>'
   $env:MES_JWT_SECRET = '<至少 64 位随机密钥>'
   ```

3. 构建并启动后端：

   ```powershell
   backend\build.bat
   backend\run.bat
   ```

4. 启动前端开发服务：

   ```powershell
   Set-Location frontend
   npm install
   npm run dev
   ```

前端地址为 `http://localhost:5173`，后端地址为 `http://localhost:8080`。初始化数据提供开发账号 `admin / 123456`，首次部署后必须修改。

构建产物已经生成时，也可以从项目根目录运行 `start-project.bat`，统一启动后端和 `http://localhost:4173` 前端预览。

## 提交前校验

```powershell
node tools/verify-project.mjs
```

该命令依次检查当前文档、前端生产构建和后端 Maven 打包。文档整理或单独排查链接时使用：

```powershell
node tools/docs-audit.mjs
```

生产看板和返修工作台仍是专属视图，尚未接入完整 SQL 数据接口；页面会明确显示未接入状态。
