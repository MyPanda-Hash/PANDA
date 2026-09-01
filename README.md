# 轻 MES（Light MES）

| 属性 | 内容 |
|---|---|
| 文档类型 | 项目说明 |
| 适用场景 | 项目入门 |
| 维护状态 | 生效 |
| 最后整理 | 2026-09-01 |
| 文档导航 | [文档中心](docs/README.md) |

轻 MES 当前提供可运行的制造业务基线，并开始作为需求驱动 PLM 的技术底座演进。前端使用 Vue 3，后端使用 Spring Boot，面板配置和业务数据统一存储在 MySQL。既有 T+ 形态只作为交互参考，新功能以实际 PLM 需求、领域模型和验收条件为准。

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

通用列表、表单、明细、参照、选单和权限入口通过 `PanelRuntime` 复用；MES 库存、生单、质量规则与后续 PLM 修订、BOM、文档和变更规则分别保留在业务层。PLM 领域数据不会仅以现有 `form_data` JSON 改名实现，具体结构在需求确认后建立。

## 本地启动

前置条件：Node.js、JDK 17+、MySQL 运行在本机（默认端口 3308）。

端口约定（U 盘多机环境，换机器只设环境变量、不改文件）：后端 HTTP 默认 `8080`（`MES_HTTP_PORT` 覆盖）、MySQL 默认 `localhost:3308`（`MES_DB_HOST`/`MES_DB_PORT` 覆盖）、数据库密码走 `SPRING_DATASOURCE_PASSWORD`。

1. 初始化数据库：

   ```powershell
   mysql -uroot -p --default-character-set=utf8mb4 --execute="source backend/src/main/resources/db/init.sql"
   ```

   不要使用 `Get-Content init.sql | mysql`，Windows PowerShell 文本管道可能把中文替换为字面量 `?`。初始化后可运行 `node tools/repair-db-question-marks.cjs --check` 检查数据库编码。

2. 在当前终端注入本地凭据：

   ```powershell
   $env:SPRING_DATASOURCE_PASSWORD = '<数据库密码>'
   $env:MES_JWT_SECRET = '<至少 64 位随机密钥>'
   $env:ALIBABA_CLOUD_ACCESS_KEY_ID = '<OCR RAM 用户的新 AccessKeyId>'
   $env:ALIBABA_CLOUD_ACCESS_KEY_SECRET = '<OCR RAM 用户的新 AccessKeySecret>'
   # 可按容量调整，默认每用户每分钟 10 次、单实例最多并发 4 次
   $env:ALIBABA_CLOUD_OCR_REQUESTS_PER_MINUTE = '10'
   $env:ALIBABA_CLOUD_OCR_MAX_CONCURRENT = '4'
   ```

   OCR 凭据仅由后端读取。请使用只授权 OCR 调用的独立 RAM 用户，不要把凭据写入前端、配置文件或 Git；已在聊天、日志等位置明文出现的凭据必须先轮换。

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

前端地址为 `http://localhost:5173`，后端地址为 `http://localhost:8080`（默认值，`MES_HTTP_PORT` 覆盖时同步变化）。初始化数据提供开发账号 `admin / 123456`，首次部署后必须修改。

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
