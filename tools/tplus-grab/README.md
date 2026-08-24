# tplus-grab —— 真实 T+ 演示环境单据页抓取 + 骨架复刻

| 属性 | 内容 |
|---|---|
| 文档类型 | 工具说明 |
| 适用场景 | 测试、问题与工作日志 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../docs/README.md) |

把「登录 T+ 演示环境 → 抓真实 DOM（列定义/字段/样式）→ 生成骨架 HTML → 像素对比」打包成可复用管线。

## 登录目标（2026-08-19 起：机械行业）

- 登录页「立即体验 → 选择角色」默认点击 **机械行业**（找不到时回退 轻MES，向后兼容）
- 实现位置：`grab.cjs --login` 的 loginFlow（expBtn → selectRoles iframe 内文本匹配「机械行业」）

## 前置条件

- Windows + Edge（Chrome 系）
- Node.js >= 21（用内置 fetch/WebSocket，无第三方依赖）
- 能访问 t.chanjet.com / h2t.chanjet.com

## 用法

```bash
# 首次：走「立即体验→轻MES」演示账号登录（会话存在 tools/tplus-grab/profile，之后免登录）
node tools/tplus-grab/grab.cjs --login --url "https://h2t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=mp&mId=mp05&pId=voucherView" --name mp05 --out docs/ref/tplus-live/mp05

# 之后换单据只换 URL 即可（如销售出库单 ST1021 / 采购入库单 ST1001 / 产成品入库单 ST1002）
node tools/tplus-grab/grab.cjs --url "https://h2t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=ST&mId=ST1021&pId=voucherView" --name st1021 --out docs/ref/tplus-live/st1021
```

输出（out 目录下）：`<name>.dom.json`（列定义/查询字段/页签/工具栏/样式快照）、`<name>.png`（真实页面截图）、`<name>.html`（骨架复刻）。

## 像素对比闭环（DSH 会话内）

```text
vision_html_screenshot(source=<out>/<name>.html, width=1600, height=1000)  # 渲染骨架
vision_pixel_diff(original=<out>/<name>.png, rebuilt=<渲染产物>)             # 差异热力图 + 报告
```

迭代：看 worstRegions → 改 HTML → 重新渲染对比，直到差异收敛。

## 说明

- 演示账号接口有 WAF 校验，必须真实浏览器点击流程登录（--login 已实现：expBtn → selectRoles iframe 内 #mes）
- 会话 cookie 存 `./profile`，重启工具也保留；会话失效报 401 时重新带 --login
- 常用参数：--width/--height 视口；--port CDP 端口（默认 9222）；--no-gen 只抓不生成
