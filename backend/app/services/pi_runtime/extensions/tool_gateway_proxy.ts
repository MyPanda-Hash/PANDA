/**
 * tool_gateway_proxy.ts — Pi extension that proxies tool calls through
 * the Enterprise Harness ToolGateway (Python side).
 *
 * In host mode, tool calls are evaluated by the Gateway's Policy Engine.
 * The decision is:
 *
 *   ALLOW   → tool executes through Pi's native tool implementation
 *   DENY    → tool is blocked with the policy reason
 *   ASK     → tool is blocked; a pending approval is created server-side
 *   REWRITE → tool is blocked with a rewrite suggestion
 *
 * Sandbox mode uses Docker as the execution boundary and skips this handler;
 * enabled Skills can use native tools without a second Harness authorization
 * layer. The two explicit broker tools below still call their fixed endpoints.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readBrowserPage } from "./browser_read_page.ts";


const GATEWAY_URL_ENV = "HARNESS_GATEWAY_URL";
const TASK_ID_ENV = "HARNESS_TASK_ID";
const TENANT_ID_ENV = "HARNESS_TENANT_ID";
const USER_ID_ENV = "HARNESS_USER_ID";
const GATEWAY_TOKEN_ENV = "HARNESS_GATEWAY_TOKEN";
const WORKSPACE_ENV = "PI_WORKSPACE_ROOT";
const HARNESS_WORKSPACE_ENV = "HARNESS_WORKSPACE_ROOT";

function harnessWorkspace(): string {
	return process.env[HARNESS_WORKSPACE_ENV] || process.env[WORKSPACE_ENV] || "";
}

// 结构化 detail（{"error_code","message"} 或 FastAPI 校验数组）必须原样交给模型：
// String(detail) 会把对象折叠成 "[object Object]"，模型无从纠正。
function rejectDetail(body: unknown, status: number): string {
	let raw: unknown = `HTTP ${status}`;
	if (body && typeof body === "object" && "detail" in body) {
		raw = body.detail;
	}
	return typeof raw === "string" ? raw : JSON.stringify(raw) ?? String(raw);
}

type ToolCallResult = { block: true; reason: string } | void;

interface ToolCallEvent {
	toolName: string;
	input: Record<string, unknown>;
}

interface ToolCallCtx {
	ui: { confirm: (title: string, message: string) => Promise<boolean> };
}

interface PiHooks {
	on: (
		event: "tool_call",
		handler: (event: ToolCallEvent, ctx: ToolCallCtx) => Promise<ToolCallResult> | ToolCallResult,
	) => void;
}

interface GatewayResult {
	success: boolean;
	output: string;
	error: string;
	is_denied: boolean;

	approval_required: boolean;
	approval_request_id: string;
	rewritten: boolean;
}

const searchSkillCatalog = defineTool({
	name: "search_skill_catalog",
	label: "Search Skill catalog",
	description:
		"Search the official Skill catalog. If the user explicitly requests installation, call request_skill_install; it only queues quarantine and administrator review. Enabled Skills load in the next session.",
	parameters: Type.Object({
		query: Type.String({ minLength: 1, maxLength: 128 }),
		limit: Type.Optional(Type.Number({ minimum: 1, maximum: 8 })),
	}),
	async execute(callId, params) {
		const gatewayUrl = (process.env[GATEWAY_URL_ENV] || "").trim();
		const token = (process.env[GATEWAY_TOKEN_ENV] || "").trim();
		if (!gatewayUrl || !token) {
			throw new Error("Harness Skill catalog is not configured");
		}
		const response = await fetch(
			`${gatewayUrl.replace(/\/+$/, "")}/admin/harness/skills/catalog/search`,
			{
				method: "POST",
				headers: {
					Accept: "application/json",
					"Content-Type": "application/json",
					Authorization: `Bearer ${token}`,
					"X-Harness-Request-ID": callId,
				},
				body: JSON.stringify({
					query: params.query,
					limit: params.limit || 8,
					task_id: process.env[TASK_ID_ENV] || "",
					tenant_id: process.env[TENANT_ID_ENV] || "",
					user_id: process.env[USER_ID_ENV] || "",
				}),
				signal: AbortSignal.timeout(20_000),
			},
		);
		const result: unknown = await response.json().catch(() => ({}));
		if (!response.ok) {
			const detail = rejectDetail(result, response.status);
			throw new Error(
				`Skill catalog search rejected: ${detail} 不要改用 http_request 访问 skills.sh 或 GitHub；请说明目录暂不可用，并请用户到 Harness 管理端用 Git 安装。`,
			);
		}
		return {
			content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
			details: result,
		};
	},
});

const requestSkillInstall = defineTool({
	name: "request_skill_install",
	label: "Request Skill installation",
	description:
		"Queue a Skill from the official catalog for backend quarantine and administrator review. This never approves, enables, or runs the Skill.",
	parameters: Type.Object({
		skill_id: Type.String({ minLength: 1, maxLength: 128, pattern: "^[A-Za-z0-9_.-]+$" }),
		source: Type.String({
			minLength: 3,
			maxLength: 256,
			pattern: "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
		}),
		subpath: Type.String({ minLength: 1, maxLength: 512 }),
		version: Type.Optional(Type.String({ maxLength: 128 })),
	}),
	async execute(callId, params) {
		const gatewayUrl = (process.env[GATEWAY_URL_ENV] || "").trim();
		const token = (process.env[GATEWAY_TOKEN_ENV] || "").trim();
		if (!gatewayUrl || !token) {
			throw new Error("Harness Skill admission is not configured");
		}
		const response = await fetch(
			`${gatewayUrl.replace(/\/+$/, "")}/admin/harness/skills/catalog/request-install`,
			{
				method: "POST",
				headers: {
					Accept: "application/json",
					"Content-Type": "application/json",
					Authorization: `Bearer ${token}`,
					"X-Harness-Request-ID": callId,
				},
				body: JSON.stringify({
					skill_id: params.skill_id,
					source: params.source,
					subpath: params.subpath,
					version: params.version || "",
					task_id: process.env[TASK_ID_ENV] || "",
					tenant_id: process.env[TENANT_ID_ENV] || "",
					user_id: process.env[USER_ID_ENV] || "",
				}),
				signal: AbortSignal.timeout(10_000),
			},
		);
		const result: unknown = await response.json().catch(() => ({}));
		if (!response.ok) {
			const detail = rejectDetail(result, response.status);
			throw new Error(`Skill install request rejected: ${detail}`);
		}
		return {
			content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
			details: result,
		};
	},
});

const httpRequest = defineTool({
	name: "http_request",
	label: "Harness HTTP request",
	description:
		"Fetch any public web page with GET or HEAD through the Harness network broker. Requests with a body or other methods are restricted to approved business API hosts. Use search_skill_catalog for Skill discovery.",
	parameters: Type.Object({
		method: Type.String({ minLength: 1, maxLength: 16 }),
		url: Type.String({ minLength: 1, maxLength: 2048 }),
		headers: Type.Optional(Type.Record(Type.String(), Type.String())),
		body: Type.Optional(Type.String({ maxLength: 1_000_000 })),
	}),
	async execute(callId, params) {
		const gatewayUrl = (process.env[GATEWAY_URL_ENV] || "").trim();
		const token = (process.env[GATEWAY_TOKEN_ENV] || "").trim();
		if (!gatewayUrl || !token) {
			throw new Error("Harness network broker is not configured");
		}
		const response = await fetch(
			`${gatewayUrl.replace(/\/+$/, "")}/admin/harness/network/request`,
			{
				method: "POST",
				headers: {
					Accept: "application/json",
					"Content-Type": "application/json",
					Authorization: `Bearer ${token}`,
					"X-Harness-Request-ID": callId,
				},
				body: JSON.stringify({
					method: params.method,
					url: params.url,
					headers: params.headers || {},
					body: params.body,
					task_id: process.env[TASK_ID_ENV] || "",
					tenant_id: process.env[TENANT_ID_ENV] || "",
					user_id: process.env[USER_ID_ENV] || "",
				}),
				signal: AbortSignal.timeout(30_000),
			},
		);
		const result: unknown = await response.json().catch(() => ({}));
		if (!response.ok) {
			const detail = rejectDetail(result, response.status);
			const recovery = /allowlist|private|reserved|skills\.sh|GitHub|github\.com/i.test(detail)
				? "http_request 可读取公网网页，但仅限 GET/HEAD；私网地址和非读取请求仍会被拒绝。技能发现使用 search_skill_catalog；安装申请使用 request_skill_install。"
				: "";
			throw new Error(`Network request rejected: ${detail}${recovery ? ` ${recovery}` : ""}`);
		}
		// broker 成功转发不等于目标网页成功；错误页不能记为成功读取。
		if (
			result !== null &&
			typeof result === "object" &&
			"status" in result &&
			typeof result.status === "number" &&
			result.status >= 400
		) {
			throw new Error(`HTTP ${result.status}: ${JSON.stringify(result)}`);
		}
		return {
			content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
			details: result,
		};
	},
});

const browserReadPage = defineTool({
	name: "browser_read_page",
	label: "Read browser source",
	description:
		"Read original page text from a fresh native browser and return a verified source receipt. Accepts only an HTTP(S) URL and optional CSS selector matching one element; returns at most 20000 UTF-16 code units with explicit truncation. No supplied text or JavaScript. Uses the approved browser sandbox network boundary, not the HTTP broker SSRF filter.",
	parameters: Type.Object({
		url: Type.String({ minLength: 1, maxLength: 8192 }),
		selector: Type.Optional(Type.String({ minLength: 1, maxLength: 1024 })),
	}, { additionalProperties: false }),
	async execute(_callId, params, signal) {
		return readBrowserPage(params, signal);
	},
});

export default function toolGatewayProxyExtension(pi: ExtensionAPI) {
	if (process.env.PI_STRICT_BROWSER_SOURCE === "1") {
		pi.registerTool(browserReadPage);
	} else {
		pi.registerTool(searchSkillCatalog);
		pi.registerTool(requestSkillInstall);
		pi.registerTool(httpRequest);
	}
	pi.on("tool_call", async (_event: ToolCallEvent, _ctx: ToolCallCtx): Promise<ToolCallResult> => {
		if (process.env.PI_EXECUTION_PROFILE === "sandbox") return;
		const gatewayUrl = (process.env[GATEWAY_URL_ENV] || "").trim();
		if (!gatewayUrl) {
			// No gateway configured — fall through to tool_policy.ts
			return;
		}

		const toolName = String(_event.toolName || "");
		const input = _event.input || {};
		const taskId = process.env[TASK_ID_ENV] || "";
		const tenantId = process.env[TENANT_ID_ENV] || "default";
		const userId = process.env[USER_ID_ENV] || "";
		const workspace = harnessWorkspace();

		try {
			const url = `${gatewayUrl.replace(/\/+$/, "")}/admin/harness/tools/evaluate`;
			const body = JSON.stringify({
				tool_name: toolName,
				arguments: input,
				task_id: taskId,
				workspace,
				tenant_id: tenantId,
				user_id: userId,
			});

			const controller = new AbortController();
			const timer = setTimeout(() => controller.abort(), 5000);
			let resp: Response;
			try {
				resp = await fetch(url, {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
						Authorization: `Bearer ${process.env[GATEWAY_TOKEN_ENV] || ""}`,
					},
					body,
					signal: controller.signal,
				});
			} finally {
				clearTimeout(timer);
			}

			if (!resp.ok) {
				return {
					block: true,
					reason: `[Harness] 网关拒绝或不可用 (HTTP ${resp.status})`,
				};
			}

			const result = (await resp.json()) as GatewayResult;

			if (result.is_denied) {
				const reason = result.error || "策略拒绝";
				const recovery =
					toolName === "bash" && /interpreter|package|network|curl|wget|npx/i.test(reason)
						? "恢复建议：不要重复尝试该命令；请使用当前工作区的 read/write，业务 API 使用 http_request，技能发现使用 search_skill_catalog，能力型操作使用已批准 Skill。"
						: toolName === "http_request"
							? "恢复建议：http_request 只用于已批准业务 API；技能发现请使用 search_skill_catalog，不要访问 skills.sh 或 GitHub。"
						: /workspace|path|file/i.test(reason)
							? "恢复建议：改用当前会话工作区内的相对路径；不要猜测绝对路径。"
							: "恢复建议：停止重复尝试，改用 Harness 提供的固定工具或向用户说明当前能力不可用。";
				return {
					block: true,
					reason: `[Harness] ${reason} ${recovery}`,
				};
			}

			if (result.approval_required) {
				return {
					block: true,
					reason: `[Harness] 需要审批: ${result.error || "等待管理员批准"} (${result.approval_request_id || ""})`,
				};
			}

			if (result.rewritten) {
				return {
					block: true,
					reason: `[Harness] 需要改写: ${result.output || result.error}`,
				};
			}

			// ALLOW — let the tool execute normally
			return;
		} catch (err: unknown) {
			const detail = err instanceof Error ? err.message : "unknown gateway error";
			return {
				block: true,
				reason: `[Harness] 网关不可用，已拒绝工具调用: ${detail}`,
			};
		}
	});
}
