---
name: minio-upload
description: Upload generated workspace files through the Doc Nexus Harness and return durable same-origin download URLs without exposing object-storage credentials.
---

<!-- doc-nexus-managed-writing-skill -->

# Managed file upload

Use this Skill when a generated file must be returned to the user immediately.
The helper delegates storage to the Harness. Never request, read, or embed MinIO
credentials, endpoints, buckets, or presigned URLs.

Run from the task workspace:

```bash
python3 /skills/minio-upload/minio_upload.py report.docx
```

Upload every supported file below a directory:

```bash
python3 /skills/minio-upload/minio_upload.py output --batch
```

命令返回 JSON，`file.url`（批量时为 `files` 中各项）是真实上传回执，
不是需要原样展示给用户的答复。路径必须位于当前 Harness 工作区内；
存储生命周期与对象命名由平台管理。

在 Doc Nexus 聊天中，平台会收集实际生成文件并显示下载/查看卡片。
上传成功后保留工作区文件供平台收集；最终答复只写必要的内容说明，没有额外说明时只回复“已完成。”。
不要重复输出下载链接、上传过程、Skill 路径、JSON 回执或自动转 DOCX 的内部说明，也不要解释平台将显示卡片或下载入口。
不要自行拼接 URL，也不要把模板字符串或 URL 编码表达式当成下载地址。
上传失败时如实说明失败和影响，不得声称文件已交付。

若用户报告找不到文件或无法访问/下载已交付的文件：重新执行上传并核对 JSON 回执；
确认上传成功后，在最终答复中直接给出 `file.url` 链接作为恢复手段
（此场景不受"不要重复输出下载链接"的约束），不要只回复"已上传"。
