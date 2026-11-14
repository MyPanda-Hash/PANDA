/* 麦克风取样：把 AudioContext 的浮点帧原样送到主线程，
   重采样与打包在主线程做，worklet 里尽量少做事避免爆音。 */
class PcmTap extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      this.port.postMessage(input[0].slice(0));
    }
    return true;
  }
}

registerProcessor("pcm-tap", PcmTap);