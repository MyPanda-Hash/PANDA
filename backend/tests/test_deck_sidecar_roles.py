"""端到端验证 `write_deck_sidecar.mjs` 推出来的页面意图。

sidecar 的 `role` 决定模板专属版式能不能命中（role 一律退成 content 时，专用版面永远
闲置），而它只在 skill 运行时产出，Python 单测覆盖不到。所以这里用真 node 跑真脚本、
读真主题元数据，断言的是「用户点重排时真正会发生什么」，不是一处内部函数的返回值。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1] / "app" / "builtin_skills" / "dashi-ppt"
SIDECAR_SCRIPT = SKILL_ROOT / "scripts" / "write_deck_sidecar.mjs"
PROJECT_ROOT = SKILL_ROOT / "project"

# 真实版式（theme01 / theme02 的固定版面号，来自 generated-metadata）。
COVER = "theme01_page005"          # slot=cover
COVER_ALT = "theme01_page003"      # slot=cover-bento
STAT_GRID = "theme01_page007"      # 关键数字一览，stats/metric
TIMELINE = "theme01_page028"       # 里程碑时间轴，events/step + trend
CONTENTS = "theme01_page009"       # 报告导览 · 目录，chapters/chapter
BIG_NUMBER = "theme01_page006"     # 大数字 · 资本体量，secondaries/misc
CLOSING = "theme02_page074"        # 封底结语 · Closing，slot=closing

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not SIDECAR_SCRIPT.is_file(),
    reason="需要 node 与 dashi-ppt skill 才能跑 sidecar 脚本",
)


def _node(script: str) -> Any:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or "node failed")
    return json.loads(completed.stdout)


def _default_props() -> dict[str, dict]:
    """各版式的 defaultProps —— 默认值本身就是合法 props，不必手搓。"""
    layouts = [
        COVER,
        COVER_ALT,
        STAT_GRID,
        TIMELINE,
        CONTENTS,
        BIG_NUMBER,
        CLOSING,
    ]
    return _node(
        f"""
import {{ getLayoutRecord }} from './scripts/workflow/theme-registry.mjs';
const out = {{}};
for (const layout of {json.dumps(layouts)}) {{
  out[layout] = getLayoutRecord(layout)?.defaultProps ?? null;
}}
process.stdout.write(JSON.stringify(out));
"""
    )


@pytest.fixture(scope="module")
def props() -> dict[str, dict]:
    return _default_props()


def _sidecar(tmp_path: Path, layouts: list[str], props: dict[str, dict]) -> dict:
    """按给定版式顺序生成一份 deck，跑真脚本，读回 sidecar。"""
    slides = [{"layout": layout, "props": props[layout]} for layout in layouts]
    goal_path = tmp_path / "goal.json"
    goal_path.write_text(
        json.dumps(
            {"title": "季度经营汇报", "audience": "管理层", "slides": slides},
            ensure_ascii=False,
        ),
        "utf-8",
    )
    deck_path = tmp_path / "output" / "季度经营汇报.pptx"
    deck_path.parent.mkdir(parents=True, exist_ok=True)
    deck_path.write_bytes(b"")  # 脚本不读 deck 内容，只借它的文件名
    completed = subprocess.run(
        ["node", str(SIDECAR_SCRIPT), str(goal_path), str(deck_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or "sidecar failed")
    sidecar_path = deck_path.with_name("季度经营汇报.deck.json")
    assert sidecar_path.is_file()
    return json.loads(sidecar_path.read_text("utf-8"))


def _roles(sidecar: dict) -> list[str]:
    return [str(page["role"]) for page in sidecar["pages"]]


def test_first_page_is_the_cover_even_when_the_layout_is_not_cover_like(
    tmp_path: Path, props: dict[str, dict]
) -> None:
    """版式启发式猜错封面，曾让整份重排被「必须有封面」拒掉。"""
    sidecar = _sidecar(tmp_path, [STAT_GRID, CONTENTS], props)
    assert _roles(sidecar)[0] == "cover"


def test_specialised_layouts_keep_their_page_intent(
    tmp_path: Path, props: dict[str, dict]
) -> None:
    """专用版式要命中专用 role，否则模板的专属版面永远闲置。"""
    sidecar = _sidecar(
        tmp_path, [COVER, STAT_GRID, TIMELINE, CONTENTS, BIG_NUMBER], props
    )
    assert _roles(sidecar) == ["cover", "metric", "timeline", "section", "content"]


def test_closing_layout_is_the_closing_page(tmp_path: Path, props: dict[str, dict]) -> None:
    sidecar = _sidecar(tmp_path, [COVER, CONTENTS, CLOSING], props)
    assert _roles(sidecar)[-1] == "closing"


def test_last_content_page_is_not_forced_into_the_closing_role(
    tmp_path: Path, props: dict[str, dict]
) -> None:
    """旧实现把末页无条件判成结束页，正文页会被套上结束版式。"""
    sidecar = _sidecar(tmp_path, [COVER, BIG_NUMBER], props)
    assert _roles(sidecar)[-1] == "content"


def test_sidecar_uses_the_shared_presentation_roles(
    tmp_path: Path, props: dict[str, dict]
) -> None:
    """普通演示稿 sidecar 与公共角色定义保持一致。"""
    from app.services import deck_roles

    sidecar = _sidecar(tmp_path, [COVER, STAT_GRID, TIMELINE, CONTENTS, CLOSING], props)
    for page in sidecar["pages"]:
        normalized = str(page["role"]).strip().lower()
        assert (
            normalized in deck_roles.ALLOWED_ROLES
            or normalized == deck_roles.DEFAULT_ROLE
        )
