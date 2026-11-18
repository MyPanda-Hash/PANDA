// WAV 编码器自检：node frontend-group/tests/audio-wav.test.mjs
//
// 不引入测试框架，任何断言失败直接非零退出，方便在 CI 或本地一键跑。
import assert from 'node:assert/strict';

import { concatChunks, encodeWav, resampleTo } from '../audio-wav.js';

const blob = encodeWav(new Float32Array([0, 0.5, -0.5, 1, -1]), 16000);
const buffer = Buffer.from(await blob.arrayBuffer());

assert.equal(blob.type, 'audio/wav', 'MIME 必须是 audio/wav');

// RIFF 头
assert.equal(buffer.subarray(0, 4).toString('ascii'), 'RIFF');
assert.equal(buffer.subarray(8, 12).toString('ascii'), 'WAVE');
assert.equal(buffer.subarray(12, 16).toString('ascii'), 'fmt ');
assert.equal(buffer.subarray(36, 40).toString('ascii'), 'data');

const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
assert.equal(view.getUint32(4, true), buffer.length - 8, 'RIFF 长度应等于总长减 8');
assert.equal(view.getUint16(20, true), 1, 'PCM 格式');
assert.equal(view.getUint16(22, true), 1, '单声道');
assert.equal(view.getUint32(24, true), 16000, '采样率');
assert.equal(view.getUint32(28, true), 32000, '字节率 = 采样率 × 2');
assert.equal(view.getUint16(34, true), 16, '位深 16');
assert.equal(view.getUint32(40, true), 10, 'data 长度 = 5 帧 × 2 字节');

// 采样值：0 → 0，0.5 → 16383，-0.5 → -16384，1 → 32767，-1 → -32768
assert.equal(view.getInt16(44, true), 0);
assert.equal(view.getInt16(46, true), 16383);
assert.equal(view.getInt16(48, true), -16384);
assert.equal(view.getInt16(50, true), 32767);
assert.equal(view.getInt16(52, true), -32768);

// 重采样：48k → 16k，长度变三分之一
const source = new Float32Array(4800);
for (let i = 0; i < source.length; i += 1) source[i] = Math.sin(i / 10);
const down = resampleTo(source, 48000, 16000);
assert.equal(down.length, 1600, '48k→16k 长度应缩到 1/3');

// 同采样率原样返回
assert.equal(resampleTo(source, 16000, 16000), source);

// 拼接总长正确
const joined = concatChunks([new Float32Array([1, 2]), new Float32Array([3])]);
assert.deepEqual(Array.from(joined), [1, 2, 3]);

// 空录音也能出合法头（避免 44 字节都没有的坏文件）
const empty = Buffer.from(await encodeWav(new Float32Array([]), 16000).arrayBuffer());
assert.equal(empty.length, 44);
assert.equal(new DataView(empty.buffer, empty.byteOffset).getUint32(40, true), 0);

console.log('audio-wav: all assertions passed');
