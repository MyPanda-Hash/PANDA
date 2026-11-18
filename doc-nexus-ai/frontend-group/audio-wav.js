// 录音编码：把 WebAudio 采到的浮点 PCM 编成 16-bit 单声道 WAV。
//
// 为什么不用 MediaRecorder：Chrome 默认产出 audio/webm，而百炼 ASR 的
// format 只支持 wav / mp3 / opus 等，webm 不在列表里，上传会被拒。
// WAV 是全网 ASR 都吃的格式。

/** 线性插值重采样，够用且无依赖。 */
export function resampleTo(samples, fromRate, toRate) {
  if (!Number.isFinite(fromRate) || fromRate <= 0) return samples;
  if (!Number.isFinite(toRate) || toRate <= 0) return samples;
  if (fromRate === toRate) return samples;
  const ratio = fromRate / toRate;
  const length = Math.max(1, Math.floor(samples.length / ratio));
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const position = i * ratio;
    const index = Math.floor(position);
    const fraction = position - index;
    const a = samples[index] ?? 0;
    const b = samples[index + 1] ?? a;
    out[i] = a + (b - a) * fraction;
  }
  return out;
}

/** 多段 PCM 拼成一段。 */
export function concatChunks(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

function writeAscii(view, offset, text) {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
}

/** Float32 [-1,1] → 16-bit PCM WAV（单声道）。 */
export function encodeWav(samples, sampleRate) {
  const frames = samples.length;
  const buffer = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buffer);

  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + frames * 2, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);        // fmt 块长度
  view.setUint16(20, 1, true);         // PCM
  view.setUint16(22, 1, true);         // 单声道
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // 字节率 = 采样率 × 声道 × 位深/8
  view.setUint16(32, 2, true);         // 块对齐
  view.setUint16(34, 16, true);        // 位深
  writeAscii(view, 36, 'data');
  view.setUint32(40, frames * 2, true);

  let offset = 44;
  for (let i = 0; i < frames; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: 'audio/wav' });
}
