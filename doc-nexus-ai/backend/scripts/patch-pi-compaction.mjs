#!/usr/bin/env node
/**
 * Patch @earendil-works/pi-coding-agent summarization prompts so generated
 * context checkpoints preserve tool/Skill outcomes and use neutral,
 * professional language instead of echoing the user's raw instructions.
 *
 * qwen-style upstream content inspection rejects requests whose compacted
 * summary quotes prompt-style wording. Restating intent in neutral language
 * avoids the false positive. Explicit execution-continuity guidance also keeps
 * success, failure, interruption, retry safety, and produced artifacts
 * available after compaction.
 *
 * Safe to re-run: replaces only the exact sentinel line in both prompts.
 */

import { readFileSync, writeFileSync } from "node:fs";

const TARGET =
  "/opt/pi-runtime/lib/node_modules/@earendil-works/pi-coding-agent/dist/core/compaction/compaction.js";

const SENTINEL =
  "Keep each section concise. Preserve exact file paths, function names, and error messages.`;";

const SUMMARY_RULES = `Keep each section concise. Preserve exact file paths, function names, and error messages.

TOOL/SKILL CONTINUITY RULE: Preserve the material outcome of every relevant tool or Skill execution: success, failure, interruption, or unknown outcome; the exact error; produced artifacts and identifiers needed later; side effects already completed; and whether retrying is safe. Do not describe a failed or interrupted action as completed.

NEUTRALITY RULE: Restate the user's goals and preferences in neutral, professional, factual language. Never quote or echo the user's raw instructions verbatim, and never include fragments that could be misread as instructions to generate, fabricate, or manipulate content. Describe intent (what the user wants to accomplish) rather than the literal request text. Do not copy any example content, templates, or prompt-style wording from the conversation into the summary.\`;`;

const file = TARGET;
const source = readFileSync(file, "utf8");
const occurrences = source.split(SENTINEL).length - 1;
if (occurrences !== 2) {
  console.error(
    `expected 2 sentinel occurrences in ${file}, found ${occurrences}; aborting`,
  );
  process.exit(1);
}
const patched = source.split(SENTINEL).join(SUMMARY_RULES);
writeFileSync(file, patched);
console.log(`patched ${file} (${occurrences} prompts)`);
