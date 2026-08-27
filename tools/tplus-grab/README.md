# tplus-grab —— 真实 T+ 演示环境单据页抓取 + 骨架复刻

| 属性 | 内容 |
|---|---|
| 文档类型 | 工具说明 |
| 适用场景 | 开发与质量 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-27 |
| 文档导航 | [文档中心](../../docs/README.md) |

把「登录 T+ 演示环境 → 抓真实 DOM（列定义/字段/样式）→ 生成骨架 HTML → 像素对比」打包成可复用管线。

## 登录目标（2026-08-26 复核：机械行业）

- 登录页固定走“立即体验” `#expBtn` → `selectRoles` iframe → “机械行业” `#machine`。
- 成功入口必须是 `h4t.chanjet.com`；禁止回退“轻MES” `#mes`，它会进入不同的 `h2t` 账套。
- 登录后直接从左侧主导航展开目标模块，不从“业务总览”判断模块是否存在。

人工登录顺序：打开 `https://t.chanjet.com/tplus/view/login.html`，点击 `#expBtn`，等待 `selectRoles` iframe 后在其中点击 `#machine`，最终确认地址进入 `https://h4t.chanjet.com/tplus/view/portal...`。成功会话应能从直接侧边栏展开“质量管理”，并看到“质检管理”和“质量追溯”。

不得在文档、截图和代码库中保存账号、密码、Cookie、Token 或账套身份信息。

## 前置条件

- Windows + Edge（Chrome 系）
- Node.js >= 21（用内置 fetch/WebSocket，无第三方依赖）
- 能访问 `t.chanjet.com` 和 `h4t.chanjet.com`

## 用法

```bash
# 首次：走「立即体验 → 机械行业」登录（会话存在 tools/tplus-grab/profile，之后免登录）
node tools/tplus-grab/grab.cjs --login --url "https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=QM&mId=QM05&pId=voucherView" --name qm05 --out docs/ref/tplus-live/qm05

# 之后换页面只换 URL；仍应确认地址属于本次机械行业 h4t 会话
node tools/tplus-grab/grab.cjs --url "https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=ST&mId=ST1021&pId=voucherView" --name st1021 --out docs/ref/tplus-live/st1021
```

输出（out 目录下）：`<name>.dom.json`（列定义/查询字段/页签/工具栏/样式快照）、`<name>.png`（真实页面截图）、`<name>.html`（骨架复刻）。

## 像素对比闭环（DSH 会话内）

```text
vision_html_screenshot(source=<out>/<name>.html, width=1600, height=1000)  # 渲染骨架
vision_pixel_diff(original=<out>/<name>.png, rebuilt=<渲染产物>)             # 差异热力图 + 报告
```

迭代：看 worstRegions → 改 HTML → 重新渲染对比，直到差异收敛。

## 说明

- 演示账号接口有 WAF 校验，必须真实浏览器点击流程登录（`--login` 已实现：`#expBtn` → `selectRoles` iframe 内 `#machine`）。
- 质量管理目录从登录后的直接侧边栏展开，不从“业务总览”页面判断模块是否存在。
- 会话 cookie 存 `./profile`，重启工具也保留；会话失效报 401 时重新带 `--login`。
- 常用参数：`--width`/`--height` 视口；`--port` CDP 端口（默认 9222）；`--no-gen` 只抓不生成。
- 新面板必须先归档列表、新增页、工具栏下拉、页签、参照和选单弹窗证据，再同步 SQL 配置、菜单、后端规则和角色权限。
