import { spawn } from "node:child_process";

const executable = process.argv[2];
if (!executable) {
	throw new Error("usage: check-pi-auto-compaction.mjs <pi-executable>");
}

const child = spawn(executable, ["--mode", "rpc"], {
	stdio: ["pipe", "pipe", "pipe"],
});
let output = "";
let settled = false;
const checks = [
	{ id: "auto-compaction-smoke", type: "set_auto_compaction", enabled: true },
	{ id: "steer-identity-smoke", type: "nexus_steer", message: "构建检查", clientMessageId: "build-steer" },
	{ id: "followup-identity-smoke", type: "nexus_follow_up", message: "构建检查", clientMessageId: "build-followup" },
];
const pending = new Set(checks.map((command) => command.id));

const verify = new Promise((resolve, reject) => {
	const fail = (reason) => {
		if (settled) return;
		settled = true;
		child.kill();
		reject(new Error(`Pi RPC capability check failed: ${reason}`));
	};
	// 构建期容纳跨架构冷启动；仍必须收到成功 RPC，不跳过能力校验。
	const timeout = setTimeout(() => fail("timed out"), 60_000);
	child.on("error", (error) => fail(error.message));
	child.on("exit", (code) => {
		if (!settled) fail(`Pi exited with code ${code}`);
	});
	child.stdout.setEncoding("utf8");
	child.stdout.on("data", (chunk) => {
		output += chunk;
		for (;;) {
			const newline = output.indexOf("\n");
			if (newline < 0) return;
			const line = output.slice(0, newline);
			output = output.slice(newline + 1);
			const response = JSON.parse(line);
			if (!pending.has(response.id)) continue;
			if (response.success !== true) {
				fail(response.error || "command was rejected");
				return;
			}
			pending.delete(response.id);
			if (!settled && pending.size === 0) {
				settled = true;
				clearTimeout(timeout);
				child.kill();
				resolve();
			}
		}
	});
	child.stdin.write(`${checks.map((command) => JSON.stringify(command)).join("\n")}\n`);
});

await verify;
