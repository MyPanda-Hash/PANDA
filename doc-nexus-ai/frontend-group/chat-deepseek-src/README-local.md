# chat-deepseek-src —— DeepSeek 官方 chat-demo 源码（含本系统改动）

来源：[`deepseek-ai/chat-demo`](https://github.com/deepseek-ai/chat-demo)（MIT，见 `LICENSE`）。
构建产物在上一级目录 `frontend-group/chat-deepseek/`，由 `serve-ai-desk.mjs` 以 `/chat-deepseek/` 提供。

## 我们改了什么

详见 `../docs/../../CONTRACT.md` §8.4 / §8.5（路径：`doc-nexus-ai/frontend-desk/CONTRACT.md`）。摘要：

| 文件 | 改动 |
| --- | --- |
| `src/components/sider/WorkbenchNav.tsx` | 新增：侧边栏顶部「简报/待办/群聊/员工画像/模型配置」，仿 DSH 侧边栏 |
| `src/components/sider/WorkbenchHeader.tsx` | 新增：标题显示「时间 · 账号名」（读 MES 的 `mes_user`） |
| `src/components/sider/Sider.tsx` | 接入上面两个组件 |
| `src/components/sider/Sider.module.css` + `.d.ts` | `.wbNav*` / 页头样式 |
| `src/i18n/zh.ts`、`en.ts` | `wbNavLabel/wbBrief/wbTodos/wbGroups/wbStaff/wbModels` |
| `build-local.ts` | 新增：把 `apiOrigin` 注入成 `/ai-api`（同源） |

## 重新构建

```bash
cd doc-nexus-ai/frontend-group/chat-deepseek-src
export PATH="$HOME/.local/node/bin:$PATH"      # 必须 Node 22；Node 24 下 rspack 原生模块加载失败
npx pnpm@9 install --registry=https://registry.npmmirror.com
NODE_ENV=production node node_modules/@rspack/cli/bin/rspack.js build -c build-local.ts
```

产物在 `dist/`，复制到 `../chat-deepseek/`（覆盖 `static/` 与 `index.html`）。

## 部署后必须做的一步

构建会覆盖 `index.html`，**登录态注入脚本要重新加**（否则界面会要求填 API Key）：

```html
<script>
  (function () {
    try {
      var token = localStorage.getItem("mes_token");
      if (token && token !== "undefined") localStorage.setItem("chat-demo:api-key", token);
    } catch (e) {}
  })();
</script>
```

插在 `</head>` 之前。key 的真实来源是后端「模型配置」，浏览器只带 MES 登录 token。
