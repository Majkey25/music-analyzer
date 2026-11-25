import { beatGrid, clock, drumName, element, paintWave, parseTuning, pitchName } from "./grid.js";
import { Transport } from "./transport.js";
import { mountScore } from "./sheet.js";

const $ = id => document.getElementById(id);
const presets = {
  bass: "E1 A1 D2 G2", "drop-f": "F0 C1 F1 Bb1", guitar: "E2 A2 D3 G3 B3 E4",
  "drop-d": "D2 A2 D3 G3 B3 E4", seven: "B1 E2 A2 D3 G3 B3 E4",
};
const mode = () => $("instrument").value;
const strings = () => ["bass", "guitar"].includes(mode());
const ordered = rows => rows.sort((a, b) => a.start - b.start || a.pitch - b.pitch);
const serialNotes = rows => rows.map(({ start, end, pitch, velocity }) => ({ start, end, pitch, velocity }));
const sourceURL = trackId => `/api/projects/${project.id}/tracks/${trackId}/audio`;
const trackURL = trackId => `/api/projects/${project.id}/tracks/${trackId}`;

let project, track, rhythm, monitor, audioWave;
let playbackMode = "", mixSelection = null;
let notes = [], tuning = [], positions = [], pages = [], marks = [];
let view = "tab", page = 0, scorePage = 1, scoreCount = 1;
let scoreRequest, refreshID = 0, selectID = 0, monitorID = 0, busy = false;
let sheetMeasures = [];
const isSheet = () => view !== "roll";
const drafts = new Map();

function showError(error) {
  $("error").textContent = error instanceof Error ? error.message : String(error);
}

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw Error(body.error ?? `Požadavek selhal (${response.status}).`);
  }
  return response;
}

async function json(path, options) {
  return (await request(path, options)).json();
}

function post(data) {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
}

function guarded(action) {
  return async event => {
    try { $("error").textContent = ""; await action(event); } catch (error) { showError(error); }
  };
}

const transport = new Transport($("player"), positionChanged, showError);

function setupButtons() {
  const hasNotes = notes.length > 0, isAudio = Boolean(track?.audio);
  $("open").disabled = $("empty-open").disabled = $("demo").disabled = busy;
  $("save-project").hidden = !project?.tracks.length;
  $("save-project").disabled = busy;
  $("open").hidden = $("close").hidden = !project?.tracks.length;
  $("demo").hidden = Boolean(project?.tracks.length);
  $("close").disabled = busy;
  $("transcribe").disabled = busy || !isAudio;
  $("transcribe").hidden = !isAudio;
  $("separate").disabled = busy;
  $("separate").hidden = !project?.tracks.some(item => item.audio) || project.separated;
  $("monitor").disabled = $("apply-mix").disabled = busy;
  for (const button of $("practice-presets").querySelectorAll("button")) button.disabled = busy;
  $("first-note").disabled = !hasNotes;
  $("midi").disabled = $("xml").disabled = !hasNotes;
  $("txt").hidden = !strings();
  $("txt").disabled = !hasNotes || positions.length !== notes.length;
  $("results").hidden = !hasNotes;
  $("adjustments").hidden = !track?.notes?.length;
  $("result-empty").hidden = hasNotes;
  $("play").disabled = !transport.duration;
  $("loop").disabled = !hasNotes;
  $("strings-form").hidden = !strings();
  document.querySelector('[data-view="tab"]').hidden = !strings();
  document.querySelector('[data-view="roll"]').textContent = mode() === "drums" ? "Údery" : "Piano roll";
}

function busyStatus(value, message = "") {
  busy = value;
  $("status").textContent = message;
  $("progress").hidden = !value;
  setupButtons();
}

async function waitForWork(snapshot) {
  while (snapshot.status === "busy") {
    busyStatus(true, snapshot.message);
    await new Promise(resolve => setTimeout(resolve, 700));
    snapshot = await json(`/api/projects/${snapshot.id}`);
  }
  if (snapshot.status === "error") throw Error(snapshot.message);
  return snapshot;
}

function showTrackList() {
  $("tracks").replaceChildren(...project.tracks.map(item => {
    const li = element("li"), button = element("button", item.title, "track-item");
    button.type = "button";
    button.setAttribute("aria-pressed", String(item.id === track?.id));
    button.append(element("small", [item.method || "Audio", item.count === null ? "" : `${item.count} not`]
      .filter(Boolean).join(" / ")));
    button.disabled = busy;
    button.addEventListener("click", guarded(() => select(item.id, true)));
    li.append(button);
    if (item.audio && project.separated && item !== project.tracks[0]) {
      const link = element("a", "WAV", "stem-link");
      link.href = sourceURL(item.id) + "?download=1";
      link.setAttribute("aria-label", `Stáhnout ${item.title} jako WAV`);
      li.append(link);
    }
    return li;
  }));
}

function rhythmFields() {
  $("bpm").value = Number(rhythm.tempo[0].bpm.toFixed(2));
  $("numerator").value = rhythm.meter[0].numerator;
  $("denominator").value = rhythm.meter[0].denominator;
  $("phase").value = Number(rhythm.phase.toFixed(3));
  $("timing").textContent = rhythm.timing + (rhythm.tempo.length > 1 || rhythm.meter.length > 1
    ? " / obsahuje změny tempa nebo taktu." : "");
  $("sheet-info").textContent = `${rhythm.meter[0].numerator}/${rhythm.meter[0].denominator} · ${Number(rhythm.tempo[0].bpm.toFixed(2))} BPM`;
}

function setInstrument(value, preset) {
  $("instrument").value = value;
  $("preset").value = preset;
  if (presets[preset]) $("tuning").value = presets[preset];
}

function allowReplacement() {
  return ![...drafts.values()].some(value => value.edited)
    || window.confirm("Zavřít relaci s neuloženými úpravami?");
}

async function closeProject(checkEdits = true) {
  if (!project) return true;
  if (checkEdits && !allowReplacement()) {
    return false;
  }
  const time = transport.position;
  transport.setSource(null, 0);
  try {
    await request(`/api/projects/${project.id}`, { method: "DELETE" });
  } catch (error) {
    await setMonitor(playbackMode);
    transport.seek(time);
    throw error;
  }
  selectID++;
  monitorID++;
  refreshID++;
  scoreRequest?.abort();
  $("score-image").replaceChildren();
  sheetMeasures = [];
  project = track = rhythm = monitor = audioWave = null;
  playbackMode = "";
  mixSelection = null;
  drafts.clear();
  notes = positions = pages = marks = [];
  transport.loop = null;
  transport.setSource(null, 0);
  $("loop").setAttribute("aria-pressed", "false");
  $("studio").hidden = true;
  $("empty").hidden = false;
  busyStatus(false);
  return true;
}

async function loadProject(snapshot) {
  try {
    const ready = await waitForWork(snapshot);
    await closeProject(false);
    project = ready;
    $("project-title").textContent = project.title;
    rhythm = { tempo: project.tempo, meter: project.meter, phase: project.phase, timing: project.timing };
    rhythmFields();
    $("empty").hidden = true;
    $("studio").hidden = false;
    await select(project.active ?? project.tracks[0].id, true);
    if (project.practice) {
      const saved = project.practice;
      mixSelection = saved.mix.length ? saved.mix : null;
      setupMonitorOptions();
      if ([...$("monitor").options].some(option => option.value === saved.monitor)) {
        $("monitor").value = saved.monitor;
        await setMonitor(saved.monitor);
      }
      setSpeed(saved.speed);
      if (saved.view !== "tab" || strings()) view = saved.view;
      transport.seek(saved.position);
      page = pageFor(transport.position);
      await render(true);
    }
  } catch (error) {
    if (project?.id !== snapshot.id) {
      await request(`/api/projects/${snapshot.id}`, { method: "DELETE" }).catch(() => {});
    }
    throw error;
  } finally {
    busyStatus(false);
    if (project) showTrackList();
  }
}

async function select(id, first = false) {
  const revision = ++selectID;
  monitorID++;
  const detail = await json(trackURL(id));
  if (revision !== selectID) return;
  track = detail;
  scoreRequest?.abort();
  $("score-image").replaceChildren();
  marks = [];
  sheetMeasures = [];
  mixSelection = null;
  $("loop").setAttribute("aria-pressed", "false");
  if (detail.setup && !drafts.has(id)) {
    const raw = (detail.notes ?? []).map((note, i) => ({
      ...note, ...(detail.setup.preferred[i] == null ? {} : { string: detail.setup.preferred[i] }),
    }));
    drafts.set(id, { raw, mode: detail.mode, preset: "custom", edited: false,
      tuning: detail.setup.tuning.map(pitchName).join(" "), frets: detail.setup.frets, offset: 0, transpose: 0 });
  }
  const draft = drafts.get(id);
  if (draft) {
    $("instrument").value = draft.mode;
    $("preset").value = draft.preset;
    $("tuning").value = draft.tuning;
    $("frets").value = draft.frets;
    $("offset").value = draft.offset;
    $("transpose").value = draft.transpose;
  } else {
    setInstrument(detail.mode, detail.mode === "bass" ? (detail.method === "Demo" ? "drop-f" : "bass")
      : detail.mode === "guitar" ? "guitar" : "custom");
    $("offset").value = $("transpose").value = "0";
  }
  setupMonitorOptions();
  $("monitor").value = detail.audio ? detail.id : project.source ?? "";
  $("track-description").textContent = `${detail.title} / ${detail.method || "Zvuková stopa"}`;
  showTrackList();
  refreshNotes();
  await setMonitor($("monitor").value);
  if (revision !== selectID) return;
  view = strings() ? "tab" : "score";
  scorePage = 1;
  await refresh({ first });
}

async function setMonitor(id, keep = false) {
  const revision = ++monitorID;
  let selected = null, url = null, mixedNotes = null;
  if (id === "@backing" || id === "@custom") {
    const ids = id === "@backing" ? audioStems().filter(item => item.id !== track.id).map(item => item.id) : mixSelection;
    if (!ids?.length) throw Error("Vyber alespoň jednu zvukovou stopu.");
    const result = await json(`/api/projects/${project.id}/practice`, post({ tracks: ids }));
    selected = { wave: result.wave };
    url = `/api/projects/${project.id}/practice/${result.key}`;
  } else if (id === "@all-midi" || id === "@backing-midi") {
    const items = project.tracks.filter(item => item.count && (id !== "@backing-midi" || item.id !== track.id));
    mixedNotes = ordered((await Promise.all(items.map(async item => {
      const source = item.id === track.id ? track : await json(trackURL(item.id));
      return playingNotes(source).map(note => ({ ...note, mode: drafts.get(item.id)?.mode ?? source.mode }));
    }))).flat());
  } else if (id) {
    selected = id === track.id ? track : await json(trackURL(id));
    url = sourceURL(selected.id);
  }
  if (revision !== monitorID) return;
  playbackMode = id;
  monitor = selected;
  audioWave = monitor?.wave ?? null;
  transport.notes = mixedNotes ?? notes;
  const duration = audioWave?.duration ?? (transport.notes.length ? Math.max(...transport.notes.map(note => note.end)) : 0);
  transport.setSource(url, duration, keep);
  $("seek").max = Math.max(duration, .01);
  $("seek").disabled = duration === 0;
  paintTimeline();
  updatePracticeButtons();
  return true;
}

function audioStems() {
  return project.tracks.filter(item => item.audio && item.id !== project.source);
}

function setupMonitorOptions() {
  const choices = project.tracks.filter(item => item.audio);
  const stems = audioStems();
  const midiTracks = project.tracks.filter(item => item.count);
  const options = [];
  if (project.source) options.push(new Option("Celá skladba", project.source));
  for (const item of stems) options.push(new Option(item.title, item.id));
  options.push(new Option("Vybrané noty", ""));
  if (stems.length > 1 && stems.some(item => item.id === track.id)) {
    options.push(new Option("Doprovod bez této stopy", "@backing"));
  }
  if (!choices.length && midiTracks.length > 1) {
    options.push(new Option("Všechny MIDI stopy", "@all-midi"),
      new Option("Doprovod bez této MIDI stopy", "@backing-midi"));
  }
  if (mixSelection?.length) options.push(new Option("Vlastní doprovod", "@custom"));
  $("monitor").replaceChildren(...options);
  const quickMix = options.some(option => ["@backing", "@backing-midi"].includes(option.value));
  $("monitor-label").hidden = options.length < 2 || quickMix;
  $("mixer").hidden = stems.length < 2;
  $("practice-presets").hidden = !quickMix;
  const list = $("mix-tracks");
  list.replaceChildren(element("legend", "Stopy pro poslech"));
  for (const item of stems) {
    const input = element("input");
    input.type = "checkbox";
    input.value = item.id;
    input.checked = mixSelection ? mixSelection.includes(item.id) : item.id !== track.id;
    const label = element("label");
    label.append(input, document.createTextNode(item.title));
    list.append(label);
  }
}

function presetSources() {
  return [project.source ?? "@all-midi", track.audio ? track.id : "",
    project.source ? "@backing" : "@backing-midi"];
}

function updatePracticeButtons() {
  for (const [index, button] of [...$("practice-presets").querySelectorAll("button")].entries()) {
    button.setAttribute("aria-pressed", String(presetSources()[index] === playbackMode));
  }
  $("mix-summary").textContent = playbackMode === "@custom" ? "Vlastní doprovod je aktivní" : "Vlastní doprovod";
}

async function changeMonitor(id) {
  const wasPlaying = transport.playing;
  $("monitor").value = id;
  try {
    if (await setMonitor(id, true)) {
      await refresh();
      if (wasPlaying) await transport.play();
    }
  } catch (error) {
    $("monitor").value = playbackMode;
    throw error;
  }
}

function playingNotes(source) {
  const draft = drafts.get(source?.id);
  const base = draft?.raw ?? source?.notes ?? [];
  const offset = draft?.offset ?? 0, transpose = draft?.transpose ?? 0;
  return ordered(base.map(note => ({ ...note, start: Math.max(0, note.start + offset),
    end: note.end + offset, pitch: note.pitch + transpose, raw: note }))
    .filter(note => note.end > note.start && note.pitch >= 0 && note.pitch <= 127));
}

function refreshNotes() {
  notes = playingNotes(track);
  if (!playbackMode.startsWith("@") || !playbackMode.endsWith("-midi")) transport.notes = notes;
  transport.mode = mode();
  if (!monitor) {
    transport.duration = transport.notes.length ? Math.max(...transport.notes.map(note => note.end)) : 0;
    $("seek").max = Math.max(.01, transport.duration);
  }
}

function saveDraft() {
  const prior = drafts.get(track.id);
  const draft = {
    raw: prior?.raw ?? track.notes?.map(note => ({ ...note })) ?? [],
    edited: prior?.edited ?? false,
    mode: mode(), preset: $("preset").value, tuning: $("tuning").value,
    frets: Number($("frets").value), offset: Number($("offset").value),
    transpose: Number($("transpose").value),
  };
  drafts.set(track.id, draft);
  return draft;
}

async function refresh({ first = false } = {}) {
  const revision = ++refreshID;
  scoreRequest?.abort();
  if (transport.playing) transport.pause();
  refreshNotes();
  positions = [];
  setupButtons();
  paintTimeline();
  if (strings() && notes.length) {
    tuning = parseTuning($("tuning").value);
    if (!$("frets").reportValidity()) return;
    const result = await json("/api/tab", post({
      notes: serialNotes(notes), tuning, frets: Number($("frets").value), bass: mode() === "bass",
      preferred: notes.map(note => note.raw.string ?? null),
    }));
    if (revision !== refreshID) return;
    positions = result.positions;
  }
  const duration = Math.max(.1, transport.duration, ...notes.map(note => note.end));
  pages = beatGrid(rhythm.tempo, rhythm.meter, rhythm.phase, duration);
  if (first && notes.length) {
    page = pageFor(notes[0].start);
    transport.seek(Math.min(notes[0].start, transport.duration));
  } else page = Math.min(page, pages.length - 1);
  setupButtons();
  await render(true);
}

function pageFor(time) {
  const index = pages.findIndex(segment => segment.end > time + .00001);
  return index < 0 ? Math.max(0, pages.length - 1) : index;
}

function localNotes(segment) {
  return notes.flatMap((note, index) => note.start < segment.end && note.end > segment.start
    ? [{ note, position: positions[index] }] : []);
}

function visibleLanes(segmentNotes) {
  if (view === "tab") return tuning.map((pitch, i) => ({ id: i, label: pitchName(pitch) })).reverse();
  if (mode() === "drums") return [...new Set(segmentNotes.map(item => item.note.pitch))]
    .sort((a, b) => b - a).map(pitch => ({ id: pitch, label: drumName(pitch) }));
  const pitches = segmentNotes.map(item => item.note.pitch);
  const low = Math.max(0, Math.min(...pitches, pitches.length ? 127 : 48) - 1);
  const high = Math.min(127, Math.max(...pitches, pitches.length ? 0 : 59) + 1);
  return Array.from({ length: high - low + 1 }, (_, i) => ({ id: high - i, label: pitchName(high - i) }));
}

function graph(segment, items, lanes) {
  const root = $("grid");
  root.classList.toggle("drums", mode() === "drums");
  const ruler = element("div", "", "grid-ruler");
  const rulerLabel = element("span", "Doby", "grid-label");
  ruler.append(rulerLabel);
  const columns = element("div", "", "grid-body ruler-columns");
  columns.style.top = "0";
  ruler.append(columns);
  root.replaceChildren(ruler);
  const body = element("div", "", "grid-body");
  const percent = time => (100 * (time - segment.start) / (segment.end - segment.start)) + "%";
  marks = [];
  lanes.forEach(lane => {
    const row = element("div", "", "grid-lane");
    row.append(element("span", lane.label, "grid-label"));
    root.append(row);
  });
  root.append(body);
  for (const tick of segment.ticks) {
    if (tick.time >= segment.end - .001) continue;
    const marker = element("div", "", "beat"), label = element("span",
      tick.beat === 1 ? `${tick.bar}.1` : String(tick.beat), "ruler-beat");
    if (tick.beat === 1) {
      marker.classList.add("is-bar");
      label.classList.add("is-bar");
    }
    marker.style.left = label.style.left = percent(tick.time);
    body.append(marker);
    columns.append(label);
  }
  const line = element("div");
  line.id = "playhead";
  line.setAttribute("aria-hidden", "true");
  body.append(line);
  const laneIndex = new Map(lanes.map((lane, i) => [lane.id, i]));
  for (const { note, position } of items) {
    const row = laneIndex.get(view === "tab" ? position?.string : note.pitch);
    if (row === undefined) continue;
    const text = view === "tab" ? String(position.fret) : "";
    const button = element("button", text, "note");
    button.type = "button";
    button.title = `${mode() === "drums" ? drumName(note.pitch) : pitchName(note.pitch)} / ${clock(note.start)}`;
    button.setAttribute("aria-label", view === "tab"
      ? `${button.title}, struna ${position.string + 1}, pražec ${position.fret}` : button.title);
    button.style.top = `calc(${row} * var(--row-height) + .14rem)`;
    button.style.left = percent(Math.max(segment.start, note.start));
    button.style.width = percent(segment.start + Math.min(segment.end, note.end) - Math.max(segment.start, note.start));
    button.addEventListener("click", () => transport.seek(note.start));
    body.append(button);
    marks.push({ note, button });
  }
  $("keyboard").replaceChildren();
  $("keyboard").hidden = mode() !== "piano";
  if (mode() === "piano") {
    const pitches = [...laneIndex.keys()].sort((a, b) => a - b);
    for (const pitch of pitches) {
      const key = element("span", pitchName(pitch), "key");
      key.classList.toggle("dark", [1, 3, 6, 8, 10].includes(pitch % 12));
      key.dataset.pitch = pitch;
      $("keyboard").append(key);
    }
  }
}

function editTable(items) {
  if (!$("edit").open) return;
  const labels = [mode() === "drums" ? "Úder MIDI" : "Tón MIDI", "Začátek (s)", "Konec (s)"];
  if (strings()) labels.push("Struna");
  labels.push("");
  const table = element("table"), head = element("thead"), row = element("tr");
  row.append(...labels.map(label => {
    const th = element("th", label);
    th.scope = "col";
    return th;
  }));
  head.append(row);
  const body = element("tbody");
  for (const { note } of items.filter(item => item.note.start >= pages[page].start)) {
    const tr = element("tr");
    const fields = ["pitch", "start", "end"];
    fields.forEach((field, index) => {
      const cell = element("td"), input = element("input");
      input.type = "number";
      input.min = "0";
      input.max = field === "pitch" ? "127" : "3600";
      input.step = field === "pitch" ? "1" : "0.001";
      input.value = Number(note[field].toFixed(3));
      input.setAttribute("aria-label", `${labels[index]} noty v ${clock(note.start)}`);
      input.addEventListener("change", guarded(async () => {
        if (!input.reportValidity()) return;
        const value = Number(input.value);
        if ((field === "start" && value >= note.end) || (field === "end" && value <= note.start)) {
          input.value = note[field];
          throw Error("Konec noty musí následovat po začátku.");
        }
        const draft = saveDraft(), raw = draft.raw[rawIndex(draft, note)];
        raw[field] = value - (field === "pitch" ? draft.transpose : draft.offset);
        draft.edited = true;
        await refresh();
      }));
      cell.append(input);
      tr.append(cell);
    });
    if (strings()) {
      const cell = element("td"), choice = element("select");
      choice.setAttribute("aria-label", `Struna noty v ${clock(note.start)}`);
      choice.append(new Option("Automaticky", ""));
      const maxFret = Number($("frets").value);
      tuning.forEach((pitch, index) => {
        const fret = note.pitch - pitch;
        if (fret >= 0 && fret <= maxFret) {
          choice.append(new Option(`${pitchName(pitch)} (${fret})`, index));
        }
      });
      if (note.raw.string !== undefined && ![...choice.options].some(option => +option.value === note.raw.string && option.value)) {
        choice.append(new Option("Mimo ladění", note.raw.string));
      }
      choice.value = note.raw.string ?? "";
      choice.addEventListener("change", guarded(async () => {
        const draft = saveDraft(), raw = draft.raw[rawIndex(draft, note)];
        if (choice.value === "") delete raw.string;
        else raw.string = Number(choice.value);
        draft.edited = true;
        await refresh();
      }));
      cell.append(choice);
      tr.append(cell);
    }
    const cell = element("td"), remove = element("button", "Smazat");
    remove.type = "button";
    remove.setAttribute("aria-label", `Smazat notu v ${clock(note.start)}`);
    remove.addEventListener("click", guarded(async () => {
      const draft = saveDraft();
      const index = rawIndex(draft, note);
      draft.raw.splice(index, 1);
      draft.edited = true;
      await refresh();
    }));
    cell.append(remove);
    tr.append(cell);
    body.append(tr);
  }
  table.append(head, body);
  $("note-table").replaceChildren(table);
}

function rawIndex(draft, note) {
  const index = draft.raw.indexOf(note.raw);
  return index >= 0 ? index : track.notes.indexOf(note.raw);
}

function scoreData(tab = false) {
  const shift = rhythm.phase;
  // MusicXML quantizes to the displayed beat origin. MIDI keeps absolute time.
  const items = notes.map((note, index) => ({
    aligned: { ...note, start: Math.max(0, note.start - shift), end: note.end - shift },
    note, position: positions[index],
  })).filter(item => item.aligned.end > item.aligned.start);
  const body = { notes: serialNotes(items.map(item => item.aligned)), tempo: rhythm.tempo, meter: rhythm.meter,
    mode: mode(), title: project.title, page: scorePage };
  if (tab) {
    body.tuning = tuning;
    body.positions = items.map(item => item.position ?? null);
  }
  return { body, original: items.map(item => item.note) };
}

async function drawScore(atCursor) {
  scoreRequest?.abort();
  const controller = new AbortController();
  scoreRequest = controller;
  $("score-image").replaceChildren();
  $("score-image").hidden = true;
  $("score-loading").hidden = false;
  try {
    const activeView = view;
    const { body, original } = scoreData(view === "tab");
    body.width = Math.min(2700, Math.max(900, Math.round($("results").clientWidth / .46)));
    if (atCursor) body.at = Math.max(0, transport.position - rhythm.phase);
    const score = await json("/api/export/score", { ...post(body), signal: controller.signal });
    if (controller.signal.aborted || view !== activeView) return;
    scoreCount = score.pages;
    scorePage = score.page;
    const drawn = mountScore($("score-image"), score, original, rhythm.phase, time => {
      transport.seek(time);
      page = pageFor(time);
      editTable(localNotes(pages[page]));
    });
    $("score-image").hidden = false;
    marks = drawn.markers;
    sheetMeasures = drawn.measures;
    if (sheetMeasures.length) {
      page = pageFor(sheetMeasures[0].start);
      editTable(localNotes(pages[page]));
    }
    pager();
    syncLoop();
    positionChanged(transport.position, transport.playing);
  } catch (error) {
    if (error.name !== "AbortError") throw error;
  } finally {
    if (scoreRequest === controller) $("score-loading").hidden = true;
  }
}

function pager() {
  const current = pages[page];
  $("page-label").textContent = isSheet() ? `${scorePage} / ${scoreCount}`
    : current?.first ? `Takt ${current.first}–${current.last}` : "Před první dobou";
  $("previous").disabled = isSheet() ? scorePage <= 1 : page <= 0;
  $("next").disabled = isSheet() ? scorePage >= scoreCount : page >= pages.length - 1;
}

function syncLoop() {
  if ($("loop").getAttribute("aria-pressed") !== "true") transport.loop = null;
  else if (!isSheet()) transport.loop = pages[page];
  else transport.loop = sheetMeasures.length
    ? { start: sheetMeasures[0].start, end: Math.min(transport.duration, sheetMeasures.at(-1).end) } : null;
}

async function render(atCursor = false) {
  for (const button of document.querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  if (!notes.length || !pages.length) return;
  const current = pages[page], items = localNotes(current);
  const isScore = isSheet();
  $("graph").hidden = isScore;
  if (!isScore) {
    scoreRequest?.abort();
    $("score-loading").hidden = true;
    $("score-image").hidden = true;
  }
  $("keyboard").hidden = true;
  $("loop").disabled = false;
  const unplaced = positions.filter(position => position === null).length;
  $("placement").textContent = view === "tab" ? unplaced
    ? `${tuning.length} strun / ${unplaced} not bez hratelné polohy v tomto ladění či souzvuku.`
    : `${tuning.length} strun / navržený prstoklad` : "";
  editTable(items);
  pager();
  syncLoop();
  if (isScore) {
    marks = [];
    await drawScore(atCursor);
  } else {
    graph(current, items, visibleLanes(items));
    positionChanged(transport.position, transport.playing);
  }
}

function positionChanged(position, playing) {
  $("time").textContent = clock(position);
  $("seek").value = position;
  $("play").textContent = playing ? "Pozastavit" : "Přehrát";
  $("wave-head").style.left = `${100 * position / Math.max(.01, transport.duration)}%`;
  const segment = pages[page], line = $("playhead");
  if (segment && line) {
    line.hidden = position < segment.start || position > segment.end;
    line.style.left = `${100 * (position - segment.start) / (segment.end - segment.start)}%`;
  }
  for (const { note, button } of marks) {
    button.classList.toggle("playing", note.start <= position && note.end > position);
  }
  if (isSheet()) {
    for (const { group, start, end } of sheetMeasures) {
      group.classList.toggle("is-current", position >= start && position < end);
    }
  }
  if (!$("keyboard").hidden) {
    const active = new Set(marks.filter(({ note }) => note.start <= position && note.end > position)
      .map(({ note }) => note.pitch));
    for (const key of $("keyboard").children) key.classList.toggle("playing", active.has(Number(key.dataset.pitch)));
  }
}

async function changePage(direction) {
  if (isSheet()) scorePage += direction;
  else {
    page += direction;
    transport.seek(pages[page].start);
    $("graph").scrollLeft = 0;
  }
  await render();
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function textTab() {
  const lines = [project.title, `Ladění: ${tuning.map(pitchName).join(" ")}`,
    "Každý sloupec má čas noty a takt.dobu."];
  const ticks = pages.flatMap(segment => segment.ticks);
  let beat = 0;
  for (let i = 0; i < notes.length; i += 10) {
    const subset = notes.slice(i, i + 10);
    lines.push("", "     " + subset.map(note => clock(note.start).padStart(8)).join(" "));
    lines.push("     " + subset.map(note => {
      while (beat + 1 < ticks.length && ticks[beat + 1].time <= note.start + .001) beat++;
      const tick = ticks[beat];
      return (tick?.time <= note.start + .001 ? `${tick.bar}.${tick.beat}` : "·").padStart(8);
    }).join(" "));
    for (let string = tuning.length - 1; string >= 0; string--) {
      const values = subset.map((_, offset) => {
        const position = positions[i + offset];
        return (position?.string === string ? String(position.fret) : "·").padStart(8);
      });
      lines.push(pitchName(tuning[string]).padStart(4) + "|" + values.join(" "));
    }
    const missing = subset.map((note, offset) => positions[i + offset] ? null
      : `${clock(note.start)} ${pitchName(note.pitch)}`).filter(Boolean);
    if (missing.length) lines.push("Bez hratelné polohy: " + missing.join(", "));
  }
  return lines.join("\n");
}

function importDialog() { $("import-dialog").showModal(); }
$("open").onclick = $("empty-open").onclick = importDialog;
$("cancel-import").onclick = () => $("import-dialog").close();
$("import-form").onsubmit = guarded(async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const files = [...$("files").files];
  const archive = files.find(file => file.name.toLowerCase().endsWith(".zip"));
  if (archive && files.length > 1) {
    $("import-dialog").close();
    throw Error("Uloženou relaci otevři samostatně.");
  }
  const restore = new FormData();
  if (archive) restore.append("project", archive);
  if (!allowReplacement()) return;
  $("import-dialog").close();
  busyStatus(true, "Načítám soubor.");
  try {
    await loadProject(await json(archive ? "/api/restore" : "/api/projects", {
      method: "POST", body: archive ? restore : form,
    }));
    $("import-form").reset();
  } finally { busyStatus(false); }
});
$("demo").onclick = guarded(async () => {
  if (!allowReplacement()) return;
  await loadProject(await json("/api/demo", { method: "POST" }));
});
$("close").onclick = guarded(closeProject);
$("save-project").onclick = guarded(async () => {
  const edits = {};
  for (const [id, draft] of drafts) {
    const saved = playingNotes({ id });
    const change = { mode: draft.mode, notes: serialNotes(saved) };
    if (["bass", "guitar"].includes(draft.mode)) {
      change.setup = { tuning: parseTuning(draft.tuning), frets: draft.frets,
        preferred: saved.map(note => note.raw.string ?? null) };
    }
    edits[id] = change;
  }
  const id = project.id;
  const practice = { monitor: playbackMode, mix: mixSelection ?? [], view,
    speed: transport.speed, position: transport.position };
  const data = { ...rhythm, active: track.id, edits, practice };
  busyStatus(true, "Připravuji uložení relace.");
  try {
    project = await waitForWork(await json(`/api/projects/${id}/save`, post(data)));
    const link = element("a");
    link.href = `/api/projects/${id}/download`;
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
    for (const draft of drafts.values()) draft.edited = false;
  } finally { busyStatus(false); }
});

$("separate").onclick = guarded(async () => {
  busyStatus(true);
  showTrackList();
  try {
    project = await waitForWork(await json(`/api/projects/${project.id}/separate`, { method: "POST" }));
    const bass = project.tracks.find(item => item.mode === "bass" && item.audio);
    if (bass) await select(bass.id, true);
  } finally { busyStatus(false); showTrackList(); }
});

$("transcribe").onclick = guarded(async () => {
  const trackId = track.id, selected = mode();
  if (drafts.get(trackId)?.edited && !window.confirm("Nahradit ručně upravené noty novým přepisem?")) return;
  const pitches = strings() ? parseTuning($("tuning").value) : [];
  if (strings() && !$("frets").reportValidity()) return;
  const minimum = pitches.length ? Math.min(...pitches) : selected === "piano" ? 21 : selected === "violin" ? 55 : 21;
  const maximum = pitches.length ? Math.min(127, Math.max(...pitches) + Number($("frets").value))
    : selected === "piano" ? 108 : 120;
  const settings = saveDraft();
  busyStatus(true);
  showTrackList();
  try {
    const snapshot = await json(trackURL(trackId) + "/transcribe", post({ mode: selected, minimum, maximum }));
    project = await waitForWork(snapshot);
    const detail = await json(trackURL(trackId));
    drafts.set(trackId, { ...settings, raw: detail.notes, edited: false, offset: 0, transpose: 0 });
    await select(trackId, true);
  } finally { busyStatus(false); showTrackList(); }
});

$("instrument").onchange = guarded(async () => {
  if (strings()) {
    $("preset").value = mode();
    $("tuning").value = presets[mode()];
    view = "tab";
  } else view = "score";
  saveDraft();
  await refresh();
});
$("preset").onchange = guarded(async () => {
  if (presets[$("preset").value]) $("tuning").value = presets[$("preset").value];
  saveDraft();
  await refresh();
});
$("settings-form").onsubmit = guarded(async event => {
  event.preventDefault();
  parseTuning($("tuning").value);
  if ($("tuning").value !== presets[$("preset").value]) $("preset").value = "custom";
  saveDraft();
  await refresh();
});
$("adjust-form").onsubmit = guarded(async event => {
  event.preventDefault();
  saveDraft();
  await refresh({ first: true });
});
$("rhythm-form").onsubmit = guarded(async event => {
  event.preventDefault();
  rhythm = { tempo: [{ time: 0, bpm: Number($("bpm").value) }],
    meter: [{ time: 0, numerator: Number($("numerator").value), denominator: Number($("denominator").value) }],
    phase: Number($("phase").value), timing: "Vlastní mřížka" };
  rhythmFields();
  await refresh();
});
$("reset-rhythm").onclick = guarded(async () => {
  rhythm = { tempo: project.tempo, meter: project.meter, phase: project.phase, timing: project.timing };
  rhythmFields();
  await refresh();
});
for (const button of document.querySelectorAll("[data-view]")) {
  button.onclick = guarded(async () => { view = button.dataset.view; await render(true); });
}
$("monitor").onchange = guarded(async () => {
  await changeMonitor($("monitor").value);
});
for (const [index, id] of ["practice-all", "practice-solo", "practice-backing"].entries()) {
  $(id).onclick = guarded(() => changeMonitor(presetSources()[index]));
}
$("apply-mix").onclick = guarded(async () => {
  const selected = [...$("mix-tracks").querySelectorAll("input:checked")].map(input => input.value);
  if (!selected.length) throw Error("Vyber alespoň jednu stopu pro poslech.");
  mixSelection = selected;
  setupMonitorOptions();
  await changeMonitor("@custom");
});
$("previous").onclick = guarded(() => changePage(-1));
$("next").onclick = guarded(() => changePage(1));
$("seek").oninput = () => transport.seek(Number($("seek").value));
$("seek").onchange = guarded(async () => { page = pageFor(transport.position); await render(true); });
$("wave").onclick = guarded(async event => {
  transport.seek(transport.duration * event.offsetX / event.currentTarget.clientWidth);
  page = pageFor(transport.position);
  await render(true);
});
$("first-note").onclick = guarded(async () => { transport.seek(notes[0].start); page = pageFor(notes[0].start); await render(true); });
$("play").onclick = guarded(() => transport.playing ? transport.pause() : transport.play());
document.addEventListener("keydown", event => {
  if (event.code !== "Space" || event.repeat || $("play").disabled || $("studio").hidden
      || $("import-dialog").open || event.target.closest("button, input, select, textarea, summary, a, [role=button]")) return;
  event.preventDefault();
  $("play").click();
});
function setSpeed(value) {
  transport.rate(value);
  for (const button of $("speed").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.speed) === value));
  }
}
for (const button of $("speed").querySelectorAll("button")) {
  button.onclick = () => setSpeed(Number(button.dataset.speed));
}
$("choose-instrument").onclick = () => {
  $("instrument-settings").open = true;
  $("instrument").focus();
};
$("loop").onclick = () => {
  $("loop").setAttribute("aria-pressed", String($("loop").getAttribute("aria-pressed") !== "true"));
  syncLoop();
  if (transport.loop && (transport.position < transport.loop.start || transport.position >= transport.loop.end)) {
    transport.seek(transport.loop.start);
  }
};
$("edit").ontoggle = () => pages[page] && editTable(localNotes(pages[page]));
$("add-note").onclick = guarded(async () => {
  const draft = saveDraft(), start = pages[page].start - draft.offset;
  draft.raw.push({ start, end: start + .25, pitch: (mode() === "drums" ? 36 : 60) - draft.transpose, velocity: 95 });
  draft.edited = true;
  await refresh();
});
$("txt").onclick = () => download(new Blob([textTab()], { type: "text/plain;charset=utf-8" }), "prepis.txt");
for (const [button, kind, filename] of [["midi", "midi", "prepis.mid"], ["xml", "musicxml", "prepis.musicxml"]]) {
  $(button).onclick = guarded(async () => {
    const { body: data } = scoreData(kind === "musicxml" && view === "tab");
    if (kind === "midi") data.notes = serialNotes(notes);
    const response = await request(`/api/export/${kind}`, post(data));
    download(await response.blob(), filename);
  });
}
function paintTimeline() {
  if (audioWave) {
    paintWave($("wave"), audioWave.peaks);
    return;
  }
  if (!notes.length || !transport.duration) {
    paintWave($("wave"), []);
    return;
  }
  const bins = Array(320).fill(0);
  for (const note of notes) {
    const first = Math.floor(note.start / transport.duration * bins.length);
    const last = Math.min(bins.length - 1, Math.floor(note.end / transport.duration * bins.length));
    for (let i = first; i <= last; i++) bins[i] += note.velocity * Math.exp(-(i - first) * .27);
  }
  paintWave($("wave"), bins);
}
new ResizeObserver(paintTimeline).observe($("wave"));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTimeline);
let sheetWidth = 0, reflow;
new ResizeObserver(([entry]) => {
  const width = Math.round(entry.contentRect.width);
  if (Math.abs(width - sheetWidth) < 20) return;
  const previous = sheetWidth;
  sheetWidth = width;
  clearTimeout(reflow);
  if (previous && width) {
    reflow = setTimeout(() => {
      if (project && isSheet() && notes.length && !busy) drawScore(true).catch(showError);
    }, 150);
  }
}).observe($("results"));
setupButtons();
