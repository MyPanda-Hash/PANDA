from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

POLICY = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "pi_runtime"
    / "extensions"
    / "tool_policy.ts"
)


def _node_available() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(not _node_available(), reason="node is required to evaluate tool_policy.ts")


def _eval(fn: str, *args: object) -> str | None:
    script = f"""
import {{ pathToFileURL }} from "node:url";
const mod = await import(pathToFileURL({json.dumps(str(POLICY))}).href);
const args = {json.dumps(args)};
const result = mod[{json.dumps(fn)}](...args);
process.stdout.write(JSON.stringify(result ?? null));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or "node failed")
    return json.loads(completed.stdout)


def test_strict_browser_confines_tools_and_skill_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    skill = skills / "agent-browser" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("批准的浏览器能力", encoding="utf-8")
    outside = tmp_path / "models.json"
    outside.write_text("不能读取的运行配置", encoding="utf-8")
    alias = skill.parent / "config-link"
    alias.symlink_to(outside)
    monkeypatch.setenv("PI_STRICT_BROWSER_SOURCE", "1")
    assert _eval("strictBrowserToolReason", "browser_read_page", {"url": "https://example.com"}, str(skills)) is None
    assert _eval("strictBrowserToolReason", "read", {"path": str(skill)}, str(skills)) is None
    for tool_name, arguments in [
        ("bash", {"command": "echo unwanted > result.txt"}),
        ("write", {"path": "result.txt"}),
        ("http_request", {"url": "https://example.com"}),
        ("read", {"path": str(outside)}),
        ("read", {"path": str(alias)}),
    ]:
        assert _eval("strictBrowserToolReason", tool_name, arguments, str(skills)) is not None
    monkeypatch.delenv("PI_STRICT_BROWSER_SOURCE")
    assert _eval("strictBrowserToolReason", "bash", {"command": "echo normal"}, str(skills)) is None


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("bash", {"command": "curl https://example.com"}),
        ("write", {"path": "../outside.txt", "content": "not allowed"}),
    ],
)
def test_unattended_policy_rejection_pauses_execution(
    tmp_path: Path, tool_name: str, arguments: dict
) -> None:
    script = f"""
import {{ pathToFileURL }} from "node:url";
const policy = await import(pathToFileURL({json.dumps(str(POLICY))}).href);
let guard;
policy.default({{ on: (_event, handler) => {{ guard = handler; }} }});
const notices = [];
const controller = new AbortController();
const result = await guard(
  {{ toolName: {json.dumps(tool_name)}, input: {json.dumps(arguments)} }},
  {{ abort: () => controller.abort(), ui: {{ notify: (message) => notices.push({{ type: "extension_ui_request", method: "notify", message }}) }} }},
);
process.stdout.write(JSON.stringify({{ result, aborted: controller.signal.aborted }}));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1", "PI_EXECUTION_PROFILE": "sandbox",
            "PI_UNATTENDED_RESTRICTED": "1", "PI_WORKSPACE_ROOT": str(tmp_path),
            "PI_SHARED_SKILLS_ROOT": str(tmp_path / "approved-skills"),
        },
    )
    result = json.loads(completed.stdout)
    assert result["result"]["block"] is True
    assert result["aborted"] is True


@pytest.fixture
def docker_layout(tmp_path: Path) -> dict[str, str]:
    workspace = tmp_path / "data" / "pi-runtime" / "workspaces" / "_system" / "doc-nexus-pi" / "session-1"
    skills = tmp_path / "data" / "pi-runtime" / "skills" / "_system" / "doc-nexus-pi"
    app_dir = tmp_path / "app" / "backend" / "app"
    uploads = tmp_path / "app" / "backend" / "uploads"
    workspace.mkdir(parents=True)
    skills.mkdir(parents=True)
    app_dir.mkdir(parents=True)
    uploads.mkdir(parents=True)
    (workspace / "notes.md").write_text("ok\n", encoding="utf-8")
    (skills / "writing-proposal").mkdir()
    (skills / "writing-proposal" / "SKILL.md").write_text("# 写方案\n", encoding="utf-8")
    (app_dir / "main.py").write_text("app = None\n", encoding="utf-8")
    return {
        "workspace": str(workspace.resolve()),
        "skills": str(skills.resolve()),
        "app": str((tmp_path / "app").resolve()),
        "uploads": str(uploads.resolve()),
    }


def _rel(target: str, workspace: str) -> str:
    return os.path.relpath(target, workspace)


def test_write_blocks_container_app_and_uploads(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    app_main = os.path.join(docker_layout["app"], "backend", "app", "main.py")
    assert _eval("evaluateFileAccess", "write", "./notes.md", workspace, skills) is None
    assert _eval("evaluateFileAccess", "write", _rel(app_main, workspace), workspace, skills)
    assert _eval("evaluateFileAccess", "write", docker_layout["uploads"] + "/pwned", workspace, skills)
    assert _eval("evaluateFileAccess", "write", "/usr/local/bin/pi", workspace, skills)


def test_write_cannot_mutate_shared_skills(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    relative_skill = _rel(f"{skills}/writing-proposal/SKILL.md", workspace)
    assert _eval("evaluateFileAccess", "read", relative_skill, workspace, skills) is None
    assert _eval("evaluateFileAccess", "write", relative_skill, workspace, skills)
    assert _eval("evaluateFileAccess", "edit", f"{skills}/writing-proposal/SKILL.md", workspace, skills)


def test_bash_blocks_relative_escape_and_system_writes(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    app_pwned = os.path.join(docker_layout["app"], "pwned")
    assert _eval("evaluateBashCommand", "echo hi > ./out.txt", workspace, skills) is None
    assert _eval("evaluateBashCommand", "/bin/echo hello", workspace, skills) is None
    assert _eval("evaluateBashCommand", f"echo pwned > {_rel(app_pwned, workspace)}", workspace, skills)
    assert _eval("evaluateBashCommand", "echo pwned > /usr/local/bin/pi", workspace, skills)
    assert _eval("evaluateBashCommand", "echo pwned > /app/backend/app/main.py", workspace, skills)



def test_bash_allows_docx_xml_heredoc_in_workspace(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    command = f"""
cd {workspace}

mkdir -p docx_tmp/word/_rels
mkdir -p docx_tmp/_rels

cat > docx_tmp/'[Content_Types].xml' << 'XMLEOF'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
XMLEOF

echo "XML files created successfully"
""".strip()
    assert _eval("evaluateBashCommand", command, workspace, skills) is None
    assert _eval(
        "evaluateBashCommand",
        'echo \'<Override PartName="/word/document.xml"/>\' > ./content_types.xml',
        workspace,
        skills,
    ) is None
    assert _eval(
        "evaluateBashCommand",
        "cat /etc/passwd",
        workspace,
        skills,
    )
    assert _eval(
        "evaluateBashCommand",
        "echo pwned > /usr/local/bin/pi",
        workspace,
        skills,
    )


def test_bash_resolves_paths_after_workspace_cd(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    assert _eval(
        "evaluateBashCommand",
        "cd ../outside && zip -r ../report.docx .",
        workspace,
        skills,
    )
    build_dir = Path(workspace) / "docx_build"
    build_dir.mkdir()
    assert _eval(
        "evaluateBashCommand",
        "cd docx_build && zip -r ../report.docx .",
        workspace,
        skills,
    ) is None


def test_sandbox_relies_on_container_boundary_without_tool_policy_blocks(
    docker_layout: dict[str, str], monkeypatch
) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    monkeypatch.setenv("PI_EXECUTION_PROFILE", "sandbox")
    assert _eval("evaluateBashCommand", "python3 -c 'print(1)'", workspace, skills) is None
    assert _eval("evaluateBashCommand", "curl https://example.com", workspace, skills) is None
    assert _eval("evaluateBashCommand", "pip install requests", workspace, skills) is None
    assert _eval("evaluateBashCommand", "xxd report.docx", workspace, skills) is None
    assert _eval("evaluateBashCommand", "cat /etc/passwd", workspace, skills) is None
    assert _eval("evaluateFileAccess", "write", "/etc/anything", workspace, skills) is None


def test_unattended_sandbox_blocks_executable_and_hidden_config_tools(
    docker_layout: dict[str, str], monkeypatch
) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    monkeypatch.setenv("PI_EXECUTION_PROFILE", "sandbox")
    monkeypatch.setenv("PI_UNATTENDED_RESTRICTED", "1")

    assert _eval("unattendedToolReason", "bash")
    assert _eval("unattendedToolReason", "http_request")
    assert _eval("unattendedToolReason", "browser_read_page")
    assert _eval("unattendedToolReason", "request_skill_install")
    assert _eval("unattendedToolReason", "nexus_rename_chat_session")
    assert _eval("unattendedToolReason", "read") is None
    assert _eval("unattendedToolReason", "write") is None
    assert _eval("unattendedToolReason", "nexus_search_evidence") is None
    assert _eval("evaluateFileAccess", "read", "/pi-config/models.json", workspace, skills)
    assert _eval("evaluateFileAccess", "write", "/etc/anything", workspace, skills)
    assert _eval("evaluateFileAccess", "write", ".pi/extensions/evil.ts", workspace, skills)
    assert _eval("evaluateFileAccess", "write", "result.md", workspace, skills) is None



def test_sandbox_preflights_unavailable_cli_retries(
    docker_layout: dict[str, str], monkeypatch
) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    monkeypatch.setenv("PI_EXECUTION_PROFILE", "sandbox")

    belt_reason = _eval("evaluateBashCommand", "belt app run tavily/search-assistant", workspace, skills)
    assert belt_reason is not None
    assert "belt CLI" in belt_reason

    package_manager_reason = _eval("evaluateBashCommand", "which npx && npx --version", workspace, skills)
    assert package_manager_reason is not None
    assert "Node 包管理器" in package_manager_reason


def test_bash_blocks_zip_hex_inspection(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    assert _eval("evaluateBashCommand", "xxd report.docx", workspace, skills)
    assert _eval("evaluateBashCommand", "hexdump -C out.zip", workspace, skills)
    assert _eval("evaluateBashCommand", "od -A x -t x1z -N 32 file.docx", workspace, skills)
    assert _eval("evaluateBashCommand", "ls -l report.docx", workspace, skills) is None


def test_bash_blocks_interpreters_kill_and_ssrf(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    assert _eval("evaluateBashCommand", "python3 -c 'open(\"/app/backend/app/main.py\",\"w\")'", workspace, skills)
    assert _eval("evaluateBashCommand", "python3 ./notes.md", workspace, skills)
    assert _eval("evaluateBashCommand", "kill 1", workspace, skills)
    assert _eval("evaluateBashCommand", "pkill uvicorn", workspace, skills)
    assert _eval("evaluateBashCommand", "curl http://127.0.0.1:8000/health", workspace, skills)
    assert _eval("evaluateBashCommand", "npx skills find flight", workspace, skills)
    assert _eval("evaluateBashCommand", "echo $NEXUS_CAPABILITY_TOKEN", workspace, skills)


def test_bash_cannot_rewrite_shared_skills(docker_layout: dict[str, str]) -> None:
    workspace, skills = docker_layout["workspace"], docker_layout["skills"]
    skill_md = f"{skills}/writing-proposal/SKILL.md"
    assert _eval(
        "evaluateBashCommand",
        f"cat {_rel(skill_md, workspace)}",
        workspace,
        skills,
    )
    assert _eval(
        "evaluateBashCommand",
        f"echo pwned > {skills}/writing-proposal/SKILL.md",
        workspace,
        skills,
    )


@pytest.mark.parametrize("status", [200, 404, 429])
def test_http_tool_reports_target_status_not_broker_status(status: int) -> None:
    gateway = POLICY.with_name("tool_gateway_proxy.ts")
    script = f"""
import {{ registerHooks }} from "node:module";
import {{ pathToFileURL }} from "node:url";
registerHooks({{
  resolve(specifier, context, next) {{
    if (specifier === "@earendil-works/pi-coding-agent")
      return {{ url: "data:text/javascript,export const defineTool = x => x", shortCircuit: true }};
    if (specifier === "typebox")
      return {{ url: "data:text/javascript,export const Type = new Proxy({{}}, {{get: () => () => ({{}})}})", shortCircuit: true }};
    return next(specifier, context);
  }}
}});
const tools = new Map();
const mod = await import(pathToFileURL({json.dumps(str(gateway))}).href);
mod.default({{ registerTool: tool => tools.set(tool.name, tool), on: () => {{}} }});
const payload = {{ status: {status}, headers: {{}}, body: "target response" }};
globalThis.fetch = async () => new Response(JSON.stringify(payload), {{status: 200}});
try {{
  const result = await tools.get("http_request").execute("call", {{method: "GET", url: "https://example.com/"}});
  console.log(JSON.stringify({{failed: false, status: result.details.status}}));
}} catch (error) {{
  console.log(JSON.stringify({{failed: true, message: error.message}}));
}}
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "NODE_NO_WARNINGS": "1",
            "HARNESS_GATEWAY_URL": "https://gateway.example",
            "HARNESS_GATEWAY_TOKEN": "test-token",
        },
    )
    result = json.loads(completed.stdout)
    assert result["failed"] is (status >= 400)
    if status >= 400:
        assert f"HTTP {status}" in result["message"]
        assert "target response" in result["message"]
    else:
        assert result["status"] == 200



@pytest.mark.parametrize("strict", [False, True])
def test_browser_extension_exposes_only_turn_authorized_tools(strict: bool, tmp_path: Path) -> None:
    script = f"""
import {{ registerHooks }} from "node:module";
import {{ pathToFileURL }} from "node:url";
registerHooks({{
  resolve(specifier, context, next) {{
    if (specifier === "@earendil-works/pi-coding-agent")
      return {{url: "data:text/javascript,export const defineTool = x => x;", shortCircuit: true}};
    if (specifier === "typebox")
      return {{url: "data:text/javascript,export const Type = new Proxy({{}}, {{get: () => () => ({{}})}});", shortCircuit: true}};
    return next(specifier, context);
  }}
}});
const tools = [];
const hooks = [];
const pi = {{registerTool: tool => tools.push(tool.name), on: (_event, handler) => hooks.push(handler)}};
(await import(pathToFileURL({json.dumps(str(POLICY.with_name("tool_gateway_proxy.ts")))}).href)).default(pi);
(await import(pathToFileURL({json.dumps(str(POLICY))}).href)).default(pi);
const rejected = [];
for (const toolName of ["bash", "write", "http_request"]) {{
  for (const hook of hooks) {{
    const result = await hook({{toolName, input: {{command: "echo forbidden", path: "output.txt"}}}}, {{ui: {{notify: () => {{}}}}, abort: () => {{}}}});
    if (result?.block) {{ rejected.push(toolName); break; }}
  }}
}}
console.log(JSON.stringify({{tools, rejected}}));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1", "PI_EXECUTION_PROFILE": "sandbox",
            "PI_STRICT_BROWSER_SOURCE": "1" if strict else "0",
            "PI_UNATTENDED_RESTRICTED": "0", "PI_WORKSPACE_ROOT": str(tmp_path),
        },
    )
    result = json.loads(completed.stdout)
    if strict:
        assert result["tools"] == ["browser_read_page"]
        assert result["rejected"] == ["bash", "write", "http_request"]
    else:
        assert "browser_read_page" not in result["tools"]
        assert "http_request" in result["tools"]
        assert result["rejected"] == []

@pytest.mark.parametrize(
    "scenario",
    [
        "redirect", "truncated", "http_error", "missing_status", "empty",
        "ambiguous", "malformed", "cancelled", "close_failure", "credential_url",
        "file_url", "selector_injection",
    ],
)
def test_browser_source_receipt_requires_verified_navigation_and_cleanup(scenario: str) -> None:
    browser = POLICY.with_name("browser_read_page.ts")
    script = """
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { pathToFileURL } from "node:url";
import { existsSync, readFileSync } from "node:fs";
import vm from "node:vm";
const scenario = SCENARIO;
const controller = new AbortController();
const calls = [];
const actual = "https://www.iana.org/help/example-domains";
const raw = scenario === "truncated" ? "a".repeat(19999) + "😀suffix" : "原文 <script> ```\\nexample.com / example.org";
let root;
let requestedSelector;
childProcess.execFile = (file, argv, options, callback) => {
  const args = argv.slice(argv.indexOf("30s") + 1);
  calls.push(args[0]);
  root = options.cwd;
  if (file !== "/usr/local/bin/agent-browser" || options.shell !== false ||
      options.env.HOME !== root || options.env.AGENT_BROWSER_PROFILE ||
      options.env.PI_API_KEY || options.env.HARNESS_GATEWAY_TOKEN ||
      options.env.UNRELATED_SECRET || options.env.HTTP_PROXY ||
      readFileSync(argv[1], "utf8") !== "{}")
    throw new Error("Unsafe browser execution");
  let data = {};
  let error = null;
  let malformed = false;
  try {
    if (args[0] === "eval") {
      if (scenario === "malformed") malformed = true;
      const context = {
        document: {
          title: "IANA example domains",
          querySelectorAll: selector => {
            requestedSelector = selector;
            if (scenario === "ambiguous") return [{}, {}];
            return [{innerText: scenario === "empty" ? "  " : raw}];
          },
        },
        location: {href: actual},
      };
      data = {result: vm.runInNewContext(args[1], context), origin: actual};
      if (scenario === "cancelled") controller.abort();
    } else if (args[0] === "network") {
      data = {requests: calls.includes("eval") ? [{
        url: actual, resourceType: "Document",
        status: scenario === "http_error" ? 404 : scenario === "missing_status" ? undefined : 200,
      }] : []};
    } else if (args[0] === "close") {
      if (scenario === "close_failure") throw new Error("close failed");
      data = {closed: true};
    }
  } catch (caught) { error = caught; }
  queueMicrotask(() => callback(error, malformed ? "invalid JSON" : JSON.stringify({success: true, data})));
};
syncBuiltinESMExports();
const mod = await import(pathToFileURL(BROWSER_PATH).href);
const selector = scenario === "selector_injection" ? 'body"); throw new Error("injected"); //' : "body";
let result = null;
let failed = false;
try {
  result = await mod.readBrowserPage({
    url: scenario === "credential_url" ? "https://user:secret@example.com/" :
      scenario === "file_url" ? "file:///etc/passwd" : "https://iana.org/",
    selector,
  }, controller.signal);
} catch { failed = true; }
const cleaned = root ? !existsSync(root) : true;
// 失败关闭留下的目录仅属于当前测试，避免测试本身遗留文件。
if (root && existsSync(root)) {
  const {rm} = await import("node:fs/promises");
  await rm(root, {recursive: true, force: true});
}
console.log(JSON.stringify({failed, result, calls, cleaned, requestedSelector, selector, raw}));
""".replace("SCENARIO", json.dumps(scenario)).replace("BROWSER_PATH", json.dumps(str(browser)))
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1", "PI_UNATTENDED_RESTRICTED": "0",
            "PI_STRICT_BROWSER_SOURCE": "1", "PI_API_KEY": "model-secret-probe",
            "HARNESS_GATEWAY_TOKEN": "gateway-secret-probe",
            "UNRELATED_SECRET": "unknown-secret-probe", "HTTP_PROXY": "http://secret-proxy/",
            "AGENT_BROWSER_PROFILE": "/must-not-reuse-user-profile",
        },
    )
    output = json.loads(completed.stdout)
    success = scenario in {"redirect", "truncated", "selector_injection"}
    assert output["failed"] is not success
    if scenario in {"credential_url", "file_url"}:
        assert output["calls"] == []
    else:
        assert output["calls"][-1] == "close"
        assert output["cleaned"] is (scenario != "close_failure")
    if not success:
        assert output["result"] is None
        return
    details = output["result"]["details"]
    assert details == {
        "kind": "browser_source_v1",
        "url": "https://www.iana.org/help/example-domains",
        "title": "IANA example domains",
        "text": "a" * 19999 if scenario == "truncated" else output["raw"],
        "truncated": scenario == "truncated",
    }
    assert json.loads(output["result"]["content"][0]["text"]) == details
    assert output["requestedSelector"] == output["selector"]


@pytest.mark.parametrize("scenario", [
    "success", "collision", "short", "long", "digest", "wrong_version",
    "http_error", "stream_error", "cancelled", "symlink", "parent_symlink",
])
def test_presentation_extension_streams_original_and_cleans_failed_copy(tmp_path: Path, scenario: str):
    script = """
import { registerHooks } from "node:module";
import { pathToFileURL } from "node:url";
import { mkdir, readdir, readFile, stat, symlink, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-coding-agent")
      return {url: "data:text/javascript,export const defineTool = x => x", shortCircuit: true};
    if (specifier === "typebox")
      return {url: "data:text/javascript,export const Type = new Proxy({}, {get: () => () => ({})})", shortCircuit: true};
    if (specifier === "typebox/value")
      return {url: "data:text/javascript,export const Value = {Check: () => true}", shortCircuit: true};
    return next(specifier, context);
  }
});
const cwd = WORKSPACE;
const scenario = SCENARIO;
const outside = join(cwd, "outside");
await mkdir(outside);
await writeFile(join(outside, "keep.pptx"), "user original");
if (scenario === "parent_symlink") {
  await symlink(outside, join(cwd, ".pi"), "dir");
} else {
  await mkdir(join(cwd, ".pi"));
  if (scenario === "symlink") await symlink(outside, join(cwd, ".pi", "attachments"), "dir");
  else await mkdir(join(cwd, ".pi", "attachments"));
}
const payload = new TextEncoder().encode("exact-original-presentation-bytes");
const controller = new AbortController();
let transport;
globalThis.fetch = async (url, options) => {
  transport = {url, method: options.method, redirect: options.redirect,
    auth: options.headers.Authorization, agent: options.headers["X-Nexus-Agent-ID"],
    body: JSON.parse(options.body)};
  let offset = 0;
  const body = new ReadableStream({
    pull(c) {
      if (offset && scenario === "stream_error") return c.error(new Error("private host path"));
      if (offset && scenario === "cancelled") controller.abort();
      if (offset >= payload.length) return c.close();
      c.enqueue(payload.slice(offset, offset + 5));
      offset += 5;
    }
  });
  return new Response(body, {
    status: scenario === "http_error" ? 403 : 200,
    headers: {
      "Content-Length": String(payload.length + (scenario === "short" ? 1 : scenario === "long" ? -1 : 0)),
      "X-Nexus-Document-ID": "doc",
      "X-Nexus-Version-ID": scenario === "wrong_version" ? "other" : "version",
      "X-Nexus-Source-Format": "pptx",
      "X-Nexus-SHA256": scenario === "digest" ? "0".repeat(64) : createHash("sha256").update(payload).digest("hex"),
    },
  });
};
const tools = new Map();
(await import(pathToFileURL(EXTENSION).href)).default({
  registerTool: tool => tools.set(tool.name, tool), on: () => {},
});
let failed = false;
let message = null;
let first = null;
let second = null;
try {
  first = await tools.get("nexus_read_presentation").execute(
    "call", {document_id: "doc", version_id: "version"}, controller.signal, undefined, {cwd},
  );
  if (scenario === "collision") second = await tools.get("nexus_read_presentation").execute(
    "call", {document_id: "doc", version_id: "version"}, controller.signal, undefined, {cwd},
  );
} catch (error) { failed = true; message = error.message; }
const bytes = first ? await readFile(join(cwd, first.details.path), "utf8") : null;
const mode = first ? (await stat(join(cwd, first.details.path))).mode & 0o777 : null;
const files = await readdir(join(cwd, ".pi", "attachments")).catch(() => []);
console.log(JSON.stringify({
  failed, message, first, second, bytes, mode, files, transport,
  protectedOriginal: await readFile(join(outside, "keep.pptx"), "utf8"),
  outsideFiles: await readdir(outside),
}));
""".replace("WORKSPACE", json.dumps(str(tmp_path))).replace(
        "SCENARIO", json.dumps(scenario)
    ).replace("EXTENSION", json.dumps(str(POLICY.with_name("nexus_capability.ts"))))
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1",
            "NEXUS_CAPABILITY_BASE_URL": "https://capability.example",
            "NEXUS_CAPABILITY_TOKEN": "private-grant",
            "NEXUS_CAPABILITY_AGENT_ID": "pi-agent",
        },
    )
    result = json.loads(completed.stdout)
    success = scenario in {"success", "collision"}
    assert result["failed"] is not success
    assert result["protectedOriginal"] == "user original"
    assert result["outsideFiles"] == ["keep.pptx"]
    assert result["transport"]["auth"] == "Bearer private-grant"
    assert result["transport"]["redirect"] == "error"
    assert result["transport"]["body"]["arguments"] == {"document_id": "doc", "version_id": "version"}
    if success:
        details = result["first"]["details"]
        assert details["path"].startswith(".pi/attachments/presentation-")
        assert details["read_only"] is True
        assert details["source_format"] == "pptx"
        assert details["document_id"] == "doc"
        assert details["version_id"] == "version"
        assert result["bytes"] == "exact-original-presentation-bytes"
        assert result["mode"] == 0o400
        assert str(tmp_path) not in json.dumps(result["first"])
        assert "private-grant" not in json.dumps(result["first"])
        if scenario == "collision":
            assert result["second"]["details"]["path"] != details["path"]
    else:
        assert result["files"] == ([] if scenario != "symlink" else ["keep.pptx"])
        assert str(tmp_path) not in result["message"]
        assert "private-grant" not in result["message"]


@pytest.mark.parametrize("scenario", [
    "schedule_create", "unauthorized", "consecutive", "lifecycle",
    "loader_not_allowed", "unclassified", "nonlazy", "unattended", "strict_browser",
])
def test_capability_loader_preserves_registry_boundary_and_lifecycle(scenario: str):
    # 直接运行真实扩展；只替换 Pi 宿主，不替换工具加载逻辑或业务函数。
    script = """
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { pathToFileURL } from "node:url";
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-coding-agent")
      return {url: "data:text/javascript,export const defineTool = x => x", shortCircuit: true};
    if (specifier === "typebox")
      return {url: "data:text/javascript,export const Type = new Proxy({}, {get: () => () => ({})})", shortCircuit: true};
    if (specifier === "typebox/value")
      return {url: "data:text/javascript,export const Value = {Check: () => true}", shortCircuit: true};
    return next(specifier, context);
  }
});
const scenario = SCENARIO;
const loaderName = "nexus_load_capabilities";
const create = "nexus_propose_schedule";
const manage = "nexus_propose_schedule_action";
const search = "nexus_search_evidence";
let allowed = ["read", loaderName, create, manage, search];
if (scenario === "unauthorized") allowed = ["read", loaderName, create];
if (scenario === "loader_not_allowed") allowed = ["read", create, search];
if (scenario === "unclassified") allowed.push("future_authorized_tool");
const tools = new Map();
const hooks = new Map();
let active = [...allowed];
const changes = [];
let networkCalls = 0;
globalThis.fetch = async () => {
  networkCalls++;
  throw new Error("能力加载不得访问网络");
};
const pi = {
  registerTool: tool => tools.set(tool.name, tool),
  on: (name, callback) => {
    const callbacks = hooks.get(name) ?? [];
    callbacks.push(callback);
    hooks.set(name, callbacks);
  },
  getAllTools: () => allowed.map(name => ({name, description: name, parameters: {}})),
  getActiveTools: () => [...active],
  // 不模拟 SDK 的过滤保护，扩展传入越权名字必须直接暴露给断言。
  setActiveTools: names => {
    active = [...names];
    changes.push([...active]);
  },
};
(await import(pathToFileURL(EXTENSION).href)).default(pi);
async function emit(name) {
  for (const callback of hooks.get(name) ?? []) {
    await callback({type: name, prompt: "测试", messages: [], systemPrompt: ""}, {cwd: process.cwd()});
  }
}
function expectActive(expected) {
  assert.equal(new Set(active).size, active.length, "激活列表不得重复");
  assert.deepEqual([...active].sort(), [...expected].sort());
  assert.ok(active.every(name => allowed.includes(name)), "激活不得突破 registry");
}
async function load(groups) {
  assert.ok(active.includes(loaderName), "追加消息仍须能调用 loader");
  const result = await tools.get(loaderName).execute(
    "load", {groups}, new AbortController().signal, undefined, {cwd: process.cwd()},
  );
  assert.ok(changes.every(names => names.every(name => allowed.includes(name))),
    "任何中间激活操作都不得突破 registry");
  assert.deepEqual([...result.details.active_tools].sort(), [...active].sort());
  return result.details;
}
const base = ["read", loaderName];
if (["nonlazy", "unattended", "strict_browser"].includes(scenario)) {
  assert.equal(tools.has(loaderName), false);
  await emit("session_start");
  await emit("before_agent_start");
  expectActive(allowed);
  assert.deepEqual(changes, []);
} else if (scenario === "loader_not_allowed") {
  await emit("session_start");
  await emit("before_agent_start");
  expectActive(allowed);
  assert.deepEqual(changes, []);
} else {
  assert.ok(tools.has(loaderName));
  // 即使没有 session_start，首个模型请求前也要完成收缩。
  await emit("before_agent_start");
  expectActive(scenario === "unclassified" ? [...base, "future_authorized_tool"] : base);
  if (scenario === "schedule_create") {
    const result = await load(["schedule_create"]);
    assert.deepEqual(result.activated_tools, [create]);
    assert.deepEqual(result.unavailable_groups, []);
    expectActive([...base, create]);
  } else if (scenario === "unauthorized") {
    const result = await load(["schedule_manage", "memory_write"]);
    assert.deepEqual(result.activated_tools, []);
    assert.deepEqual([...result.unavailable_groups].sort(), ["memory_write", "schedule_manage"]);
    expectActive(base);
    for (const group of ["unknown", "__proto__", "constructor", "toString", manage]) {
      // 未知组必须安全报告不可用，原型属性也不能变成能力映射。
      const result = await load([group]);
      assert.deepEqual(result.activated_tools, []);
      assert.deepEqual(result.unavailable_groups, [group]);
      expectActive(base);
    }
    await load(["schedule_create"]);
    expectActive([...base, create]);
  } else if (scenario === "consecutive") {
    await load(["schedule_create"]);
    expectActive([...base, create]);
    // steer/follow_up 不触发 before_agent_start；同一 run 内直接加载下一组。
    await load(["knowledge"]);
    expectActive([...base, create, search]);
    const repeated = await load(["schedule_create", "knowledge", "schedule_create"]);
    assert.deepEqual([...repeated.activated_tools].sort(), [create, search].sort());
    assert.deepEqual(repeated.unavailable_groups, []);
    expectActive([...base, create, search]);
  } else if (scenario === "lifecycle") {
    await load(["schedule_create"]);
    expectActive([...base, create]);
    await emit("session_start");
    expectActive(base);
    await load(["knowledge"]);
    expectActive([...base, search]);
    // SDK reload 会重新建立 registry 并恢复全部 active，再发 session_start。
    active = [...allowed];
    await emit("session_start");
    expectActive(base);
    await load(["schedule_create"]);
    expectActive([...base, create]);
    await emit("before_agent_start");
    expectActive(base);
  } else if (scenario === "unclassified") {
    await load(["schedule_create"]);
    expectActive([...base, "future_authorized_tool", create]);
  }
}
assert.equal(networkCalls, 0);
console.log(JSON.stringify({passed: true}));
""".replace("SCENARIO", json.dumps(scenario)).replace(
        "EXTENSION", json.dumps(str(POLICY.with_name("nexus_capability.ts")))
    )
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1",
            "NEXUS_TOOL_LOADING": "eager" if scenario == "nonlazy" else "lazy",
            "PI_UNATTENDED_RESTRICTED": "1" if scenario == "unattended" else "0",
            "PI_STRICT_BROWSER_SOURCE": "1" if scenario == "strict_browser" else "0",
            "NEXUS_MEMORY_SCOPE": "none", "NEXUS_HISTORY_RECALL": "0",
        },
    )
    assert json.loads(completed.stdout) == {"passed": True}


def test_delivery_tool_rejects_originals_and_paths_outside_workspace(tmp_path: Path):
    from app.services.harness import file_storage

    rules = file_storage.workspace_delivery_rules()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".pi").mkdir()
    (workspace / ".pi" / "original.pptx").write_bytes(b"original")
    (workspace / "ready.txt").write_text("最终成果", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("不应交付", encoding="utf-8")
    (workspace / "linked.txt").symlink_to(outside)
    policy_cases = {
        **{f"{directory}/result.txt": "delivery_path_excluded" for directory in rules["excluded_dirs"]},
        **{name: "delivery_format_unsupported" for name in rules["excluded_names"]},
        **{f"result{suffix.upper()}": None for suffix in rules["suffixes"]},
        "slides.DECK.JSON": None,
        "intermediate.json": "delivery_format_unsupported",
        "large.txt": "delivery_file_too_large",
        "limit.txt": None,
    }
    for name in policy_cases:
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"final")
    for name, size in [("large.txt", rules["max_size"] + 1), ("limit.txt", rules["max_size"])]:
        with (workspace / name).open("wb") as output:
            output.truncate(size)
    for name, error in policy_cases.items():
        path = workspace / name
        scanner_accepts = (
            not file_storage._is_excluded(path, workspace)
            and file_storage._is_deliverable(path)
            and path.stat().st_size <= file_storage._MAX_SIZE
        )
        assert scanner_accepts == (error is None)
    script = f"""
import {{ registerHooks }} from "node:module";
import {{ pathToFileURL }} from "node:url";
registerHooks({{
  resolve(specifier, context, next) {{
    if (specifier === "@earendil-works/pi-coding-agent")
      return {{url: "data:text/javascript,export const defineTool = x => x", shortCircuit: true}};
    if (specifier === "typebox")
      return {{url: "data:text/javascript,export const Type = new Proxy({{}}, {{get: () => () => ({{}})}})", shortCircuit: true}};
    if (specifier === "typebox/value")
      return {{url: "data:text/javascript,export const Value = {{Check: () => true}}", shortCircuit: true}};
    return next(specifier, context);
  }}
}});
const tools = new Map();
(await import(pathToFileURL({json.dumps(str(POLICY.with_name("nexus_capability.ts")))}).href)).default({{
  registerTool: tool => tools.set(tool.name, tool), on: () => {{}},
}});
const ctx = {{cwd: {json.dumps(str(workspace))}}};
const deliver = paths => tools.get("nexus_deliver_files").execute("call", {{paths}}, undefined, undefined, ctx);
const allowed = await deliver(["./ready.txt", "ready.txt"]);
const rejected = [];
for (const path of [".pi/original.pptx", "../private.txt", "linked.txt", "."]) {{
  try {{ await deliver([path]); rejected.push(false); }}
  catch {{ rejected.push(true); }}
}}
const policy = {{}};
for (const path of {json.dumps(list(policy_cases))}) {{
  try {{ await deliver([path]); policy[path] = null; }}
  catch (error) {{ policy[path] = error.message.split("：")[0]; }}
}}
console.log(JSON.stringify({{allowed: allowed.details.deliverable_paths, rejected, policy}}));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
        env={
            **os.environ, "NODE_NO_WARNINGS": "1",
            "NEXUS_DELIVERY_RULES": json.dumps(rules),
        },
    )
    result = json.loads(completed.stdout)
    assert result["allowed"] == ["ready.txt"]
    assert result["rejected"] == [True, True, True, True]
    assert result["policy"] == policy_cases
