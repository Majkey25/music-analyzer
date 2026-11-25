const pitchNames = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];
const namedDrums = new Map([
  [35, "Kick"], [36, "Kick"], [37, "Rim"], [38, "Snare"], [40, "Snare"],
  [41, "Floor tom"], [42, "Hi-hat"], [43, "Floor tom"], [44, "Pedal hi-hat"],
  [45, "Low tom"], [46, "Open hi-hat"], [47, "Tom"],
  [48, "Tom"], [49, "Crash"], [50, "High tom"], [51, "Ride"],
]);
export const drumName = pitch => namedDrums.get(pitch) ?? `Hit ${pitch}`;
export const pitchName = pitch => pitchNames[pitch % 12] + (Math.floor(pitch / 12) - 1);
export const clock = seconds => {
  const centiseconds = Math.max(0, Math.round(seconds * 100));
  return `${Math.floor(centiseconds / 6000)}:${(centiseconds % 6000 / 100).toFixed(2).padStart(5, "0")}`;
};

export function parseTuning(value) {
  const names = value.trim().split(/[\s,]+/);
  if (!value.trim() || names.length > 16) {
    throw Error("Enter tuning for 1 to 16 strings, for example F0 C1 F1 Bb1.");
  }
  return names.map(name => {
    const match = /^([A-Ga-g])([#♯b♭]?)(-?\d{1,2})$/u.exec(name);
    if (!match) throw Error("Each string needs a note and octave, for example F#2 or Bb1.");
    const [, step, accidental, octave] = match;
    const alteration = accidental ? ("#♯".includes(accidental) ? 1 : -1) : 0;
    const midi = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[step.toUpperCase()]
      + 12 * (Number(octave) + 1) + alteration;
    if (midi < 0 || midi > 127) throw Error("Tuning must be between C−1 and G9.");
    return midi;
  });
}

export function beatGrid(tempo, meter, phase, duration) {
  let quarter = 0;
  const sections = tempo.map((section, index) => {
    const previous = tempo[index - 1];
    if (previous) quarter += (section.time - previous.time) * previous.bpm / 60;
    return { ...section, quarter };
  });
  function findSection(value, key) {
    let left = 0, right = sections.length;
    while (right - left > 1) {
      const middle = (left + right) >>> 1;
      if (sections[middle][key] <= value) left = middle;
      else right = middle;
    }
    return sections[left];
  }
  const quarterAt = time => {
    const t = time - phase, section = findSection(t, "time");
    return section.quarter + (t - section.time) * section.bpm / 60;
  };
  const timeAt = quarter => {
    const section = findSection(quarter, "quarter");
    return phase + section.time + (quarter - section.quarter) * 60 / section.bpm;
  };
  const origins = meter.map(row => ({ ...row, quarter: quarterAt(row.time + phase) }));
  const pages = [];
  let barNumber = 1, ticks = [], start = Math.min(phase, duration), lastBar = 0;
  if (start > .001) pages.push({ start: 0, end: start, ticks: [], first: 0, last: 0 });
  const flush = end => {
    if (end > start + .001) pages.push({ start, end, ticks, first: ticks.find(t => t.beat === 1)?.bar ?? 1, last: lastBar || 1 });
    ticks = [];
    start = end;
  };
  for (const [i, signature] of origins.entries()) {
    const nextMeter = origins[i + 1]?.quarter ?? Infinity;
    const end = quarterAt(duration);
    const inSection = q => q < end + .0001 && q < nextMeter - .0001;
    const step = 4 / signature.denominator, measure = step * signature.numerator;
    for (let q = signature.quarter; inSection(q); q += measure) {
      const time = timeAt(q);
      if (time > duration || barNumber > 12000) break;
      if (barNumber > 1 && ((barNumber - 1) % 4 === 0 || q === signature.quarter)) flush(time);
      lastBar = barNumber;
      for (let beat = 0; beat < signature.numerator && inSection(q + beat * step); beat += 1) {
        const position = timeAt(q + beat * step);
        if (position >= 0) ticks.push({ time: position, bar: barNumber, beat: beat + 1 });
      }
      barNumber += 1;
    }
  }
  flush(Math.max(duration, .1));
  return pages;
}

export function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function paintWave(canvas, peaks) {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const dpi = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpi);
  canvas.height = Math.round(height * dpi);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpi, dpi);
  const colors = getComputedStyle(canvas);
  ctx.fillStyle = colors.backgroundColor;
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = colors.borderTopColor;
  for (let x = 0; x < width; x += width / 8) {
    ctx.beginPath();
    ctx.moveTo(x + .5, 0);
    ctx.lineTo(x + .5, height);
    ctx.stroke();
  }
  ctx.fillStyle = colors.color;
  const max = Math.max(.08, peaks.reduce((l, r) => Math.max(l, r), 0));
  const step = width / Math.max(1, peaks.length);
  if (!peaks.length) ctx.fillRect(0, height / 2, width, 1);
  peaks.forEach((peak, index) => {
    const bar = Math.max(1, (height - 10) * (peak / max) ** .74);
    ctx.fillRect(index * step, (height - bar) / 2, Math.max(1, step - 1), bar);
  });
}
