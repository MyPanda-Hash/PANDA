# 工具目录

| 属性 | 内容 |
|---|---|
| 文档类型 | 工具说明 |
| 适用场景 | 测试、问题与工作日志 |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-26 |
| 文档导航 | [文档中心](../docs/README.md) |

项目工具按用途分为四类：

- `tplus-grab/`：抓取真实 T+ 页面结构、截图和交互证据。
- `panels/`：面板设计资料与配置草稿。
- `gen-*.cjs`、`gen-*.mjs`、`sync-*.mjs`：从配置或测试数据生成 SQL/面板文件。
- `patch-*.cjs`、`update-*.sql`、`run/`：已验证的增量修复和部署脚本。

常用验证命令：

```powershell
node tools/verify-project.mjs
node tools/docs-audit.mjs
```

## Milens 本地知识图谱

项目使用 Milens 在本机解析代码，并将符号、引用和依赖关系保存到仓库根目录的 `.milens/milens.db`。该目录已加入 `.gitignore`，不会上传源码或图谱数据到外部服务，也不会进入 GitHub。

首次构建或代码结构有较大变化时执行：

```powershell
milens analyze -p . -f
milens status -p .
```

常用查询与本地可视化：

```powershell
milens search PxService -p .
milens inspect PxService -p .
milens impact PxService -p .
milens serve -p .
```

`milens serve` 仅启动本地查询服务；停止服务后，索引仍保存在 `.milens/milens.db`。外部参考资料 `docs/ref/` 和内置 Maven `tools/apache-maven-3.9.9/` 不属于项目代码，已从图谱扫描范围排除。

临时探针、一次性回归脚本和重复扫描脚本不纳入版本库；需要新增诊断工具时，应使用有业务含义的名称，并在本文件补充用途。
