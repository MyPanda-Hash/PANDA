---
name: dashi-ppt
description: 使用完整 Dashi 主题与布局生成演示文稿，支持 theme01-theme12（含 theme02 炫光紫绿风），交付可编辑 PPTX、PDF 或 HTML。优先用于有明确主题风格的 PPT 制作。
---

# Dashi PPT · Doc Nexus 内置版

固定上游版本 0.2.7；出处与平台适配记录见 `UPSTREAM.json`。外层按 AGPL-3.0 授权，随包 PPTX 导出引擎按 MIT 授权；保留各自 LICENSE。不要在线升级、安装 npm 包或下载浏览器。

## 运行边界

`/skills/dashi-ppt` 是摘要批准后的只读目录，包含完整 `project/`、`scripts/`、`references/` 和 `assets/`。每轮先执行一次：

```bash
python /skills/dashi-ppt/scripts/prepare_runtime.py
```

该命令返回本轮工作区内的完整技能副本目录。下文 `<skill-root>` 始终指这个返回目录，而非 `/skills/dashi-ppt`。在后续工具调用中复用返回路径；不要重复准备、全盘搜索或修改批准目录。镜像已预装 Node.js、Chromium 和生成依赖；本技能直接调用 Node/tsx，不使用会被工具策略拦截的包管理器命令。准备失败时明确报告，不在线安装或无界重试。

最终 PPTX/PDF/ZIP 与同名 `<deck>.deck.json` 写入 `/workspace/output/<本次任务名称>/`。goal JSON、HTML、主题资源和验收图片写在 `<skill-root>/output/`，该工作区副本位于平台排除成果收集的 `.pi/` 内，避免中间图片被当成文件成果上传。平台自动收集最终成果并生成下载卡片，不自己拼下载 URL，不向用户发送容器的 `127.0.0.1` 或 `/skills/` 地址。

## 内容与交付

- 遵守用户指定页数、主题、内容与语言；一页 PPT 必须只有一张幻灯片。用户已选 `theme02` 时直接沿用，不重复询问。
- 用户没有指定风格且没有整体委托时，先用文字列出候选供选择；不发送 sandbox 本地图片链接。
- 本平台“生成 PPT”默认交付 `.pptx`；明确要求 HTML 时将 HTML 和资源打包 ZIP，要求 PDF 时导出 PDF。
- 无图片要求时使用无媒体布局，不搜索、生成图片或留下占位图。提供图片时使用 `media:stage`，不把外部 URL 写进 goal。
- 每页只承载一个信息角色。用真实业务内容替换全部可见默认文案，不虚构数字、日期或来源。
- 页码、章节号、计数和单位也属于可见文案，必须逐项填写，不能沿用主题演示稿的编号。单页的页码应为 `01 / 01`（若布局提供 `index`，显式设置）；内容组成数量不等于章节数，单位按本次业务含义填写。
- 保留主题布局、颜色、字体与结构，默认只填文案和可见数组；不要改生成器源码或自由手写 HTML 绕过主题。

主题：theme01 轻拟态、theme02 炫光紫绿、theme03 深浅代码、theme04 玻璃糖果、theme05 色谱图表、theme06 深色图谱、theme07 冷白调研、theme08 黑金实验、theme09 深蓝杂志、theme10 金色指数、theme11 高能增长、theme12 声波霓虹。

## 工作流

1. 提炼标题、目标、受众、页数和必须覆盖的事实，确认主题。为当前任务新建输出目录，不复用旧 goal 或旧 HTML。
2. 查询合适布局：

```bash
node <skill-root>/project/scripts/layout-query.mjs --theme theme02 --role breakdown --limit 8
```

`role` 按实际需求选择，允许的角色见 `references/layout-roles.md`。查询不支持的角色时阅读该文件，不盲目试错。

3. 在同一次调用中批量检查少量候选，读取精简字段契约：

```bash
node <skill-root>/project/scripts/inspect-layout.mjs --compact <layout-id> <另一个候选-id>
```

依据 `copyKeys`、`copyBudgets`、`propShapes`、`fillPlan` 填写字段。对象和数组必须符合实际形状；不能凭字段名猜结构。`contentLocked` 的布局不能改正文，应选择另一布局。普通生成不要读取数 MB 的 `layout-manifest.json` 或整个主题源码。

4. 写 goal JSON。以下为结构说明；`layout` 和 `props` 必须来自上一步真实候选，不能原样交付示例文案：

```json
{
  "title": "本次汇报标题",
  "goal": "本次汇报要解决的问题",
  "audience": "汇报对象",
  "owner": "汇报方",
  "pageCount": 1,
  "themePack": "theme02",
  "slides": [
    {"layout": "实际候选布局ID", "props": {"实际文案字段": "真实业务内容"}}
  ]
}
```

最终 `slides` 必须是具体布局列表，不能留空靠默认页面代替。图表、指标的说明与数字必须一致。长文稿可以先使用 `node <skill-root>/project/scripts/goal-scaffold.mjs --title <标题> --goal <目标> --theme <主题> --pages <页数> --out <skill-root>/output/goal.json` 生成布局与 fill-plan，再批量填实际内容。

5. 一次执行安全字段处理、goal 校验、渲染、结构与文案校验：

```bash
bash <skill-root>/scripts/render_goal_deck.sh <skill-root>/output/goal.json <skill-root>/output/ppt/index.html
```

此平台入口只使用镜像依赖，不联网下载、不启动常驻预览服务。字段安全处理若替换了布局，核对输出的 `layoutChanges` 是否仍满足需求。

6. 直接导出最终文件：

```bash
node <skill-root>/project/scripts/export-pptx.mjs <skill-root>/output/ppt /workspace/output/<任务>/汇报.pptx
```

PDF 使用同一个脚本，提供 `.pdf` 输出路径并增加 `--pdf`。CLI 自动启动并关闭所需内部服务，不需要 curl 导出接口或手动启动预览服务器。

7. 从已完成安全字段处理的 goal 生成标准内容 sidecar；第二个参数必须与上一步最终文件路径相同，脚本会在同目录写入同名 `<deck>.deck.json`：

```bash
node <skill-root>/scripts/write_deck_sidecar.mjs <skill-root>/output/goal.json /workspace/output/<任务>/汇报.pptx
```

sidecar 只保留每页实际可见文字，`note` 固定为空；不要把 goal JSON 或 HTML 复制到交付目录。每页的 `role` 由版式语义推断（封面/结束页/分隔页/流程/时间线/指标/对比），它决定聊天里「按模板重新排版」能命中哪些专属版面——不要手工改这份文件，也不要把页面文案改得与成品不一致。

8. 用 `python-pptx` 回读最终 PPTX 的页数与文字，再用 `python /skills/pptx/scripts/pptx_tool.py render <文件> --output-dir <skill-root>/output/preview` 渲染并查看真实文件。核对页码与实际总页数、全部可见文案、标题、字体、裁切和重叠；残留演示编号或文案时修正 props 后重新导出，并重新生成 sidecar。不要将 HTML 渲染成功或只列出文字当作 PPTX 验收成功。

## 失败与回复

只修复明确错误：渲染/校验失败、残留模板文案、内容遗漏、明显溢出或文件导出失败。不得宣称不存在的文件已生成，不能用空白页、截图伪装可编辑 PPTX。连续失败时报告具体阻塞，不反复找文件或安装依赖消耗预算。

最终命令成功、文件存在且验收通过后，简短回复已完成；有业务补充时再说明，文件入口由平台提供。
