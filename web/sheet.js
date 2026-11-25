const graphicTags = new Set([
  "svg", "desc", "title", "defs", "g", "path", "use", "text", "tspan",
  "rect", "line", "polyline", "polygon", "ellipse", "circle", "symbol",
]);

export function makeScoreCursor(namespace) {
  const line = document.createElementNS(namespace, "line");
  line.setAttribute("class", "score-playhead");
  line.setAttribute("aria-hidden", "true");
  const geometry = new WeakMap();

  return (measure, position) => {
    if (!measure?.group.isConnected) { line.remove(); return; }
    let layout = geometry.get(measure.group);
    if (!layout) {
      const staffs = [...measure.group.querySelectorAll("g.staff")];
      if (!staffs.length) return;
      const boxes = staffs.flatMap(staff => {
        const paths = [...staff.querySelectorAll(":scope > path")];
        return (paths.length ? paths : [staff]).map(path => path.getBBox());
      });
      const rhythm = [...measure.group.querySelectorAll("g.tabGrp, g.beam:has(g.tabDurSym)")].map(glyph => glyph.getBBox());
      const left = Math.min(...boxes.map(box => box.x));
      const right = Math.max(...boxes.map(box => box.x + box.width));
      const duration = measure.end - measure.start;
      const anchors = new Map([[0, left], [duration, right]]);
      for (const { time, node } of measure.anchors) {
        const elapsed = Math.round((time - measure.start) * 100000) / 100000;
        if (elapsed < 0 || elapsed >= duration) continue;
        const box = node.getBBox();
        anchors.set(elapsed, Math.max(left, Math.min(right, box.x + box.width / 2)));
      }
      const points = [...anchors].sort((a, b) => a[0] - b[0]);
      for (let i = 1; i < points.length; i++) points[i][1] = Math.max(points[i - 1][1], points[i][1]);
      layout = { points,
        top: Math.min(...boxes.map(box => box.y)) - 90,
        bottom: Math.max(...[...boxes, ...rhythm].map(box => box.y + box.height)) + 90 };
      geometry.set(measure.group, layout);
    }
    const points = layout.points;
    const elapsed = position - measure.start;
    const index = Math.min(points.length - 1, Math.max(1, points.findLastIndex(([time]) => time <= elapsed) + 1));
    const [t0, x0] = points[index - 1], [t1, x1] = points[index];
    const x = x0 + (x1 - x0) * Math.max(0, Math.min(1, (elapsed - t0) / (t1 - t0)));
    if (line.parentNode !== measure.group) {
      line.setAttribute("y1", layout.top);
      line.setAttribute("y2", layout.bottom);
      measure.group.append(line);
    }
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
  };
}

export function mountScore(host, score, notes, phase, seek) {
  const documentSVG = new DOMParser().parseFromString(score.svg, "image/svg+xml");
  const svg = documentSVG.documentElement;
  if (svg.localName !== "svg") throw Error("Could not render the score.");
  // The engraver emits its own glyphs. Keep only inert SVG and fragment references.
  for (const node of [svg, ...svg.querySelectorAll("*")]) {
    if (node.namespaceURI !== "http://www.w3.org/2000/svg" || !graphicTags.has(node.localName)) {
      node.remove();
      continue;
    }
    for (const attribute of [...node.attributes]) {
      if (/^on/i.test(attribute.localName) || attribute.localName === "style"
          || (attribute.localName === "href" && !/^#[\w.-]+$/u.test(attribute.value))
          || /url\s*\(/i.test(attribute.value)) {
        node.removeAttributeNode(attribute);
      }
    }
  }
  const width = parseFloat(svg.getAttribute("width")), height = parseFloat(svg.getAttribute("height"));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  host.replaceChildren(svg);
  const markers = [], byID = new Map();
  for (const glyph of svg.querySelectorAll("g.note[id]")) {
    const match = /^n(\d+)-\d+$/u.exec(glyph.id);
    const note = match && notes[Number(match[1])];
    if (!note) continue;
    markers.push({ note, button: glyph });
    byID.set(glyph.id, note);
  }
  const measures = [];
  const times = [...new Set(Object.values(score.measures))].sort((a, b) => a - b);
  const lastNote = notes.reduce((last, note) => Math.max(last, note.end), 0);
  for (const group of svg.querySelectorAll("g.measure[id]")) {
    const time = score.measures[group.id];
    if (time === undefined) continue;
    const start = phase + time / 1000;
    const index = times.indexOf(time);
    const end = times[index + 1] !== undefined ? phase + times[index + 1] / 1000
      : Math.max(start + .1, lastNote, phase + score.end / 1000);
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `Go to bar ${index + 1}`);
    const staffs = [...group.querySelectorAll(":scope > g.staff")];
    const boxes = (staffs.length ? staffs : [group]).map(staff => staff.getBBox());
    const left = Math.min(...boxes.map(box => box.x));
    const top = Math.min(...boxes.map(box => box.y));
    const right = Math.max(...boxes.map(box => box.x + box.width));
    const bottom = Math.max(...boxes.map(box => box.y + box.height));
    const hit = document.createElementNS(svg.namespaceURI, "rect");
    for (const [key, value] of Object.entries({ x: left, y: top, width: right - left, height: bottom - top })) {
      hit.setAttribute(key, String(value));
    }
    hit.setAttribute("class", "measure-hit");
    hit.setAttribute("aria-hidden", "true");
    group.prepend(hit);
    group.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      seek(start);
    });
    const anchors = [...group.querySelectorAll("g.note[id]")].flatMap(glyph => {
      const time = score.onsets[glyph.id];
      return time === undefined ? [] : [{ time: phase + time / 1000, node: glyph.querySelector(".notehead, text") ?? glyph }];
    });
    measures.push({ group, start, end, number: index + 1, anchors });
  }
  svg.addEventListener("click", event => {
    const glyph = event.target.closest("g.note[id]");
    const note = glyph && byID.get(glyph.id);
    if (note) seek(note.start);
    else {
      const group = event.target.closest("g.measure[id]");
      const measure = measures.find(row => row.group === group);
      if (measure) seek(measure.start);
    }
  });
  return { markers, measures, moveCursor: makeScoreCursor(svg.namespaceURI) };
}
