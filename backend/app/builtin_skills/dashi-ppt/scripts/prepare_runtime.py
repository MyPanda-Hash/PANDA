#!/usr/bin/env python3
"""在本轮工作区准备完整技能副本，复用镜像内的固定依赖。"""

from pathlib import Path
import os
import shutil
import tempfile


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    dependencies = Path("/opt/dashi-ppt-runtime/node_modules")
    if not (dependencies / "tsx/package.json").is_file():
        raise SystemExit("Dashi 镜像依赖缺失，请管理员更新 sandbox 镜像；不要在线安装。")
    workspace = Path(os.environ.get("PI_WORKSPACE_ROOT", "/workspace")).resolve()
    if not workspace.is_dir():
        raise SystemExit("当前会话工作区不存在。")
    # 沿用平台的内部目录，避免主题资源被当作本轮生成成果上传。
    internal = workspace / ".pi"
    if internal.is_symlink():
        raise SystemExit("工作区内部目录不能是符号链接。")
    internal.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="dashi-ppt-", dir=internal))
    shutil.copytree(source, target, dirs_exist_ok=True)
    # 依赖包保持只读，生成器写入的 node_modules/.cache 留在工作区。
    modules = target / "project/node_modules"
    modules.mkdir()
    for dependency in dependencies.iterdir():
        if dependency.name != ".cache":
            (modules / dependency.name).symlink_to(
                dependency, target_is_directory=dependency.is_dir()
            )
    print(target)


if __name__ == "__main__":
    main()
