#!/usr/bin/env node
// 让排队输入在实际消费时带回业务 ID；相同文字的 steer/follow_up 不能靠文本匹配。
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.argv[2] || "/opt/pi-runtime/lib/node_modules/@earendil-works/pi-coding-agent");
const { version } = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
if (version !== "0.82.1") {
	throw new Error(`Pi control identity patch requires 0.82.1, got ${version}`);
}

function replaceOnce(source, before, after, path) {
	if (source.split(after).length - 1 === 1) return source;
	const count = source.split(before).length - 1;
	if (count !== 1) throw new Error(`Pi control patch anchor mismatch in ${path}: ${before}`);
	return source.replace(before, after);
}

const changes = new Map();
function patch(relative, replacements) {
	const path = resolve(root, relative);
	let source = readFileSync(path, "utf8");
	for (const [before, after] of replacements) source = replaceOnce(source, before, after, path);
	changes.set(path, source);
}

patch("dist/core/agent-session.js", [
	["async steer(text, images) {", "async steer(text, images, clientMessageId) {"],
	["await this._queueSteer(expandedText, images);", "await this._queueSteer(expandedText, images, clientMessageId);"],
	["async followUp(text, images) {", "async followUp(text, images, clientMessageId) {"],
	["await this._queueFollowUp(expandedText, images);", "await this._queueFollowUp(expandedText, images, clientMessageId);"],
	["async _queueSteer(text, images) {", "async _queueSteer(text, images, clientMessageId) {"],
	["async _queueFollowUp(text, images) {", "async _queueFollowUp(text, images, clientMessageId) {"],
	[
		"this.agent.steer({\n            role: \"user\",\n            content,\n            timestamp: Date.now(),\n        });",
		"this.agent.steer({\n            role: \"user\",\n            content,\n            timestamp: Date.now(),\n            nexusClientMessageId: clientMessageId,\n        });",
	],
	[
		"this.agent.followUp({\n            role: \"user\",\n            content,\n            timestamp: Date.now(),\n        });",
		"this.agent.followUp({\n            role: \"user\",\n            content,\n            timestamp: Date.now(),\n            nexusClientMessageId: clientMessageId,\n        });",
	],
]);
patch("dist/core/agent-session.d.ts", [
	["steer(text: string, images?: ImageContent[]): Promise<void>;", "steer(text: string, images?: ImageContent[], clientMessageId?: string): Promise<void>;"],
	["followUp(text: string, images?: ImageContent[]): Promise<void>;", "followUp(text: string, images?: ImageContent[], clientMessageId?: string): Promise<void>;"],
]);
patch("dist/modes/rpc/rpc-mode.js", [
	[
		'            case "steer": {',
		`            case "nexus_steer":
            case "nexus_follow_up": {
                const clientMessageId = command.clientMessageId;
                if (typeof clientMessageId !== "string" || !clientMessageId.trim() || clientMessageId.length > 64) {
                    return error(id, command.type, "clientMessageId is required (1-64 characters)");
                }
                if (command.type === "nexus_steer") {
                    await session.steer(command.message, command.images, clientMessageId);
                } else {
                    await session.followUp(command.message, command.images, clientMessageId);
                }
                return success(id, command.type);
            }
            case "steer": {`,
	],
]);

// 全部版本与锚点检查通过后再写入，失败时不留下半套协议。
for (const [path, source] of changes) writeFileSync(path, source);
console.log(`patched Pi ${version} queued input identity (${changes.size} files)`);
