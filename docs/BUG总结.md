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

## 2026-08-19

### 11. 员工导入 500：Duplicate entry 'EMP-EMP-010'（编号 COUNT 法撞号）
- **现象**：员工 Excel 导入报 500，`SQLIntegrityConstraintViolationException: Duplicate entry 'EMP-EMP-010' for key 'uk_panel_formno'`，整批回滚；昨天导入成功后今天再导必失败
- **根因**：`archNo()` 用 `COUNT(form_no LIKE 'EMP-%') + 1` 生成编号。库里 EMP-001~008 + EMP-010 = 9 行 → 新编号算出 EMP-010 → 与已有编号重复 → 唯一索引拒绝
- **修复**：`archNo/invNo` 改为「取已有最大序号 + 1 并查重循环」（与 `generateFormNo` 同款安全算法）
- **教训**：编号生成禁止用行数 COUNT（删除/断号/并发都会撞号），一律按最大序号递增 + 存在性查重

### 12. EMP 面板「业务员」字段类型配成「是否」
- **现象**：导入员工后业务员值全部变成 false（姓名丢失）
- **根因**：`panel_config` 里 业务员 dataType=是否（布尔），导入按布尔解析，非「是/true/1」一律 false
- **修复**：dataType 改为 文本
- **教训**：配置字段类型要与语义一致；导入/表单/预览都按 dataType 渲染，配错会静默损坏数据

### 13. 单单据面板 gridTabs 列配错 → 明细预览全空 → 保存覆盖明细（数据丢失）
- **现象**：员工改为单单据面板后，列表 A 区/表单明细显示为空；用户在空明细上点保存，整张单据明细被覆盖成 1 行（原 10 人丢失）
- **根因**：`gridTabs[0].columns` 误配成单据头字段（备注/单据状态），而 A 区预览/表单明细展示的是明细行（员工字段）→ 列错位全空
- **修复**：columns 改为明细行字段（员工编码…停用）；明细数据从种子（engine.js seed）+ 残留行恢复
- **教训**：a) gridTabs[0].columns 必须 = 明细行字段；b) 表单保存是整张明细覆盖写回，渲染异常时禁止保存；c) 重要数据改动前先备份/导出

### 14. PowerShell 中文经管道/HTTP 编码污染（诊断与测试数据坑）
- **现象**：pwsh 内联中文拼 JSON/SQL/请求体，经 harness→pwsh→mysql/fetch 多跳转后出现多重编码乱码（键名损坏），且控制台显示本身也会乱码，极易误判数据损坏
- **规避**：涉及中文数据的自动化写操作与诊断一律用 Node 脚本（文件 UTF-8、fetch 原生 UTF-8、中文用 \uXXXX 转义）+ 十六进制校验键名；mysql 命令行加 `--default-character-set=utf8mb4`
- **教训**：PowerShell 中文内联不可靠；先 hex 校验字节再判断数据是否损坏，不要被控制台乱码误导

### 15. 参照弹窗永不弹出：RefPickDialog prop 名与 v-model 契约不匹配（2026-08-19）
- **现象**：表单/新增弹窗点参照字段（工序编码等）参照弹窗不弹出；点击事件、refVisible、props 链路全通（DOM 反查：RefPickDialog/ElDialog modelValue=true、ElDialog exposed visible=true），但 el-dialog overlay display:none
- **根因**：RefPickDialog 声明 prop `visible`，但调用方（PanelxForm/NewVoucherDialog）用 `v-model="refVisible"`——Vue 3 无参数 v-model 契约是 `modelValue` prop + `update:modelValue` 事件；`visible` 永远收不到值，且模板显式 `:model-value="visible"` 覆盖了 attrs 继承 → el-dialog model-value 恒为默认 false → 弹窗永不显示
- **修复**：RefPickDialog 改为标准契约：prop `modelValue` + `:model-value="modelValue"` + `emit('update:modelValue')`（defineEmits 保留 update:visible 兼容）；调用方零改动
- **教训**：a) 自定义组件的 v-model 必须用 modelValue/update:modelValue 契约（或显式 v-model:xxx 并两边一致）；b) 弹窗类"点击无反应"先反查 DOM→Vue 实例链（`el.__vueParentComponent` 逐级读 props/exposed）确认状态真值，别只看 DOM 可见性（headless 下 transition 时序会误报）；c) 历史 E2E 通过不代表代码对——当时验证路径与当前入口不同

## 2026-08-20

### 16. 查询区填条件后列表异常 + 选单生成的单据缺单据日期/单据编号
- **现象**：进货单（PU_IN）列表页查询区填写 单据日期/单据编号 后，列表"所有单据"都显示/保留（像全被改成填的值）；且选单生成的进货单表头 单据日期/单据编号 为空白
- **根因**（两条叠加）：
  a) 后端 `queryFormDataList` 条件过滤 `if (rv == null) continue`——**缺键行跳过过滤**（新建/选单生成缺 单据日期/单据编号 键的行在填查询条件后全部保留，看起来"所有单据都变成我填写的"）
  b) 普通选单生成路径（SelectVoucherDialog 无 generateButton 分支）formData 只含 headerMap 映射字段，且 `save` 新建不补默认值——非审批面板"建单据时填写 单据日期/单据编号"规则未覆盖选单生成
- **修复**：a) 过滤缺键 → `hit=false`（不匹配，不再漏过）；b) `save` 新建分支对**非审批面板**按字段存在性补 单据日期=当天、单据编号（或锭号）=form_no（缺才填）；mock 同步；c) 存量回填（PU_IN 2002-2006 按创建日期补 单据日期、form_no 补 单据编号）
- **验证**：填条件后 total=1（只匹配真正符合的行）✅；模拟选单生成（formData 仅映射字段）保存后自动补 日期/编号 ✅
- **教训**：a) 条件过滤对**缺失字段**必须判不匹配，不能 continue 跳过（否则缺字段的数据绕过滤）；b) 新规则（填写时机）必须覆盖**所有创建路径**（手动新增/选单生成/推式生单），并做存量回填；c) 用户报告的"所有单据都变了"先查**数据是否真被污染**（DB 直查）→ 再查过滤/展示层，两者往往叠加

### 17. 产成品入库单选生产加工单生成空表头单据（"没有数据"）
- **现象**：FINISH_IN 选单（选生产加工单）生成的新单表头全空（data={}），看起来"没有生单成功、没有数据"；历史还产生过明细也为空的废单（FI-2001~2003，data={} + detail={products:[]}）
- **根因**：`SelectVoucherDialog.generate()` 普通选单分支 `head[m.to] = r[m.from]`——FINISH_IN selectConfig headerMap `from:'单据编号'`，而 **MO 行的编号字段是「锭号」**（行内无「单据编号」键）→ 取到 undefined → JS `{a: undefined}` 被 `JSON.stringify` **丢弃该键** → 新单 data 为空对象 → 表头全空；弹窗列渲染早有 cellText 兼容（单据编号→锭号回退）但生单映射没有
- **修复**：generate() 新增 `selVal(row, f)`（与 cellText 同规则：单据编号回退 锭号/编号，空值不写入）；清理历史废单（FI-2001~2003 + 测试单）
- **验证**：UI 全流程（弹窗勾选→生成→跳转表单）→ 新单 data={加工单号:MO-…, 匹配来源单号:MO-…} + detail.products 2 行完整 ✅
- **教训**：a) 选单/生单的**字段映射取值必须与列渲染用同一套兼容规则**（单据编号↔锭号），一处兼容一处不兼容必然产生半空数据；b) 排查"生成无数据"先查**新单的 data 与 detail_data 原文**（区分表头空 vs 明细空），再回溯映射链路；c) 弹窗勾选的行对象与列表行同源，可直接复用渲染层的取值规则

### 18. 选单生成的单据明细表格不显示（detail 键与 tabs key 不匹配）
- **现象**：产成品入库单选生产加工单生成的新单，**列表/弹窗的明细表格"暂无数据"**；DB 里 detail_data={"products":[...]} 有数据
- **根因**：`SelectVoucherDialog.generate()` 用 `cfg.detailKey`（FINISH_IN='products'、MATERIAL_OUT='materials'）作为**写入目标单据的 detail 键**——但目标面板 `detail.tabs[0].key` 是 **'items'** → 表格读 `detail['items']` 为空。detailKey 的语义是「从来源单据取明细的键」（MO 的 products），**不能直接作为目标写入键**
- **修复**：generate() 生成前查目标面板配置，**写入键 = 目标面板 `detail.tabs[0].key`**（来源键只用于取源明细）；清理键不匹配的存量测试单
- **验证**：选单生成 → 弹窗/列表明细表格正常显示 2 行（铝型材-散热片/轴套 C）✅
- **教训**：a) **来源明细键与目标明细键必须分开**（detailKey=来源键，目标键取目标面板 tabs key），混用必现"有数据不显示"；b) "表格没数据"先查 **detail_data 键 vs detail.tabs key** 是否一致；c) 生成/保存的 detail 键契约：`detail.tabs[].key` 是唯一权威

### 19. 关闭全部页签后误报「面板编号无效」
- **现象**：叉掉所有面板页签只剩「我的桌面」时，弹出"面板编号无效，请从菜单重新进入"
- **根因**：`PanelxList` 的 `watch([panelCode, operationName])` 在关闭页签跳转 `/dashboard` 时 panelCode 变为 undefined → watch 触发 `search()` → `load()` → `invalidPanel`（panelCode 空）→ 误报；`PanelxForm` 的 `watch([panelCode, code])` 同源（切走时会 load 无效参数）
- **修复**：两处 watch 开头加守卫——`panelCode` 为空/'undefined' 时直接 return（组件即将失活/卸载，不触发加载）
- **验证**：打开面板 → 关闭全部 → 跳 `/dashboard`、无错误提示 ✅
- **教训**：**路由参数驱动的 watch 必须防御"参数失效"场景**（路由切走/页签关闭时 params 变 undefined），否则触发无意义加载并误报；keep-alive 组件在失活后仍可能被 watch 触发
