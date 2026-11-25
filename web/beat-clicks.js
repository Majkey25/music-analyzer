function readyForCount(audio, signal) {
  if (!audio || (audio.readyState >= 3 && !audio.seeking)) return;
  if (audio.error) throw Error("Could not prepare audio for the count-in.");
  const listeners = new AbortController();
  const options = { signal: listeners.signal };
  return new Promise((resolve, reject) => {
    const finish = error => {
      clearTimeout(timer);
      listeners.abort();
      if (error) reject(error);
      else resolve();
    };
    const ready = () => { if (audio.readyState >= 3 && !audio.seeking) finish(); };
    const failed = () => finish(Error("Could not prepare audio for the count-in."));
    const timer = setTimeout(failed, 8000);
    audio.addEventListener("canplay", ready, options);
    audio.addEventListener("seeked", ready, options);
    audio.addEventListener("error", failed, options);
    signal.addEventListener("abort", () => finish(), options);
  });
}

export class BeatClicks {
  constructor(status, volume, onCountChange) {
    this.status = status;
    this.volume = volume;
    this.onCountChange = onCountChange;
    this.tones = new Set();
    this.lastBeat = -Infinity;
    this.counting = false;
  }

  async prepare() {
    if (!this.context) {
      this.context = new AudioContext();
      this.level = this.context.createGain();
      this.level.gain.value = this.volume;
      this.level.connect(this.context.destination);
    }
    await this.context.resume();
  }

  setVolume(value) {
    this.volume = value;
    if (this.level) this.level.gain.value = value;
  }

  sound(when, accent) {
    const tone = this.context.createOscillator();
    const gain = this.context.createGain();
    tone.frequency.value = accent ? 1340 : 935;
    gain.gain.setValueAtTime(accent ? .17 : .11, when);
    gain.gain.exponentialRampToValueAtTime(.001, when + .042);
    tone.connect(gain).connect(this.level);
    this.tones.add(tone);
    tone.onended = () => { this.tones.delete(tone); tone.disconnect(); gain.disconnect(); };
    tone.start(when);
    tone.stop(when + .045);
  }

  cancel() {
    this.pending?.abort();
    this.pending = null;
    this.setCounting(false);
    clearTimeout(this.timer);
    this.reset();
  }

  reset() {
    this.lastBeat = -Infinity;
    for (const tone of this.tones) if (tone.context.state !== "closed") tone.stop();
    this.tones.clear();
  }

  setCounting(counting) {
    if (counting === this.counting) return;
    this.counting = counting;
    this.status.hidden = !counting;
    this.onCountChange();
  }

  async countIn({ beats, position, interval, count, speed, audio }, startPlayback) {
    this.cancel();
    const { signal } = this.pending = new AbortController();
    this.setCounting(true);
    try {
      await Promise.all([this.prepare(), readyForCount(audio, signal)]);
      if (signal.aborted) return;
      // Start the audio early enough for its next beat to land on the count-in grid.
      const next = beats.find(beat => beat.time >= position - .001);
      const pickup = next ? Math.max(0, Math.min(interval, next.time - position)) : 0;
      const duration = (count * interval - pickup) / speed;
      const start = this.context.currentTime + .04;
      for (let i = 0; i < count; i++) this.sound(start + i * interval / speed, i === 0);
      const launch = () => {
        const remaining = start + duration - this.context.currentTime;
        if (remaining > .002) {
          this.timer = setTimeout(launch, remaining * 1000);
          return;
        }
        this.pending = null;
        this.setCounting(false);
        startPlayback();
      };
      this.timer = setTimeout(launch, (duration + .04) * 1000);
    } catch (error) {
      if (signal.aborted) return;
      this.cancel();
      throw error;
    }
  }

  follow(beats, position, speed, end = Infinity) {
    if (this.context?.state !== "running") return;
    const earliest = Math.max(this.lastBeat + .00001, position - .05 * speed);
    let low = 0, high = beats.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (beats[mid].time < earliest) low = mid + 1;
      else high = mid;
    }
    for (let index = low; index < beats.length && beats[index].time < end
        && beats[index].time <= position + .07 * speed; index++) {
      const { time, beat } = beats[index];
      this.sound(this.context.currentTime + Math.max(0, time - position) / speed, beat === 1);
      this.lastBeat = time;
    }
  }
}
