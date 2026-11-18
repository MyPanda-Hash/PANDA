import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";

const TEXT_LIMIT = 20_000;
// 与 chat_browser_delivery.py 一致，预算包含 base64、外层 JSON 和 64 位 HMAC。
const RECEIPT_LIMIT = 80_000;
const RECEIPT_OVERHEAD = JSON.stringify({ kind: "browser_delivery_v1", data: "", proof: "0".repeat(64) }).length;
const SOURCE_BYTE_LIMIT = Math.floor((RECEIPT_LIMIT - RECEIPT_OVERHEAD) / 4) * 3;
const CLI = "/usr/local/bin/agent-browser";

type SourceReceipt = {
	kind: "browser_source_v1";
	url: string;
	title: string;
	text: string;
	truncated: boolean;
};

function httpUrl(value: unknown): string {
	if (typeof value !== "string" || value.length > 8192 || /[\u0000-\u0020\u007f]/u.test(value)) {
		throw new Error("browser_read_page: invalid URL");
	}
	const url = new URL(value);
	if (!/^https?:$/.test(url.protocol) || url.username || url.password || !url.hostname) {
		throw new Error("browser_read_page: only credential-free HTTP(S) URLs are supported");
	}
	return url.href;
}

function object(value: unknown): Record<string, unknown> {
	if (!value || typeof value !== "object" || Array.isArray(value)) {
		throw new Error("browser_read_page: unsupported browser response");
	}
	return value as Record<string, unknown>;
}

export async function readBrowserPage(
	params: { url: string; selector?: string },
	signal?: AbortSignal,
) {
	if (process.env.PI_STRICT_BROWSER_SOURCE !== "1" || process.env.PI_UNATTENDED_RESTRICTED === "1") {
		throw new Error("browser_read_page: available only for strict browser turns");
	}
	const url = httpUrl(params.url);
	const selector = params.selector ?? "body";
	if (typeof selector !== "string" || !selector.trim() || selector.length > 1024 || selector.includes("\0")) {
		throw new Error("browser_read_page: invalid CSS selector");
	}
	signal?.throwIfAborted();
	const root = await mkdtemp(path.join(tmpdir(), "nexus-browser-"));
	const session = `source-${randomUUID()}`;
	const config = path.join(root, "config.json");
	// 浏览器不继承模型密钥、业务 token、代理凭据或用户配置。
	const env: NodeJS.ProcessEnv = {
		PATH: "/usr/local/bin:/usr/bin:/bin",
		LANG: "C.UTF-8",
		LC_ALL: "C.UTF-8",
		TZ: "Asia/Shanghai",
		TMPDIR: root,
		HOME: root,
		XDG_CONFIG_HOME: root,
		XDG_CACHE_HOME: root,
		XDG_RUNTIME_DIR: root,
		AGENT_BROWSER_EXECUTABLE_PATH: "/usr/bin/chromium",
		AGENT_BROWSER_DEFAULT_TIMEOUT: "20000",
	};
	const deadline = Date.now() + 60_000;
	let started = false;
	let closed = false;
	const command = async (args: string[], closing = false): Promise<Record<string, unknown>> => {
		if (!closing) signal?.throwIfAborted();
		const timeout = closing ? 10_000 : Math.min(30_000, deadline - Date.now());
		if (timeout <= 0) throw new Error("browser_read_page: timed out");
		const stdout = await new Promise<string>((resolve, reject) => {
			execFile(CLI, ["--config", config, "--session", session, "--json", "--no-webmcp", "--idle-timeout", "30s", ...args], {
				cwd: root, env, encoding: "utf8", shell: false,
				timeout, maxBuffer: 1_000_000, killSignal: "SIGKILL",
				signal: closing ? undefined : signal,
			}, (error, stdout) => {
				if (error) reject(new Error(`browser_read_page: browser command failed (${error.code ?? error.name})`));
				else resolve(stdout);
			});
		});
		const response = object(JSON.parse(stdout));
		if (response.success !== true) throw new Error("browser_read_page: browser command rejected");
		return object(response.data);
	};
	try {
		await writeFile(config, "{}", { mode: 0o600 });
		started = true;
		await command(["open"]);
		// 0.38.2 的 open 不返回 HTTP 状态；导航前启用原生 CDP 请求记录。
		await command(["network", "requests"]);
		await command(["open", url]);
		// 固定提取程序；selector 仅按 JSON 字符串编码，不作为代码执行。
		const extracted = await command(["eval", `(() => {
			const nodes = document.querySelectorAll(${JSON.stringify(selector)});
			if (nodes.length !== 1) throw new Error("Expected exactly one matching element");
			const full = nodes[0].innerText;
			if (typeof full !== "string" || !full.trim()) throw new Error("Empty or unsupported page text");
			let end = Math.min(full.length, ${TEXT_LIMIT});
			if (end < full.length && /[\\uD800-\\uDBFF]/.test(full.charAt(end - 1))) end--;
			return { url: location.href, title: document.title, text: full.slice(0, end), truncated: end < full.length };
		})()`]);
		const page = object(extracted.result);
		const actualUrl = httpUrl(page.url);
		if (httpUrl(extracted.origin) !== actualUrl || typeof page.title !== "string" || page.title.length > 2048 ||
			typeof page.text !== "string" || !page.text.trim() || page.text.length > TEXT_LIMIT || typeof page.truncated !== "boolean") {
			throw new Error("browser_read_page: invalid page extraction");
		}
		const network = await command(["network", "requests"]);
		if (!Array.isArray(network.requests)) throw new Error("browser_read_page: missing navigation status");
		const withoutHash = (value: string) => { const parsed = new URL(value); parsed.hash = ""; return parsed.href; };
		const documents = network.requests.map(object).filter((request) =>
			request.resourceType === "Document" && typeof request.url === "string" &&
			withoutHash(request.url) === withoutHash(actualUrl));
		// 多次同址导航或无法绑定的状态不能冒充当前页面的成功读取。
		if (documents.length !== 1 || !Number.isInteger(documents[0].status) ||
			Number(documents[0].status) < 200 || Number(documents[0].status) >= 300) {
			throw new Error("browser_read_page: unsuccessful or unverifiable HTTP navigation");
		}
		signal?.throwIfAborted();
		const details: SourceReceipt = { kind: "browser_source_v1", url: actualUrl, title: page.title, text: page.text, truncated: page.truncated };
		if (Buffer.byteLength(JSON.stringify(details), "utf8") > SOURCE_BYTE_LIMIT) {
			details.truncated = true;
			const text = details.text;
			let low = 0;
			let high = text.length;
			while (low < high) {
				const middle = Math.floor((low + high + 1) / 2);
				details.text = text.slice(0, middle);
				if (Buffer.byteLength(JSON.stringify(details), "utf8") <= SOURCE_BYTE_LIMIT) low = middle;
				else high = middle - 1;
			}
			// 二分预算后仍须保留完整代理对，不能交付半个 Unicode 字符。
			if (low < text.length && /[\uD800-\uDBFF]/u.test(text.charAt(low - 1))) low--;
			details.text = text.slice(0, low);
		}
		return { content: [{ type: "text" as const, text: JSON.stringify(details) }], details };
	} finally {
		try {
			if (started) {
				const result = await command(["close"], true);
				if (result.closed !== true) throw new Error("browser_read_page: browser did not confirm close");
			}
			closed = true;
		} finally {
			// 关闭失败时保留 socket 供原生空闲超时回收，且不返回成功 receipt。
			if (closed) await rm(root, { recursive: true, force: true });
		}
	}
}
