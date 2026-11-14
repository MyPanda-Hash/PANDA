"""Static admission analysis for third-party standard Skills.

Analysis never executes or rewrites Skill content. It inventories recognizable
scripts and flags dependencies, network use, writes, mixed runtimes, and unknown
executables for administrator review before the exact tree is approved.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_ADAPTED_ARGS = 32

_PY_STDLIB = frozenset(sys.stdlib_module_names)

_PY_NETWORK = re.compile(
    r"\b(?:urllib|requests|httpx|aiohttp|socket|ftplib)\b|"
    r"\b(?:urlopen|requests\.|get\(|post\()\b|curl\b|wget\b",
    re.I,
)
_PY_DEP = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_]+)", re.M)
_PY_WRITE = re.compile(r"\bopen\([^)]*['\"][wa]x?\+?['\"]|Path\([^)]*\)\.write_|shutil\.(?:copy|move)\(", re.M)
_NODE_NETWORK = re.compile(
    r"\b(?:fetch|axios|got|request|superagent|node-fetch|undici)\b|"
    r"require\(['\"](?:https?|net|dns|tls)['\"]\)|curl\b|wget\b",
    re.I,
)
_NODE_DEP = re.compile(r"(?:require\(['\"]|from\s+['\"])([@A-Za-z0-9_.-]+)['\"]", re.M)
_NODE_WRITE = re.compile(r"\bfs\.(?:writeFile|appendFile|createWriteStream|copyFile|rename)\b|writeFileSync", re.M)
_BASH_NETWORK = re.compile(r"\b(?:curl|wget|nc|netcat|socat|nmap|ssh|scp)\b", re.I)
_PROMPT_COMMAND_DEP = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:belt|npm|npx|pnpm|yarn|bun)(?![A-Za-z0-9_.-])",
    re.I,
)

_BASH_RAW = re.compile(r"\bpython3?|node|perl|ruby|php\b", re.I)

_PDF_ENTRYPOINT = r'''"""Harness-managed PDF generator for the approved pdf Skill."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def _output_path(value: object, workspace: Path) -> Path:
    name = str(value or "document.pdf").strip()
    relative = Path(name)
    if (
        not name
        or relative.is_absolute()
        or relative.suffix.lower() != ".pdf"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("output must be a relative .pdf path inside the workspace")
    target = (workspace / relative).resolve()
    target.relative_to(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _paragraphs(payload: dict[str, object]) -> list[str]:
    values = payload.get("paragraphs")
    if isinstance(values, list):
        return [str(value) for value in values if str(value).strip()]
    content = payload.get("content", payload.get("text", ""))
    return [line for line in str(content).splitlines() if line.strip()]


payload = json.load(sys.stdin)
if not isinstance(payload, dict):
    raise ValueError("input must be a JSON object")
workspace = Path(os.environ["PI_WORKSPACE_ROOT"]).resolve()
target = _output_path(payload.get("output"), workspace)
font_paths = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
font_path = next((path for path in font_paths if Path(path).is_file()), "")
if not font_path:
    raise RuntimeError("Harness PDF requires a Noto CJK font")
pdfmetrics.registerFont(TTFont("HarnessCJK", font_path, subfontIndex=0))
body = ParagraphStyle(
    "HarnessBody", fontName="HarnessCJK", fontSize=11, leading=18
)
title_style = ParagraphStyle(
    "HarnessTitle",
    parent=body,
    alignment=TA_CENTER,
    fontSize=18,
    leading=26,
    spaceAfter=16,
)
story = []
title = str(payload.get("title", "")).strip()
if title:
    story.append(Paragraph(escape(title), title_style))
for value in _paragraphs(payload):
    story.append(Paragraph(escape(value).replace("\n", "<br/>"), body))
    story.append(Spacer(1, 8))
if not story:
    story.append(Paragraph("", body))
SimpleDocTemplate(str(target), pagesize=A4).build(story)
print(json.dumps({"output": str(target.relative_to(workspace))}, ensure_ascii=False))
'''


@dataclass(frozen=True, slots=True)
class AdaptationReport:
    runtime: str | None
    entrypoint: str
    dependencies: tuple[str, ...] = ()
    network_calls: tuple[str, ...] = ()
    filesystem_writes: bool = False
    requires_review: bool = False
    prompt_only: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "dependencies": list(self.dependencies),
            "network_calls": list(self.network_calls),
            "filesystem_writes": self.filesystem_writes,
            "requires_review": self.requires_review,
            "prompt_only": self.prompt_only,
            "notes": list(self.notes),
        }


class SkillAdaptationError(ValueError):
    """A third-party Skill cannot be safely adapted."""


def _script_candidates(root: Path, suffix: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if path.is_file()
        and not path.is_symlink()
        and ".git" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def analyze_skill(root: Path) -> AdaptationReport:
    """Inspect a quarantined Skill directory without trusting its manifest."""
    python_scripts = _script_candidates(root, ".py")
    node_scripts = _script_candidates(root, ".js")
    bash_scripts = _script_candidates(root, ".sh")
    recognized = {*python_scripts, *node_scripts, *bash_scripts}
    unknown_scripts = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path not in recognized
        and path.name not in {"SKILL.md", "skill.manifest.json"}
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
        and (
            "scripts" in path.relative_to(root).parts[:-1]
            or bool(path.stat().st_mode & 0o111)
        )
    )
    runtime_count = sum(
        bool(scripts) for scripts in (python_scripts, node_scripts, bash_scripts)
    )
    if runtime_count > 1 or unknown_scripts:
        details = []
        if runtime_count > 1:
            details.append("检测到混合脚本运行时")
        if unknown_scripts:
            details.append(
                "未识别脚本: "
                + ", ".join(str(path.relative_to(root)) for path in unknown_scripts)
            )
        return AdaptationReport(
            runtime="mixed" if runtime_count > 1 else None,
            entrypoint="",
            requires_review=True,
            prompt_only=False,
            notes=tuple(details),
        )

    notes: list[str] = []
    if python_scripts and not node_scripts and not bash_scripts:
        main = _pick_entrypoint(python_scripts)
        deps: set[str] = set()
        network: set[str] = set()
        writes = False
        for script in python_scripts:
            text = script.read_text(encoding="utf-8", errors="replace")
            deps.update(_PY_DEP.findall(text))
            if _PY_NETWORK.search(text):
                network.update(_extract_hosts(text))
                notes.append(f"{script.name}: 检测到网络调用，需人工复核")
            if _PY_WRITE.search(text):
                writes = True
        deps.difference_update(_PY_STDLIB)
        deps.discard("__future__")
        return AdaptationReport(
            runtime="python",
            entrypoint=str(main.relative_to(root)),
            dependencies=tuple(sorted(deps)),
            network_calls=tuple(sorted(network)),
            filesystem_writes=writes,
            requires_review=bool(deps or network or writes),
            notes=tuple(notes),
        )

    if node_scripts and not python_scripts and not bash_scripts:
        main = _pick_entrypoint(node_scripts)
        deps: set[str] = set()
        network: set[str] = set()
        writes = False
        for script in node_scripts:
            text = script.read_text(encoding="utf-8", errors="replace")
            deps.update(
                dep
                for dep in _NODE_DEP.findall(text)
                if not dep.startswith("node:")
            )
            if _NODE_NETWORK.search(text):
                network.update(_extract_hosts(text))
                notes.append(f"{script.name}: 检测到网络调用，需人工复核")
            if _NODE_WRITE.search(text):
                writes = True
        return AdaptationReport(
            runtime="node",
            entrypoint=str(main.relative_to(root)),
            dependencies=tuple(sorted(deps)),
            network_calls=tuple(sorted(network)),
            filesystem_writes=writes,
            requires_review=bool(deps or network or writes),
            notes=tuple(notes),
        )

    if bash_scripts and not python_scripts and not node_scripts:
        main = _pick_entrypoint(bash_scripts)
        notes = []
        for script in bash_scripts:
            text = script.read_text(encoding="utf-8", errors="replace")
            if _BASH_NETWORK.search(text):
                notes.append(f"{script.name}: 检测到网络/远程命令")
            if _BASH_RAW.search(text):
                notes.append(f"{script.name}: 检测到解释器调用")
        return AdaptationReport(
            runtime="bash",
            entrypoint=str(main.relative_to(root)),
            requires_review=True,
            notes=tuple(notes) or ("Bash Skill 需要人工复核",),
        )

    skill_definition = root / "SKILL.md"
    prompt_only = skill_definition.is_file()
    prompt_dependencies: tuple[str, ...] = ()
    prompt_network: tuple[str, ...] = ()
    prompt_notes: list[str] = []
    if prompt_only:
        prompt_text = skill_definition.read_text(encoding="utf-8", errors="replace")
        prompt_dependencies = tuple(
            sorted(
                {
                    match.group(0).lower()
                    for match in _PROMPT_COMMAND_DEP.finditer(prompt_text)
                }
            )
        )
        prompt_network = tuple(_extract_hosts(prompt_text))
        if prompt_dependencies:
            prompt_notes.append(
                "SKILL.md 引用了外部包管理器或 CLI："
                + ", ".join(prompt_dependencies)
                + "，需确认 sandbox 镜像已预装且凭据已配置"
            )
        if prompt_network:
            prompt_notes.append(
                "SKILL.md 包含外部网络地址："
                + ", ".join(prompt_network)
                + "，需确认网络能力和白名单"
            )
        if not prompt_notes:
            prompt_notes.append("未检测到脚本；将按标准提示型 Skill 原生加载")
    else:
        prompt_notes.append("未识别到标准 SKILL.md 或可分析脚本，需要人工处理")
    return AdaptationReport(
        runtime=None,
        entrypoint="",
        dependencies=prompt_dependencies,
        network_calls=prompt_network,
        requires_review=bool(prompt_dependencies or prompt_network) or not prompt_only,
        prompt_only=prompt_only,
        notes=tuple(prompt_notes),
    )


def _pick_entrypoint(candidates: list[Path]) -> Path:
    for name in ("main", "index", "run", "start", "entry", "app"):
        for candidate in candidates:
            if candidate.stem.lower() == name:
                return candidate
    return candidates[0]


def _extract_hosts(text: str) -> list[str]:
    hosts: set[str] = set()
    for match in re.finditer(
        r"https?://([A-Za-z0-9._-]+(?::\d+)?)", text
    ):
        host = match.group(1).lower()
        if host not in {"localhost", "127.0.0.1", "0.0.0.0"}:
            hosts.add(host)
    return sorted(hosts)


_PY_WRAPPER = '''"""Harness-generated fixed entrypoint (auto-adapted).

Input arrives as JSON on stdin; only the validated argument list is passed
to the original script. Never edit this file by hand; re-run adaptation.
"""

from __future__ import annotations

import json
import pathlib
import runpy
import sys

_payload = json.load(sys.stdin)
_args = _payload.get("args", []) if isinstance(_payload, dict) else []
if not isinstance(_args, list) or len(_args) > {max_args} or any(
    not isinstance(item, str) for item in _args
):
    raise SystemExit("input.args must be a list of at most {max_args} strings")
_script = pathlib.Path(__file__).resolve().parent / "{entry}"
sys.argv = ["{entry}", *_args]
runpy.run_path(str(_script), run_name="__main__")
'''

_NODE_WRAPPER = '''// Harness-generated fixed entrypoint (auto-adapted).
const path = require("node:path");
const readline = require("node:readline");
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  let payload = {};
  try {
    payload = JSON.parse(line);
  } catch {
    process.exit(2);
  }
  const args = Array.isArray(payload.args) ? payload.args : [];
  if (
    args.length > {max_args} ||
    args.some((item) => typeof item !== "string")
  ) {
    console.error("input.args must be a list of at most {max_args} strings");
    process.exit(1);
  }
  process.argv = ["{entry}", ...args];
  require(path.join(__dirname, "{entry}"));
});
'''


def write_adapted_entrypoint(
    root: Path, report: AdaptationReport, skill_id: str
) -> Path:
    """Generate a fixed entrypoint next to the original script."""
    if report.runtime == "python":
        wrapper_name = f"{skill_id}_entrypoint.py"
        wrapper = root / wrapper_name
        wrapper.write_text(
            _PY_WRAPPER.format(entry=report.entrypoint, max_args=MAX_ADAPTED_ARGS),
            encoding="utf-8",
        )
        return wrapper
    if report.runtime == "node":
        wrapper_name = f"{skill_id}_entrypoint.js"
        wrapper = root / wrapper_name
        wrapper.write_text(
            _NODE_WRAPPER.format(entry=report.entrypoint, max_args=MAX_ADAPTED_ARGS),
            encoding="utf-8",
        )
        return wrapper
    raise SkillAdaptationError("auto-adaptation only supports python/node")


def build_manifest(
    report: AdaptationReport,
    skill_id: str,
    version: str,
) -> dict[str, Any]:
    """Conservative capability request derived from static analysis.

    Network hosts are requests for the server-side allowlist; filesystem is
    read-only unless the analysis found writes (which flags review). Skills
    with detected direct network calls are forced to container execution so
    the local executor can never bypass the network gate.
    """
    if report.runtime not in {"python", "node", "bash"}:
        raise SkillAdaptationError("cannot build manifest without a runtime")
    return {
        "skill_id": skill_id,
        "version": version,
        "runtime": report.runtime,
        "entrypoint": report.entrypoint,
        "capabilities": {
            "filesystem": "workspace-rw" if report.filesystem_writes else "workspace-ro",
            "network": sorted(report.network_calls),
            "credentials": [],
            "network_execution": (
                "container" if report.network_calls else "broker-client"
            ),
        },
    }


def _adapt_builtin_pdf(root: Path, skill_id: str, version: str) -> dict[str, Any]:
    entrypoint = root / "pdf_entrypoint.py"
    entrypoint.write_text(_PDF_ENTRYPOINT, encoding="utf-8")
    report = AdaptationReport(
        runtime="python",
        entrypoint=entrypoint.name,
        filesystem_writes=True,
        prompt_only=False,
        notes=("已生成 Harness 固定 PDF 入口，使用 reportlab 写入工作区",),
    )
    manifest = build_manifest(report, skill_id, version)
    (root / "skill.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"report": report.as_dict(), "manifest": manifest}


def adapt_skill_dir(root: Path, skill_id: str, version: str) -> dict[str, Any]:
    """Analyze and adapt a quarantined Skill directory in place.

    Returns the adaptation report plus generated manifest. When the report
    requires review, the manifest is still written (as a request) but the
    caller must surface the report to the administrator.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", skill_id):
        raise SkillAdaptationError("invalid skill_id")
    report = analyze_skill(root)
    if skill_id == "pdf" and (root / "SKILL.md").is_file():
        return _adapt_builtin_pdf(root, skill_id, version)
    if report.notes and any("已包含 skill.manifest.json" in note for note in report.notes):
        return {"report": report.as_dict(), "manifest": None}
    if report.runtime is None:
        # Prompt-only Skill (SKILL.md without scripts): no executable manifest
        # is generated, but external CLI/network references remain review data.
        report = AdaptationReport(
            runtime=None,
            entrypoint="",
            dependencies=report.dependencies,
            network_calls=report.network_calls,
            requires_review=report.requires_review,
            prompt_only=report.prompt_only,
            notes=(
                *report.notes,
                "纯提示型 Skill（无脚本），挂载后仅供 Agent 读取 SKILL.md，不注册可执行入口",
            ),
        )
        return {"report": report.as_dict(), "manifest": None}
    if report.runtime not in {"python", "node"}:
        raise SkillAdaptationError(report.notes[0] if report.notes else "unsupported Skill")
    wrapper = write_adapted_entrypoint(root, report, skill_id)
    manifest = build_manifest(report, skill_id, version)
    manifest["entrypoint"] = wrapper.name
    (root / "skill.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"report": report.as_dict(), "manifest": manifest}
