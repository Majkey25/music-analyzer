export class Transport {
  constructor(audio, changed, failed, onSeek) {
    this.audio = audio;
    this.changed = changed;
    this.failed = failed;
    this.onSeek = onSeek;
    this.position = 0;
    this.duration = 0;
    this.speed = 1;
    this.playing = false;
    this.notes = [];
    this.mode = "melody";
    this.loop = null;
    this.context = null;
    this.voices = new Set();
    this.frame = 0;
    this.timer = 0;
    this.baseTime = 0;
    this.basePosition = 0;
    this.generation = 0;
    audio.addEventListener("loadedmetadata", () => this.seekAudio());
    audio.addEventListener("ended", () => {
      if (!this.playing) return;
      if (this.loop) {
        const generation = this.generation;
        this.seek(this.loop.start);
        audio.play().catch(() => this.playbackRejected(generation));
      } else this.pause();
    });
    audio.addEventListener("error", () => {
      if (this.source) this.reportFailure(new Error("Could not play the audio."));
    });
    audio.addEventListener("timeupdate", () => {
      if (this.playing && this.loop && audio.currentTime >= this.loop.end) this.seek(this.loop.start);
    });
  }

  reportFailure(error) {
    this.pause();
    this.failed(error);
  }

  playbackRejected(generation) {
    if (this.playing && generation === this.generation) {
      this.reportFailure(new Error("Playback could not start. Try playing again."));
    }
  }

  setSource(url, duration, keepPosition = false) {
    this.pause();
    this.loop = null;
    this.duration = duration;
    this.position = keepPosition ? Math.min(this.position, duration) : 0;
    this.source = url;
    this.audio.removeAttribute("src");
    if (url) this.audio.src = url;
    this.audio.load();
    this.changed(this.position, false);
  }

  seek(position) {
    this.onSeek();
    this.position = Math.max(0, Math.min(this.duration, position));
    this.stopVoices();
    this.seekAudio();
    this.basePosition = this.position;
    this.baseTime = this.context?.currentTime ?? 0;
    this.nextNote = this.notes.findIndex(note => note.end > this.position);
    if (this.nextNote < 0) this.nextNote = this.notes.length;
    this.changed(this.position, this.playing);
  }

  seekAudio() {
    if (this.source && this.audio.readyState > 0 && Math.abs(this.audio.currentTime - this.position) > .001) {
      this.audio.currentTime = this.position;
    }
  }

  async play() {
    if (this.playing || !this.duration) return;
    this.playing = true;
    const generation = ++this.generation;
    try {
      const outsideLoop = this.loop && (this.position < this.loop.start || this.position >= this.loop.end);
      this.seek(outsideLoop ? this.loop.start : this.position >= this.duration ? 0 : this.position);
      if (this.source) {
        this.audio.playbackRate = this.speed;
        await this.audio.play();
      } else {
        if (!this.context) {
          this.context = new AudioContext();
          this.output = this.context.createDynamicsCompressor();
          this.master = this.context.createGain();
          this.master.gain.value = this.audio.volume;
          this.output.connect(this.master).connect(this.context.destination);
        }
        await this.context.resume();
        this.baseTime = this.context.currentTime;
        if (this.playing && generation === this.generation) {
          this.runSynth();
          this.timer = setInterval(() => this.runSynth(), 100);
        }
      }
      if (this.playing && generation === this.generation) this.tick();
    } catch {
      this.playbackRejected(generation);
    }
  }

  pause() {
    this.playing = false;
    this.generation++;
    this.audio.pause();
    cancelAnimationFrame(this.frame);
    clearInterval(this.timer);
    this.stopVoices();
    this.changed(this.position, false);
  }

  rate(value) {
    this.speed = value;
    this.audio.playbackRate = value;
    this.seek(this.position);
  }

  setVolume(value) {
    this.audio.volume = value;
    if (this.master) this.master.gain.value = value;
  }

  tick() {
    if (!this.playing) return;
    this.readClock();
    if (!this.playing) return;
    this.changed(this.position, true);
    this.frame = requestAnimationFrame(() => this.tick());
  }

  readClock() {
    this.position = this.source ? this.audio.currentTime
      : this.basePosition + (this.context.currentTime - this.baseTime) * this.speed;
    if (this.loop && this.position >= this.loop.end) this.seek(this.loop.start);
    if (this.position >= this.duration) {
      this.position = this.duration;
      this.pause();
    }
  }

  runSynth() {
    if (!this.playing || this.source) return;
    this.readClock();
    if (this.playing) this.schedule();
  }

  schedule() {
    const context = this.context;
    const upcoming = this.notes[this.nextNote]?.start ?? this.position;
    const lookahead = this.voices.size > 0 ? this.position + 1.25 * this.speed : upcoming + .001;
    const horizon = Math.min(Math.max(lookahead, this.position + 1.25 * this.speed), this.loop?.end ?? Infinity);
    while (this.nextNote < this.notes.length && this.notes[this.nextNote].start <= horizon
        && this.notes[this.nextNote].start < (this.loop?.end ?? Infinity)) {
      const note = this.notes[this.nextNote++];
      const when = context.currentTime + Math.max(0, note.start - this.position) / this.speed;
      const duration = (Math.min(note.end, this.loop?.end ?? Infinity) - Math.max(note.start, this.position)) / this.speed;
      if (duration <= 0) continue;
      const level = context.createGain();
      level.connect(this.output);
      const instrument = note.mode ?? this.mode;
      const hit = instrument === "drums";
      let voice;
      if (hit && note.pitch > 36) {
        voice = context.createBufferSource();
        const buffer = context.createBuffer(1, Math.ceil(context.sampleRate * .12), context.sampleRate);
        const samples = buffer.getChannelData(0);
        for (let i = 0; i < samples.length; i++) samples[i] = (Math.random() * 2 - 1) * (1 - i / samples.length);
        voice.buffer = buffer;
      } else {
        voice = context.createOscillator();
        voice.type = instrument === "violin" ? "sawtooth" : instrument === "piano" ? "sine" : "triangle";
        voice.frequency.value = hit ? 105 : 440 * 2 ** ((note.pitch - 69) / 12);
        if (hit) voice.frequency.exponentialRampToValueAtTime(40, when + .08);
      }
      const attack = Math.min(.012, duration / 2);
      level.gain.setValueAtTime(.001, when);
      level.gain.linearRampToValueAtTime((instrument === "violin" ? .075 : .14) * note.velocity / 127, when + attack);
      level.gain.exponentialRampToValueAtTime(.001, when + duration + .025);
      voice.connect(level);
      this.voices.add(voice);
      voice.onended = () => { this.voices.delete(voice); voice.disconnect(); level.disconnect(); };
      voice.start(when);
      voice.stop(when + duration + .028);
    }
  }

  stopVoices() {
    for (const voice of this.voices) voice.stop();
    this.voices.clear();
  }
}
