# BUG 总结（开发踩坑记录）

> 规则：以后开发中遇到的任何 bug / 异常 / 环境坑，一律按下面的格式记到这里。
> 格式：日期 · 现象 → 根因 → 修复 → 教训。

## 2026-08-15

### 1. 登录无反应、token 写入报错（f→r 事故残留）
- **现象**：light-mes 登录点击后停在登录页，控制台无 token；页面 mounted 报 ReferenceError
- **根因**：两处叠加：
  a) `stores/user.js` 里 `JSON.stringiry`（应为 `stringify`）——早前批量替换事故把 f 全部换成 r，单词级修复漏了这个 API 名（编译能过、运行时才炸）
  b) `business/api.js` 里 `USE_PORTAL_MOCK is not defined`——分层重构时把 USE_* 常量改成 `export ... from '@core/env'` 转发，**re-export 不创建本地绑定**，文件内部引用全部悬空
- **修复**：`stringiry` → `stringify`（4 处）；api.js 顶部补 `import { USE_MOCK, USE_PANELX, USE_PORTAL_MOCK } from '@core/env'`
- **教训**：
  1. 批量字符替换事故的修复不能只靠「构建通过」——JS 里 API 名拼错不会报编译错，必须真实运行（登录/增删改查冒烟）验证
  2. ESM `export ... from` 只是转发，不是本地绑定；文件内部还要用就显式 `import`

### 2. 批量替换 import 把 12 个文件的字母 f 全变成 r（重大事故）
- **现象**：`from→rrom`、`v-for→v-ror`、`typeof→typeor`、`'\ufeff'→'\urerr'`、`RefPick→RerPick`…12 个文件 800+ 处
- **根因**：PowerShell 里单元素数组 `@( @("a","b") )` 会被展平成字符串数组，`$pair[0]` 取到的是字符串首字符，`Replace("f","r")` 级联执行
- **修复**：单词级映射表（90+ 词条+大小写变体）+ 逐轮 vite build 纠错 + 产物体积对比验证
- **教训**：
  1. 批量文件替换禁止「数组套数组」，用 here-string `旧|新` 行格式 + 索引步进
  2. 批量改动前先 git 提交（当时没 git，差点无法恢复——已 git init）

### 3. Windows 写工具 EISDIR / 编辑 EIO
- **现象**：write 工具在 E: 盘新建文件报 `EISDIR`（原子重命名到 .tmpdir 失败）；dev server 运行时 edit 工具报 `ReplaceFileW EIO (Win32 32)`
- **根因**：工具原子写机制的跨卷重命名问题；vite 文件句柄占用
- **修复**：新文件改用 `[System.IO.File]::WriteAllText`；被占用时先停 dev server 再改
- **教训**：E: 盘新文件一律 pwsh 写；改共享文件前先确认没有 dev server 在跑

### 4. DSH Vision Toolkit 安装三连坑
- **现象**：pip 拉包失败 → 修复后仍 `ensurepip ... exit status 101`
- **根因**：
  a) 直连 PyPI 不通 → 写 `%APPDATA%\pip\pip.ini` 阿里云镜像
  b) **微软商店版 Python 的 venv 启动器在插件隔离环境（HOME/LOCALAPPDATA 重定向）下无法创建进程**（这是 101 真凶）
  c) 免费 `glm-4v-flash` 限 `max_tokens ≤ 1024`，`vision_ground/detect` 报 1210 错
- **修复**：装 python.org 官方版 3.13.14（%LOCALAPPDATA%\Programs\Python313）并在插件设置指定 `runtime.python`；视觉模型换百炼 `qwen-vl-max`
- **教训**：Windows 上别用 Store 版 Python 做工具链；免费模型配额要提前查上限

### 5. 无头 Edge CDP 会话坑
- **现象**：a) 点击「新增」后 `Runtime.evaluate` 永远不返回（脚本挂死）；b) 新开 tab 登录脚本误判「还在登录页」；c) `/json/list` 复用残留 tab 状态
- **根因**：a) 页面点击触发上下文重建，evaluate 请求丢失 → 需给每个 CDP send 加超时竞速；b) 同一 profile 下 localStorage 共享，登录态跨 tab 持久 → 后续脚本无需再登录；c) 直接 URL 参数 `&voucherStateControl=New` 可跳过点击进表单视图
- **教训**：CDP 脚本三件套——send 超时竞速 + 点击 fire-and-forget + 轮询重试；能用 URL 参数就别模拟点击

### 6. Node 升级后终端仍是旧版本
- **现象**：注册表 PATH 已改，新终端 `node --version` 还是 v22
- **根因**：explorer 进程缓存旧环境变量，其子进程（终端）继承旧 PATH
- **修复**：重启 explorer（`Stop-Process -Name explorer`）或注销重登
- **教训**：改环境变量后必须重启父进程链（explorer/终端宿主）

### 7. 杂项
- npm 在 pnpm workspace 根目录 add 报 `ERR_PNPM_ADDING_TO_ROOT` → 加 `-w`
- ripgrep 对个别中文文件名报 `os error 234` → 换 PowerShell Select-String
- raw.githubusercontent.com / github.com 从 harness 直连不通 → 用 web_search 或 npmmirror 镜像
- PowerShell `$home` 是只读自动变量，脚本变量别用这名
### 8. CSS 里的 f→r 残留（stfetch / justiry-content / 1rr）
- **现象**：`.tools` 的 `align-items: stfetch`、`.head` 的 `justiry-content`、`.fields` 的 `grid-template-columns: repeat(3, 1rr)`——无效样式值被浏览器忽略，导致字段区布局错乱（本应 3 列）
- **根因**：与第 1 条同源的 f→r 事故残留；CSS 无效属性不报错，构建也查不出
- **修复**：本次按真实 T+ 重写 CSS 时一并纠正（stretch/justify-content/1fr）
- **教训**：f→r 事故后不仅要扫 JS，**CSS 里的 stretch/justify/flex/1fr 等词也要核对**（`stretch`/`justify`/`flex` 含 f）

### 9. 列表页明细表格「暂无数据」（detail 被列表查询剥离）
- **现象**：按 T+ 布局重写列表页后，产成品明细/材料明细表格一直显示「暂无数据」，但 mock 单据行明明带 detail
- **根因**：mock 列表查询 `queryFormDataList` 走 `flattenRows()`——把每张单展平成「一行=一条产成品」，且 `{ detail, ...head }` 解构**把 detail 丢弃了**；列表行根本没有 detail，`cur.detail?.products` 恒为 undefined
- **修复**：MANU_ORDER 列表查询改为返回单据级行（浅拷贝 detail 三数组），列表页按单据翻页展示其明细
- **教训**：列表查询返回的数据契约（展平行 vs 单据行）必须和页面展示方式一致；重构页面时先核对数据源契约，别只看「有数据」

### 10. 汇总页签分组行数值翻倍
- **现象**：产成品明细汇总/材料明细汇总的「分组行」数值是实际值的 2 倍（数量 200→400），而底部合计行正确
- **根因**：`summaryRows()` 分组时 `group.set(k, { ...r })` 复制首行（已含汇总字段原值），随后 `g[it.field] = (g[it.field] || 0) + num(r[it.field])` 又把首行原值加了一遍
- **修复**：复制首行前先把待汇总字段 `delete` 掉，再逐行累加
- **教训**：聚合逻辑里「先复制再累加」的写法，复制源与累加源相同字段时会翻倍；聚合前先清空待聚合字段