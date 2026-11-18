# 工作区脚本（镜像）

这里是 `_light-m-ai` 工作区根目录那几个启动脚本的副本，方便随仓库一起版本化。

## 为什么是副本

真正的运行位置在**工作区根目录**：

```
/Users/panda/Projects/_light-m-ai/
├── AGENTS.md            工作区事实（端口、日志、踩过的坑）
├── README-MAC.md        迁移背景
├── start-local.sh       一键拉起全部服务
├── start-all.sh         旧版（检查系统 java/node，本机用不了）
├── serve-ai-web.mjs     AI 前端静态服务 + WebSocket 反代
```

这些脚本**按工作区布局写死了相对路径**（`cd doc-nexus-ai/backend`、`cd light-mes`）。
移到别处就不work了，所以这里只做版本留档，不改变运行位置。

## 改了脚本怎么办

改工作区根目录那份，再把这份覆盖过来：

```bash
cd /Users/panda/Projects/_light-m-ai
cp AGENTS.md README-MAC.md start-local.sh start-all.sh \
   serve-ai-web.mjs doc-nexus-ai/workspace/
```

## 凭据

脚本本身**不含任何密钥**，`start-local.sh` 是从工作区根的 `.env.local` 读的：

```bash
[ -f "$L/.env.local" ] && . "$L/.env.local"
```

`.env.local` 已被 `.gitignore` 挡住（`.env.*` 规则），不会入库。