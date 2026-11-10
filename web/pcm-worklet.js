"use strict";
class BankingPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Int16Array(2400);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const value of channel) {
      this.samples[this.offset++] = Math.round(Math.max(-1, Math.min(1, value)) * 32767);
      if (this.offset === this.samples.length) {
        this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
        this.samples = new Int16Array(2400);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor("banking-pcm", BankingPCM);
