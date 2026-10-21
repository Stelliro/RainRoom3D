// Phone audio: decodes the server's binary frames (PROTOCOL.md section 4) and
// schedules them gaplessly with Web Audio. Plain-http LAN page = not a secure
// context, so this sticks to AudioBufferSourceNode scheduling (no worklets).
//
// Timeline: blocks are queued until `startLead` seconds are buffered, then
// anchored at ctx.currentTime + a small margin and scheduled back to back.
// If the queue runs dry (a late block), the gain fades out at the end of the
// last block, and playback re-anchors with a short fade-in once enough is
// buffered again (no clicks). `played` = stream frames whose playback time has
// passed; it never goes down, and is what the ack reports.

const MAGIC = 0x52524131;
const HEADER = 12;
const SAFETY = 0.06;      // first block starts this far after "now"
const FADE = 0.012;       // fade length (s)

export class RainPlayer {
  constructor({ onAck, onState } = {}) {
    this.onAck = onAck || (() => {});
    this.onState = onState || (() => {});
    this.ctx = null;
    this.gain = null;
    this.analyser = null;
    this.rate = 48000;
    this.frames = 2048;
    this.startLead = 0.30;
    this.maxLead = 0.6;
    this.streamId = null;
    this.state = "idle";          // idle | waiting | buffering | playing
    this._timer = null;
    this._resetCounters();
    this.stats = {
      underruns: 0, lateFrames: 0, reanchors: 0, seqGaps: 0, badFrames: 0,
      framesReceived: 0, blocksScheduled: 0, scheduleGaps: 0, acksSent: 0,
      bufferedMs: 0, minBufferedMs: null, maxBufferedMs: 0, lastAck: 0,
      streams: 0, contextRate: 0, baseLatencyMs: 0, state: "idle", streamId: null,
      startedAt: 0, playingMs: 0,
    };
    this._tdata = null;
  }

  _resetCounters() {
    this.pending = [];             // AudioBuffers waiting for an anchor
    this.scheduled = [];           // {start, end, frames, src}
    this.nextTime = 0;             // when the next block starts (ctx time)
    this.anchored = false;
    this.expectSeq = 0;
    this.playedDone = 0;           // frames of finished blocks
    this.lastPlayed = 0;
    this.lastAckAt = 0;
    this.fadeOutAt = null;
  }

  /** Create / resume the AudioContext. Call from a user gesture (iOS). */
  async unlock() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error("This browser can't play web audio");
    if (!this.ctx) {
      try {
        this.ctx = new AC({ sampleRate: 48000, latencyHint: "playback" });
      } catch (e) {
        this.ctx = new AC();
      }
      try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (e) {}
      this.gain = this.ctx.createGain();
      this.gain.gain.value = 0;
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.smoothingTimeConstant = 0.6;
      this.gain.connect(this.analyser);
      this.analyser.connect(this.ctx.destination);
      this.stats.contextRate = this.ctx.sampleRate;
      this.stats.baseLatencyMs = Math.round((this.ctx.baseLatency || 0) * 1000);
      // a silent blip inside the gesture fully unlocks iOS
      const b = this.ctx.createBuffer(1, 1, this.ctx.sampleRate);
      const s = this.ctx.createBufferSource();
      s.buffer = b; s.connect(this.ctx.destination); s.start(0);
    }
    if (this.ctx.state !== "running") {
      try { await this.ctx.resume(); } catch (e) {}
    }
    return this.ctx.state;
  }

  get running() { return !!this.ctx && this.ctx.state === "running"; }

  /** We asked for a phone stream; frames will follow a "stream" message. */
  expect() {
    this.stop(false);
    this._setState("waiting");
  }

  /** "stream" start message from the server. */
  beginStream(msg) {
    this._clearSources();
    this._resetCounters();
    this.streamId = msg.stream;
    this.rate = msg.sample_rate || 48000;
    this.frames = msg.frames || 2048;
    const lead = Math.max(250, Number(msg.start_lead_ms) || 250);
    this.maxLead = (Number(msg.max_lead_ms) || 600) / 1000;
    // ~300 ms jitter buffer, but never more than the server will send ahead
    this.startLead = Math.min(Math.max(lead, 300), this.maxLead * 1000 - 60) / 1000;
    this.stats.streams++;
    this.stats.streamId = this.streamId;
    this.stats.startedAt = performance.now();
    this._setState("buffering");
    this._startTimer();
    this._ack(true);
  }

  /** One binary message. */
  push(buf) {
    if (!this.ctx || this.streamId === null || this.state === "idle") return;
    if (!(buf instanceof ArrayBuffer) || buf.byteLength < HEADER + 4) { this.stats.badFrames++; return; }
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== MAGIC) { this.stats.badFrames++; return; }
    const seq = dv.getUint32(4, true);
    const n = Math.floor((buf.byteLength - HEADER) / 4);
    if (seq !== (this.expectSeq >>> 0)) {
      // can't happen on one TCP stream; treat it as a restart of the timeline
      this.stats.seqGaps++;
      this._clearSources();
      const done = this._playedNow();
      this.pending = []; this.scheduled = []; this.anchored = false;
      this.playedDone = done;
      this._setState("buffering");
    }
    this.expectSeq = (seq + 1) >>> 0;
    this.stats.framesReceived++;
    const pcm = new Int16Array(buf, HEADER, n * 2);
    const ab = this.ctx.createBuffer(2, n, this.rate);
    const L = ab.getChannelData(0), R = ab.getChannelData(1);
    for (let i = 0, j = 0; i < n; i++, j += 2) {
      L[i] = pcm[j] / 32768;
      R[i] = pcm[j + 1] / 32768;
    }
    const now = this.ctx.currentTime;
    if (this.anchored) {
      if (this.nextTime < now + 0.005) {
        // the queue ran dry before this block came: re-anchor
        this.stats.lateFrames++;
        this.stats.underruns++;
        this._settle(now);
        this.anchored = false;
        this.pending = [ab];
        this._setState("buffering");
      } else {
        this._schedule(ab);
      }
    } else {
      this.pending.push(ab);
    }
    if (!this.anchored) {
      const have = this.pending.reduce((s, b) => s + b.duration, 0);
      // after an underrun refill a little less than the first start
      const need = this.stats.underruns ? Math.min(this.startLead, 0.2) : this.startLead;
      if (have >= need) this._anchor();
    }
  }

  _anchor() {
    const now = this.ctx.currentTime;
    const t = now + SAFETY;
    this.nextTime = t;
    this.anchored = true;
    if (this.stats.blocksScheduled > 0) this.stats.reanchors++;
    // fade in from silence
    const g = this.gain.gain;
    g.cancelScheduledValues(now);
    g.setValueAtTime(0, now);
    g.setValueAtTime(0, t);
    g.linearRampToValueAtTime(1, t + FADE * 2);
    this.fadeOutAt = null;
    const blocks = this.pending; this.pending = [];
    for (const b of blocks) this._schedule(b);
    this._setState("playing");
  }

  _schedule(ab) {
    const src = this.ctx.createBufferSource();
    src.buffer = ab;
    src.connect(this.gain);
    const start = this.nextTime;
    const prev = this.scheduled[this.scheduled.length - 1];
    if (prev && Math.abs(prev.end - start) > 1e-6) this.stats.scheduleGaps++;
    src.start(start);
    const item = { start, end: start + ab.duration, frames: ab.length, src };
    src.onended = () => this._onEnded(item);
    this.scheduled.push(item);
    this.nextTime = item.end;
    this.stats.blocksScheduled++;
    // more audio arrived: cancel a pending fade-out
    if (this.fadeOutAt !== null) {
      const g = this.gain.gain, now = this.ctx.currentTime;
      g.cancelScheduledValues(now);
      g.setValueAtTime(g.value, now);
      g.linearRampToValueAtTime(1, now + FADE);
      this.fadeOutAt = null;
    }
  }

  _onEnded(item) {
    const i = this.scheduled.indexOf(item);
    if (i >= 0) {
      this.scheduled.splice(i, 1);
      this.playedDone += item.frames;
    }
    // audio-driven acks keep the stream flowing even when timers are throttled
    this._ack(false);
  }

  /** Frames of this stream that have been played by now. */
  _playedNow() {
    if (!this.ctx) return this.playedDone;
    const now = this.ctx.currentTime;
    let p = this.playedDone;
    for (const it of this.scheduled) {
      if (it.end <= now) p += it.frames;
      else if (it.start < now) p += Math.floor((now - it.start) * this.rate);
    }
    if (p < this.lastPlayed) p = this.lastPlayed;
    this.lastPlayed = p;
    return p;
  }

  bufferedSeconds() {
    if (!this.ctx) return 0;
    const now = this.ctx.currentTime;
    const ahead = this.anchored ? Math.max(0, this.nextTime - now) : 0;
    return ahead + this.pending.reduce((s, b) => s + b.duration, 0);
  }

  _ack(force) {
    if (this.streamId === null || this.state === "idle" || this.state === "waiting") return;
    const t = performance.now();
    if (!force && t - this.lastAckAt < 150) return;
    this.lastAckAt = t;
    const played = this._playedNow();
    this.stats.acksSent++;
    this.stats.lastAck = played;
    this.onAck({ t: "ack", played, stream: this.streamId });
  }

  _settle(now) {
    // everything scheduled has played; count it
    for (const it of this.scheduled) this.playedDone += it.frames;
    this.scheduled = [];
    this.lastPlayed = Math.max(this.lastPlayed, this.playedDone);
  }

  _tick() {
    if (!this.ctx || this.state === "idle") return;
    const now = this.ctx.currentTime;
    const buf = this.bufferedSeconds();
    if (this.state === "playing") {
      const ms = buf * 1000;
      this.stats.bufferedMs = Math.round(ms);
      if (performance.now() - this.stats.startedAt > 1500) {
        this.stats.minBufferedMs = this.stats.minBufferedMs === null ? Math.round(ms) : Math.min(this.stats.minBufferedMs, Math.round(ms));
      }
      this.stats.maxBufferedMs = Math.max(this.stats.maxBufferedMs, Math.round(ms));
      // about to run dry: fade out at the end of the last block (no click)
      if (this.anchored && this.fadeOutAt === null && this.nextTime - now < 0.05 && this.nextTime > now + FADE) {
        const g = this.gain.gain;
        g.cancelScheduledValues(now);
        g.setValueAtTime(g.value, now);
        g.setValueAtTime(1, this.nextTime - FADE);
        g.linearRampToValueAtTime(0, this.nextTime);
        this.fadeOutAt = this.nextTime;
      }
      if (this.anchored && this.nextTime < now - 0.02 && this.state === "playing") {
        // ran dry and nothing came yet
        this.stats.underruns++;
        this._settle(now);
        this.anchored = false;
        this._setState("buffering");
      }
      this.stats.playingMs = Math.round(performance.now() - this.stats.startedAt);
    } else {
      this.stats.bufferedMs = Math.round(buf * 1000);
    }
    this._ack(false);
  }

  _startTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), 50);
  }

  _clearSources() {
    for (const it of this.scheduled) {
      try { it.src.onended = null; it.src.stop(); } catch (e) {}
      try { it.src.disconnect(); } catch (e) {}
    }
    this.scheduled = [];
  }

  /** Stop local playback (the caller tells the server). */
  stop(fade = true) {
    if (this.ctx && this.gain && fade) {
      const g = this.gain.gain, now = this.ctx.currentTime;
      g.cancelScheduledValues(now);
      g.setValueAtTime(g.value, now);
      g.linearRampToValueAtTime(0, now + 0.08);
      const old = this.scheduled;
      this.scheduled = [];
      setTimeout(() => { for (const it of old) { try { it.src.onended = null; it.src.stop(); it.src.disconnect(); } catch (e) {} } }, 120);
    } else {
      this._clearSources();
    }
    this._resetCounters();
    this.streamId = null;
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this._setState("idle");
  }

  /** RMS level (dBFS) of what is playing now, or null. */
  level() {
    if (!this.analyser || this.state !== "playing") return null;
    if (!this._tdata) this._tdata = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(this._tdata);
    let s = 0;
    for (let i = 0; i < this._tdata.length; i++) s += this._tdata[i] * this._tdata[i];
    const rms = Math.sqrt(s / this._tdata.length);
    return rms > 1e-6 ? 20 * Math.log10(rms) : -120;
  }

  _setState(s) {
    if (this.state === s) return;
    this.state = s;
    this.stats.state = s;
    this.onState(s);
  }
}
