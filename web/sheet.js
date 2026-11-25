const graphicTags = new Set([
  "svg", "desc", "title", "defs", "g", "path", "use", "text", "tspan",
  "rect", "line", "polyline", "polygon", "ellipse", "circle", "symbol",
]);

export function mountScore(host, score, notes, phase, seek) {
  const documentSVG = new DOMParser().parseFromString(score.svg, "image/svg+xml");
  const svg = documentSVG.documentElement;
  if (svg.localName !== "svg") throw Error("Partituru se nepodařilo vykreslit.");
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
      : Math.max(start + .1, lastNote);
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `Přejít na takt ${index + 1}`);
    const box = group.getBBox();
    const hit = document.createElementNS(svg.namespaceURI, "rect");
    for (const [key, value] of Object.entries({ x: box.x, y: box.y, width: box.width, height: box.height })) {
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
    measures.push({ group, start, end, number: index + 1 });
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
  return { markers, measures };
}
