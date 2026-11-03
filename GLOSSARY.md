# GLOSSARY — 轻 MES 领域术语表

> 本文件是术语表(glossary),不是实现规范。记录领域概念与已敲定的边界决策。
> 由 grill-with-docs 拷问会话维护,术语成型一条记一条。

## 术语

### 扫描填单辅助(Scan-to-Fill)

OCR 模块的业务定位:用户在面板表单里拍照/上传单据图片 → 云 OCR 识别 →
`OcrFormMapper` 把识别结果映射到当前面板字段 → 人工确认后提交。

- 证据:端点 `POST /api/ocr/scan-form`(入参 `panelCode`)、前端
  `ScanFillDialog.vue`、`OcrFormMapper`。
- 边界:不做通用文档识别 API,不对外;识别结果必须经过人工确认才落库。
- 网关抽象 `OcrGateway`(云端边界,服务层 own 校验与 schema 映射)仅服务于此定位。

### 已审批判据(Approved Status Criteria)

`core/doc-status.js` 定义共享判据:单据状态 ∈ {已审核, 生产中, 已完工, 已关闭},
或历史数据"审批状态"∈ {已审批, 已通过} → 视为已审批(`isDocApproved()`/
`APPROVED_DOC_STATUSES`)。PanelxList/PanelxForm 共用同一把尺子,禁止各处手写状态集合。

### 表格调整 / 列偏好(Column Preferences)

用户级表格列配置:顺序(seq)/显示(visible)/栏名(alias),存 `px_column_pref`
(panel_code + owner + col_name),经 `ColumnPrefsDialog.vue` 调整,请求时
`applyColumnPrefs` 注入(过滤+排序+追加新列);只影响本人显示,不改面板定义。

### 界面语言切换与词条词典(i18n)

顶栏下拉 + Alt+L 循环;vue-i18n 热切换。词条三层:英文静态包
(`i18n/locales/en.js` 的 `biz.*`,中文原文即键,永远优先) → `/api/locale/dict`
字典(内存 TTL → `sys_translation` 表) → 阿里云机翻(结果落库成缓存)。
`tt()` 仅在显示层翻译,数据键(buttonName/dataName/状态值)永不翻译。

### 权限动作(11 项)

`business/permissions.js`:view / query / add / edit / delete / export / print /
audit / price / review(预留)/ adjust(预留);角色-面板授权矩阵按动作粒度授予,
按钮经映射表校验;admin 全权。

### 工作台设置(Desk Settings)

`DeskSettingsDialog.vue` 工作台卡片配置:快捷入口、KPI/进度/待办内容卡片的显隐
与布局,存于 `stores/app.js` 工作台设置档。

### 选单生单与占用(来源反查语义)

无独立占用表:生单目标明细行**必带**「来源面板/来源单号/来源行号」,占用判定按
这些来源信息**全库反查**已执行数量累加(`reservedQuantity`,逐 FormData 遍历),
来源明细只能被一个下游占用、不得超过可执行数量;来源单据须已审核。

## 已敲定决策

| 日期 | 决策 | 来源 |
|---|---|---|
| 2026-08-29 | OCR 定位为"扫描填单辅助",非通用 OCR 服务 | grill 会话第 1 问 |
| 2026-08-29 | 扫描审计走结构化日志(userName/panelCode/requestId/耗时),不入库;合规需求出现再升级 | grill 会话第 2 问 |
| 2026-08-29 | 凭据未配置:启动时检测 AK 为空→后端返回明确错误/不注册端点,前端 ScanFillDialog 禁用并提示"未配置",不做 fail-fast 阻断启动 | grill 会话第 3 问 |

## 代码已实现(拷问中核实,无需决策)

- **映射策略**:`OcrFormMapper` 数据驱动——从面板配置(dataSchema/detail.tabs)自动取
  标签,字段可加 `ocrAliases` 别名;新面板零代码接入。
- **限流**:按用户滑动窗口(默认 10 次/分)+ 全局并发信号量(默认 4),配置见
  `mes.ocr.requests-per-minute / max-concurrent`。
- **准入**:仅"单据"类面板且非只读可用;图片深度验证(格式/≤10MB/尺寸 15~8192/长宽比<50)。
- **凭据**:阿里云 AK 走环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET`。