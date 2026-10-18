// Мультитрек NASLUX: дорожки трека (партии после разделения или сам трек),
// заглушить/соло/громкость, обрезка краёв, метроном по долям самой песни с
// отсчётом, повтор куска, своя дорожка из файла и скачивание микса.
//
// Звук -- Web Audio: все дорожки -- буферы, запущенные от одного момента
// часов AudioContext, поэтому расходиться им не с чего (у <audio> на каждую
// дорожку рассинхрон набегает за минуту). Заглушить/соло -- это громкость
// узла, а не перезапуск: переключается мгновенно, без щелчка.
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const clock = (sec) => {
  const s = Math.max(0, Math.floor(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8.5 5.6v12.8a1 1 0 0 0 1.5.86l10.2-6.4a1 1 0 0 0 0-1.72L10 4.74a1 1 0 0 0-1.5.86z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><rect x="6.5" y="5" width="4" height="14" rx="1.4"/><rect x="13.5" y="5" width="4" height="14" rx="1.4"/></svg>',
  pencil: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4"/></svg>',
};
// Цвет волны по инструменту: дорожки различаются с первого взгляда
const COLORS = [
  [/вокал|голос|vocal/i, '#e7b46a'], [/гитар|guitar/i, '#e08a4f'], [/бас|bass/i, '#9d8cf0'],
  [/барабан|ударн|drum/i, '#4fc3b0'], [/клавиш|пиано|piano|keys|синт|synth/i, '#6aa8f0'],
  [/струн|string/i, '#d98bb4'], [/дух|brass|wood|флейт/i, '#c9c46a'],
];
const colorOf = (name) => (COLORS.find(([re]) => re.test(name)) || [null, '#a7abb8'])[1];

const jobId = decodeURIComponent(location.pathname.split('/').pop());
const params = new URLSearchParams(location.search);
// Сдвинутая версия (тон/темп) -- свои файлы, значит, и своя раскладка кусков
const STORE = `naslux.mix.${jobId}.${params.get('file') || ''}${params.get('v') ? `.${params.get('v')}` : ''}`;

let ctx = null;
let master = null;
let metroGain = null;
// Дорожка -- полоса со своей громкостью, M/S и кусками звука на ней:
// {name, key, url, orig, extra, custom, gain, mute, solo, vol, color, clips}.
// Кусок -- {src, offset, trimStart, trimEnd}: src -- файл (буфер и пики),
// offset -- где на ленте секунда 0 файла, trimStart/trimEnd -- какая часть
// файла звучит. Резка делит кусок на два на той же дорожке; куски тянут по
// времени и на другие дорожки, копируют и вставляют.
const tracks = [];
const files = new Map();  // key -> {key, url, name, buffer, peaks, extra, local}
let selected = -1;        // выбранная дорожка
let pick = null;          // выбранный кусок на ней (последний, на который нажали)
// Несколько кусков -- с Ctrl (⌘ на Mac) или Shift: двигаются, копируются и
// удаляются вместе
let picks = new Set();
let montage = false;      // режим монтажа: протяжка двигает кусок
let clipboard = null;     // скопированное: {items: [{src, rel, trimStart, trimEnd}]}
const undoStack = [];
let duration = 0;
let playing = false;
let startTime = 0;        // момент часов ctx, когда позиция была startPos
let startPos = 0;
let pos = 0;              // позиция на паузе
let sources = [];
let analysis = null;      // {key, bpm, beats, downbeats, chords}
let beats = [];
let downbeats = new Set();
let nextBeat = 0;
let metroOn = false;
let countIn = false;
const haptic = (kind) => { if (window.haptic) window.haptic(kind); };   // отклик (nav.js)
let rec = null;              // идёт запись голоса (см. «запись голоса»)
let mic = null;              // поток микрофона
let micFor = '';             // какой микрофон открыт
let micMeter = null;         // индикатор уровня в окне записи
let recorderReady = null;
// Сведение (см. mixbus.js): стиль, роли дорожек и что насчитал анализ
let mixing = { on: false, style: '', roles: {} };
let mixState = { voice: 1, music: 1, curve: null, sig: '' };
let liveBus = null;
const loop = { on: false, a: 0, b: 0 };   // последнее выделение на линейке -- его играет «Повтор»
// Все выделенные места на линейке (с Ctrl -- несколько): их копируют,
// вырезают и удаляют на выбранной дорожке
let regions = [];
let liveRegion = null;    // выделение, которое сейчас тянут мышью/пальцем -- видно сразу
let liveAdding = false;
// что показывать нижней строкой линейки: аккорды или время трека
let rulerMode = 'chords';
const shownRegions = () => (liveRegion ? (liveAdding ? regions.concat([liveRegion]) : [liveRegion]) : regions);
// Есть выделенные места на линейке -- копируют, вырезают и удаляют их (на
// выбранной дорожке); щелчок по дорожке тогда лишь выбирает её. Ctrl +
// щелчок по кускам -- выделение кусков вместо мест.
const useRegions = () => regions.length > 0;
let job = null;
let info = null;
let file = '';
// Окно просмотра: вся песня в один экран -- такт в 10 px, названия аккордов
// не помещаются. По умолчанию видно ~30 секунд, при игре окно едет за
// курсором; «−/＋» и Ctrl+колесо -- масштаб, колесо с Shift -- прокрутка.
let zoom = 1;
let viewStart = 0;
const viewLen = () => (duration || 1) / zoom;
const toX = (sec, w) => ((sec - viewStart) / viewLen()) * w;
let hidden = new Set();   // убранные дорожки (по названию) -- помнятся в браузере
let base = null;          // набор дорожек для сдвига: {id, file ('*' -- все партии)}
let shifted = null;       // открытая сдвинутая версия (задача «Темп и тональность»)

// ---------------------------------------------------------------- утилиты

function toast(text, ms = 4500) {
  const t = $('toast');
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}

function note(text) {
  $('note').hidden = !text;
  $('note').innerHTML = text || '';
}

function audio() {
  if (ctx) return ctx;
  // iPhone: Web Audio молчит в беззвучном режиме -- просим режим «воспроизведение»
  try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch (error) { /* старый Safari */ }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  // 32 кГц: на репетиции разницы не слышно, а памяти на 12 дорожек нужно
  // вдвое меньше, чем на 44.1 кГц.
  try { ctx = new Ctx({ sampleRate: 32000 }); } catch (error) { ctx = new Ctx(); }
  master = ctx.createGain();
  master.connect(limiter(ctx)).connect(ctx.destination);
  metroGain = window.metroSound.output(ctx, $('metroVol').value);
  return ctx;
}

function saved() {
  try { return JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (error) { return {}; }
}

// Раскладка: дорожки с кусками, по ключам файлов (не по названиям -- их
// можно менять). Куски из ещё не сохранённых на сервере файлов не пишем.
function layout() {
  return tracks.map((t) => ({
    key: t.key, name: t.name, custom: Boolean(t.custom), mute: t.mute, solo: t.solo, vol: t.vol, fx: t.fx || '',
    clips: t.clips.filter((c) => !c.src.local).map((c) => ({ src: c.src.key, offset: c.offset,
      trimStart: c.trimStart, trimEnd: c.trimEnd })),
  })).filter((l) => l.clips.length || l.custom || !l.key.startsWith('local:'));
}

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      metro: metroOn, countIn, metroVol: $('metroVol').value, hidden: [...hidden],
      metroMode: $('metroMode').value, trackHeight, lanes: layout(), rulerMode,
      mixing: { on: mixing.on, style: mixing.style, roles: mixing.roles },
    }));
  } catch (error) { /* приватный режим -- просто без памяти */ }
  refreshSplit();
  if (mixing.on) {
    clearTimeout(save.mixTimer);
    save.mixTimer = setTimeout(() => analyzeMix().catch(() => {}), 900);
  }
}

const keyOf = (url, name) => (url ? decodeURIComponent(url.split('?')[0].split('/').pop()) : '') || name;
const clipStart = (c) => c.offset + c.trimStart;
const clipEnd = (c) => c.offset + c.trimEnd;
function updateDuration() {
  const ends = tracks.flatMap((t) => t.clips.map((c) => c.offset + c.src.buffer.duration));
  duration = ends.length ? Math.max(...ends) : Math.max(duration, 30);
  $('total').textContent = clock(duration);
}

function makeLane(name, extra = {}) {
  const gain = ctx.createGain();
  const lane = { name, key: extra.key || `lane:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    url: '', orig: '', extra: '', custom: false, mute: false, solo: false, vol: 100, fx: '', ...extra,
    gain, color: colorOf(name), clips: [] };
  route(lane);
  return lane;
}

// ------------------------------------------------- обработка дорожки
// Готовые цепочки (как в Audition, только одной кнопкой): обрезать гул,
// убрать «бубнёж», добавить разборчивости, выровнять громкость
// компрессором, по желанию -- зал. Звучит сразу и попадает в микс.
const FX = {
  '': { title: 'Без обработки', text: 'Как записано' },
  clean: { title: 'Чистый голос', text: 'Срезать гул и «бубнёж», разборчивость, ровная громкость' },
  studio: { title: 'Студия', text: 'Чистый голос + небольшая комната — как на записи' },
  hall: { title: 'Зал', text: 'Чистый голос + большой зал — для баллад' },
  radio: { title: 'Радио', text: 'Узкая полоса, как в телефоне — для вставок и эффектов' },
  inst: { title: 'Мягкий инструмент', text: 'Срезать гул и немного выровнять — для гитары, клавиш' },
};
const irCache = new WeakMap();

function impulse(c, seconds, decay) {
  let byLen = irCache.get(c);
  if (!byLen) { byLen = {}; irCache.set(c, byLen); }
  const id = `${seconds}:${decay}`;
  if (byLen[id]) return byLen[id];
  const n = Math.round(c.sampleRate * seconds);
  const buf = c.createBuffer(2, n, c.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, decay);
  }
  byLen[id] = buf;
  return buf;
}

// Цепочка на контексте c (живой звук или сведение в файл): {input, output}
function fxChain(c, kind) {
  const input = c.createGain();
  const output = c.createGain();
  if (!kind || !FX[kind]) { input.connect(output); return { input, output }; }
  const filter = (type, freq, q = 0.7, gain = 0) => {
    const f = c.createBiquadFilter();
    f.type = type; f.frequency.value = freq; f.Q.value = q; f.gain.value = gain;
    return f;
  };
  const chain = [];
  if (kind === 'radio') {
    chain.push(filter('highpass', 350, 0.9), filter('lowpass', 3200, 0.9), filter('peaking', 1500, 1, 4));
  } else {
    chain.push(filter('highpass', kind === 'inst' ? 60 : 90, 0.7));
    if (kind !== 'inst') {
      chain.push(filter('peaking', 280, 1, -2.5), filter('peaking', 3200, 0.9, 3), filter('highshelf', 10000, 0.7, 2));
    }
  }
  const comp = c.createDynamicsCompressor();
  comp.threshold.value = kind === 'inst' ? -16 : -22;
  comp.knee.value = 6;
  comp.ratio.value = kind === 'inst' ? 2 : 3.5;
  comp.attack.value = 0.006;
  comp.release.value = 0.16;
  const makeup = c.createGain();
  makeup.gain.value = kind === 'inst' ? 1.15 : 1.5;
  chain.push(comp, makeup);
  let node = input;
  chain.forEach((next) => { node.connect(next); node = next; });
  node.connect(output);
  if (kind === 'studio' || kind === 'hall') {
    const verb = c.createConvolver();
    verb.buffer = kind === 'hall' ? impulse(c, 3.2, 3) : impulse(c, 1.4, 4);
    const wet = c.createGain();
    wet.gain.value = kind === 'hall' ? 0.32 : 0.18;
    node.connect(verb).connect(wet).connect(output);
  }
  return { input, output };
}

// Ограничитель на общем выходе: громкий дубль плюс дорожки не хрипят ни в
// наушниках, ни в скачанном миксе (тише -1 дБ он ничего не трогает)
function limiter(c) {
  const l = c.createDynamicsCompressor();
  l.threshold.value = -1;
  l.knee.value = 0;
  l.ratio.value = 20;
  l.attack.value = 0.002;
  l.release.value = 0.08;
  return l;
}

function route(lane) {
  try { lane.gain.disconnect(); } catch (error) { /* ещё не подключён */ }
  if (lane.fxNodes) { try { lane.fxNodes.output.disconnect(); } catch (error) { /* */ } }
  const voice = liveBus && roleOf(lane) === 'voice';
  lane.fxNodes = voice ? window.mixBus.voiceChain(ctx, mixing.style) : fxChain(ctx, lane.fx);
  lane.gain.connect(lane.fxNodes.input);
  lane.fxNodes.output.connect(!liveBus ? master : voice ? liveBus.voice : liveBus.music);
}

// Голос это или музыка: как отметили в «Сведении», иначе -- по названию
const VOICE_RE = /голос|вокал|vocal|voice|бэк|хор|choir|рэп|rap/i;
function roleOf(t) {
  return mixing.roles[t.key] || (VOICE_RE.test(t.name) ? 'voice' : 'music');
}
// Своя дорожка (записанная или загруженная), а не партия из разделения
const isOwn = (t) => Boolean(t.extra || t.custom || t.clips.some((c) => c.src.extra || c.src.local));
// Голос нейросети: голосовая партия из разделения -- когда спет свой голос
function aiVoices() {
  const own = tracks.some((t) => roleOf(t) === 'voice' && isOwn(t) && t.clips.length);
  return own ? tracks.filter((t) => roleOf(t) === 'voice' && !isOwn(t) && t.clips.length) : [];
}

function makeClip(src, r = {}) {
  const trimStart = Math.max(0, Math.min(r.trimStart ?? 0, src.buffer.duration - 0.05));
  return { src, offset: Math.max(-trimStart, r.offset || 0), trimStart,
    trimEnd: Math.max(trimStart + 0.05, Math.min(r.trimEnd ?? src.buffer.duration, src.buffer.duration)) };
}

function addSource(key, url, name, buffer, extra = '', local = false) {
  const src = { key, url, name, buffer, extra, local, peaks: peaksOf(buffer, 1600) };
  files.set(key, src);
  return src;
}

// ---------------------------------------------- отмена (Ctrl+Z) и выбор
function remember() {
  undoStack.push(tracks.map((t) => ({ ...t, clips: t.clips.map((c) => ({ ...c })) })));
  if (undoStack.length > 40) undoStack.shift();
  $('undoBtn').disabled = false;
}

function undo() {
  const prev = undoStack.pop();
  $('undoBtn').disabled = !undoStack.length;
  if (!prev) { toast('Отменять нечего'); return; }
  haptic('light');
  tracks.forEach((t) => { if (!prev.some((p) => p.gain === t.gain)) t.gain.disconnect(); });
  prev.forEach((p) => { if (!tracks.some((t) => t.gain === p.gain)) p.gain.connect(master); });
  tracks.length = 0;
  tracks.push(...prev);
  tracks.forEach(route);
  pick = null;
  picks = new Set();
  selected = Math.min(selected, tracks.length - 1);
  updateDuration();
  render();
  save();
  if (playing) startAt(position());
}

// ---------------------------------------------------------------- загрузка

async function init() {
  $('play').innerHTML = ICON.play;
  info = await (await fetch('/api/studio')).json();
  if (info.mixOpen === false) {
    $('title').textContent = 'Мультитрек';
    note('Мультитрек — в тарифе «Студия»: все партии вместе, метроном по долям, '
      + 'смена темпа и тональности, свои дорожки. <a href="/pricing?next=/studio">Подключить «Студию»</a> · '
      + '<a href="/studio">Вернуться в Студию</a>');
    return;
  }
  job = info.jobs.find((j) => j.id === jobId);
  if (!job || job.status !== 'done') {
    $('title').textContent = 'Трек не найден';
    note('Этот трек не найден среди ваших или ещё не готов. <a href="/studio">Вернуться в Студию</a>');
    return;
  }
  if (job.expired) {
    $('title').textContent = job.name;
  $('title').dataset.noi18n = '';      // название трека -- как есть, без перевода
    note('Файлы этого трека удалены по сроку хранения (14 дней).');
    return;
  }
  file = params.get('file') || (job.files[0] && job.files[0].name) || '';
  $('title').textContent = job.name;
  $('title').dataset.noi18n = '';      // название трека -- как есть, без перевода
  document.title = `${job.name} — мультитрек NASLUX`;

  const stems = job.mode === 'stems' ? job : info.jobs
    .filter((j) => j.from === job.id && j.mode === 'stems' && j.status === 'done' && j.files.length
      && (!j.sourceFile || j.sourceFile === file))
    .sort((a, b) => b.at - a.at)[0];
  const splitting = info.jobs.find((j) => j.from === job.id && j.mode === 'stems'
    && ['queued', 'running'].includes(j.status) && (!j.sourceFile || j.sourceFile === file));
  const version = job.files.find((f) => f.name === file) || job.files[0];

  // Сдвинутые версии этого набора дорожек (кнопка «Тон и темп»)
  base = stems ? { id: stems.id, file: '*' } : { id: job.id, file: version.name };
  const shifts = info.jobs.filter((j) => j.shift && j.shift.of === base.id && j.shift.file === base.file);
  shifted = shifts.find((j) => j.id === params.get('v') && j.status === 'done') || null;
  versionPicker(shifts);

  const list = shifted ? shifted.files.map((f) => ({ name: stems || f.origLabel ? f.label : 'Трек целиком', url: f.url, orig: f.origLabel || '' }))
    : stems ? stems.files.map((f) => ({ name: f.label, url: f.url, orig: f.origLabel || '' }))
      : [{ name: version.label === 'Оригинал' ? 'Трек целиком' : version.label, url: version.url, orig: version.origLabel || '' }];
  // Свои дорожки (репетиция, подложка) хранятся при треке. В сдвинутой
  // версии их нет: они в исходной тональности и темпе.
  if (!shifted) {
    const taken = new Set(list.map((item) => item.name));
    (job.extras || []).filter((x) => x.for === (params.get('file') || '')).forEach((x) => {
      let name = x.label;
      while (taken.has(name)) name += ' ·';
      taken.add(name);
      list.push({ name, url: x.url, extra: x.name });
    });
  }
  $('sub').textContent = stems
    ? `${list.length} ${plural(list.length, 'дорожка', 'дорожки', 'дорожек')}${job.mode === 'stems' ? '' : ` · ${version.label}`}`
    : 'Одна дорожка — разделите трек, чтобы глушить отдельные инструменты';
  hasStems = Boolean(stems);
  showSplit(splitting);
  if (stems) $('splitBox').hidden = true;      // покажется, когда появится монтаж (refreshSplit)

  // Аккорды и тональность: по партиям без голоса и барабанов, если трек
  // разделён (голос тянет ноты мимо аккорда), иначе -- по самой версии
  if (stems) loadAnalysis(stems.id, 'harmony');
  else loadAnalysis(jobId, version.name);
  await loadTracks(list);
}

function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

async function loadTracks(list) {
  audio();
  const memory = saved();
  metroOn = Boolean(memory.metro);
  countIn = Boolean(memory.countIn);
  if (memory.metroVol) $('metroVol').value = memory.metroVol;
  if (memory.metroMode) $('metroMode').value = memory.metroMode;
  if (memory.trackHeight) setTrackHeight(memory.trackHeight, false);
  setRulerMode(memory.rulerMode === 'time' ? 'time' : 'chords', false);
  if (memory.mixing && typeof memory.mixing === 'object') {
    const style = window.mixBus.STYLES[memory.mixing.style] ? memory.mixing.style : '';
    mixing = { on: Boolean(memory.mixing.on && style), style,
      roles: memory.mixing.roles && typeof memory.mixing.roles === 'object' ? memory.mixing.roles : {} };
  }
  metroGain.setVolume($('metroVol').value);
  pressed('metro', metroOn);
  pressed('countIn', countIn);
  // Убранные дорожки («×») не грузим вовсе -- и память, и трафик (если
  // только их кусков не скопировали на другие дорожки)
  hidden = new Set(memory.hidden || []);
  list.forEach((item) => { item.key = keyOf(item.url, item.name); });
  const lanes = Array.isArray(memory.lanes) ? memory.lanes : null;
  const needed = new Set((lanes || []).flatMap((l) => (l.clips || []).map((c) => c.src)));
  const kept = list.filter((item) => needed.has(item.key) || (!hidden.has(item.key) && !hidden.has(item.name)));
  if (kept.length) list = kept; else hidden.clear();   // убрать все нельзя
  showHidden();
  let done = 0;
  note(`Загружаем дорожки: 0 из ${list.length}…`);
  let missing = 0;
  await Promise.all(list.map(async (item) => {
    const data = await audioBytes(item.url).catch(() => null);
    if (!data) { missing++; return; }
    const buffer = await ctx.decodeAudioData(data);
    addSource(item.key, item.url, item.name, buffer, item.extra || '');
    note(`Загружаем дорожки: ${++done} из ${list.length}…`);
  }));
  const itemOf = (key) => list.find((item) => item.key === key);
  const origin = (item) => ({ key: item.key, url: item.url, orig: item.orig || '', extra: item.extra || '' });
  if (lanes) {
    lanes.forEach((l) => {
      const item = itemOf(l.key);
      if (!item && !l.custom && !(l.clips || []).length) return;
      const lane = makeLane(item && !l.custom ? item.name : (l.name || 'Дорожка'),
        { ...(item ? origin(item) : { key: l.key }), custom: Boolean(l.custom) && !item,
          mute: Boolean(l.mute), solo: Boolean(l.solo), vol: l.vol ?? 100, fx: FX[l.fx] ? l.fx : '' });
      lane.clips = (l.clips || []).filter((c) => files.has(c.src)).map((c) => makeClip(files.get(c.src), c));
      if (lane.clips.length || lane.custom || item) tracks.push(lane);
    });
  }
  // Файлы, которых в раскладке нет (новые партии, своя дорожка с другого
  // устройства, первый заход) -- каждой своя дорожка целиком
  const placed = new Set(tracks.flatMap((t) => [t.key, ...t.clips.map((c) => c.src.key)]));
  list.forEach((item) => {
    if (placed.has(item.key) || !files.has(item.key)) return;
    // Своя дорожка, ушедшая на сервер, пока страницу перезагружали, -- в ту же пустую дорожку
    const waiting = item.extra && tracks.find((t) => t.custom && !t.clips.length && t.name === item.name);
    if (waiting) {
      Object.assign(waiting, origin(item), { custom: false });
      waiting.clips = [makeClip(files.get(item.key))];
      return;
    }
    const old = ((memory.clips || {})[item.key]) || [((memory.tracks || {})[item.name]) || {}];  // память до 16.10
    const lane = makeLane(item.name, { ...origin(item), mute: Boolean(old[0].mute), solo: Boolean(old[0].solo),
      vol: old[0].vol ?? 100 });
    lane.clips = old.map((r) => makeClip(files.get(item.key), r));
    const rank = (memory.order || []).indexOf(item.key);
    if (rank >= 0 && rank < tracks.length) tracks.splice(rank, 0, lane); else tracks.push(lane);
  });
  if (!tracks.length) {
    note('Нет интернета, а этот трек ещё не сохранён на устройстве. Откройте его один раз при интернете — '
      + 'дальше он будет работать и без сети.');
    return;
  }
  note(missing ? `Без интернета загрузились не все дорожки: нет ${missing}. Откройте трек при интернете, чтобы сохранить все.` : '');
  keepOffline();
  updateDuration();
  zoom = duration > 45 ? duration / 30 : 1;
  render();
  refreshSplit();
  mixUi();
  if (mixing.on) { buildLiveBus(); analyzeMix().catch(() => {}); }
}

// Звук дорожек -- через память устройства: открыли трек при интернете --
// на репетиции без сети он загрузится оттуда (sw.js, тот же кэш)
const AUDIO_CACHE = 'naslux-audio-v1';

async function audioBytes(url) {
  let cache = null;
  try { cache = window.caches ? await caches.open(AUDIO_CACHE) : null; } catch (error) { cache = null; }
  const saved = cache && await cache.match(url).catch(() => null);
  if (saved) return saved.arrayBuffer();
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  if (cache) {
    try { await cache.put(url, response.clone()); } catch (error) { /* память устройства кончилась */ }
  }
  return response.arrayBuffer();
}

// Сохранить для работы без интернета саму страницу, её скрипты и данные
// трека; звук уже в памяти. Отметка «✓ без интернета» -- когда всё на месте.
async function keepOffline() {
  const mark = $('offlineState');
  if (!('serviceWorker' in navigator) || !window.caches || !mark) return;
  try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (error) { /* */ }
  const reg = await navigator.serviceWorker.ready.catch(() => null);
  const worker = reg && (reg.active || navigator.serviceWorker.controller);
  if (!worker) return;
  const here = location.origin;
  const urls = [location.href, '/studio', '/api/studio', '/api/me',
    ...performance.getEntriesByType('resource').map((r) => r.name)
      .filter((u) => u.startsWith(`${here}/static/`) || (u.startsWith(`${here}/api/studio/`) && u.includes('/analysis')))];
  if (navigator.onLine) worker.postMessage({ type: 'keep', urls: [...new Set(urls)] });
  mark.hidden = false;
  mark.title = 'Трек сохранён на этом устройстве: откроется и сыграет без интернета';
}

// ------------------------------------------------------------- монтаж
// Куски на дорожках: «Разрезать» делит кусок по курсору на месте; в
// «Монтаже» кусок тянут по времени и на другую дорожку, края прилипают к
// тактам и к краям других кусков -- так склеивают встык. «Копировать» --
// выделенное на линейке (или выбранный кусок), «Вставить» -- по курсору на
// выбранную дорожку. Ctrl+C / Ctrl+V / Delete / Ctrl+Z -- то же с клавиатуры.
function markSelected() {
  document.querySelectorAll('.mx-row[data-i]').forEach((row) => {
    row.classList.toggle('picked', Number(row.dataset.i) === selected);
  });
  const lane = tracks[selected];
  const something = picks.size || (regions.length && lane && lane.clips.length);
  $('cutBtn').disabled = !lane || !lane.clips.length;
  $('copyBtn').disabled = !something;
  $('cutXBtn').disabled = !something;
  $('pasteBtn').disabled = !lane || !clipboard;
  $('delClipBtn').disabled = !something;
  $('deselectBtn').disabled = !picks.size && !regions.length;
  const count = picks.size > 1 ? picks.size : regions.length > 1 ? regions.length : 0;
  ['copyBtn', 'cutXBtn', 'delClipBtn'].forEach((id) => {
    if (count) $(id).dataset.count = count; else delete $(id).dataset.count;
  });
}

function choose(i, clip = null, add = false) {
  selected = i;
  if (add && clip) regions = [];            // Ctrl + кусок -- выделяем куски, места на линейке снимаем
  if (!add && regions.length) clip = null;  // места выделены -- щелчок только выбирает дорожку
  if (add && clip) {
    // Ctrl/Shift -- добавить кусок к выбранным или убрать его из них
    if (picks.has(clip)) { picks.delete(clip); pick = [...picks].pop() || null; } else { picks.add(clip); pick = clip; }
  } else {
    pick = clip;
    picks = new Set(clip ? [clip] : []);
  }
  markSelected();
  redraw();
}

const laneOf = (clip) => tracks.findIndex((t) => t.clips.includes(clip));

function snapOffset(clip, offset, width) {
  const start = offset + clip.trimStart;
  const end = offset + clip.trimEnd;
  const limit = (12 / width) * viewLen();        // 12 пикселей
  let best = null;
  const edges = [0];
  tracks.forEach((t) => t.clips.forEach((o) => { if (o !== clip) edges.push(clipStart(o), clipEnd(o)); }));
  beats.forEach((b) => { if (downbeats.has(b.toFixed(2))) edges.push(b); });
  edges.forEach((edge) => {
    [edge - start, edge - end].forEach((d) => {
      if (Math.abs(d) < limit && (best === null || Math.abs(d) < Math.abs(best))) best = d;
    });
  });
  return Math.max(-clip.trimStart, offset + (best || 0));
}

function clipAt(lane, sec) {
  return lane && lane.clips.find((c) => sec >= clipStart(c) && sec <= clipEnd(c));
}

function setMontage(on, quiet = false) {
  if (montage === on) return;
  montage = on;
  pressed('montage', montage);
  $('board').classList.toggle('montage', montage);
  if (montage && !quiet) {
    toast('Монтаж: тяните кусок по времени или на другую дорожку. Края прилипают к тактам и к соседним кускам — так куски склеиваются', 7000);
  }
  redraw();
}

function cut() {
  const lane = tracks[selected];
  const at = position();
  const c = (pick && tracks[selected] && tracks[selected].clips.includes(pick) && at > clipStart(pick) && at < clipEnd(pick))
    ? pick : clipAt(lane, at);
  if (!lane) { toast('Нажмите на дорожку, которую резать, и поставьте курсор, где резать'); return; }
  if (!c || at <= clipStart(c) + 0.05 || at >= clipEnd(c) - 0.05) {
    toast('Поставьте курсор на кусок выбранной дорожки — там, где резать');
    return;
  }
  remember();
  haptic('light');
  const right = { ...c, trimStart: at - c.offset };
  c.trimEnd = at - c.offset;
  lane.clips.splice(lane.clips.indexOf(c) + 1, 0, right);
  pick = right;
  picks = new Set([right]);
  setMontage(true, true);
  render();
  save();
  if (playing) startAt(position());
  toast('Разрезано. Тяните кусок мышью или пальцем — по времени или на другую дорожку');
}

// Что копировать: выделенное на линейке (на выбранной дорожке) или выбранный кусок
function copy() {
  const lane = tracks[selected];
  if (!lane || !lane.clips.length) { toast('Нажмите на дорожку, с которой копировать'); return false; }
  // Несколько кусков (Ctrl) -- копируются как есть, каждый со своей дорожки
  if (picks.size > 1 && !useRegions()) {
    const chosen = [...picks];
    const a0 = Math.min(...chosen.map(clipStart));
    const lane0 = Math.min(...chosen.map(laneOf));
    clipboard = { items: chosen.map((c) => ({ src: c.src, rel: clipStart(c) - a0, trimStart: c.trimStart,
      trimEnd: c.trimEnd, lane: laneOf(c) - lane0 })), len: Math.max(...chosen.map(clipEnd)) - a0 };
    markSelected();
    toast(`Скопировано кусков: ${chosen.length} — поставьте курсор и нажмите «Вставить» (Ctrl+V)`);
    return true;
  }
  const spans = useRegions() ? regions.map((r) => [r.a, r.b]) : pick ? [[clipStart(pick), clipEnd(pick)]] : [];
  if (!spans.length) {
    toast('Выделите кусок на линейке над дорожками или нажмите на кусок');
    return false;
  }
  const a0 = spans[0][0];
  const items = [];
  spans.forEach(([a, b]) => lane.clips.filter((c) => clipEnd(c) > a && clipStart(c) < b).forEach((c) => {
    const from = Math.max(clipStart(c), a);
    const to = Math.min(clipEnd(c), b);
    items.push({ src: c.src, rel: from - a0, trimStart: from - c.offset, trimEnd: to - c.offset });
  }));
  if (!items.length) { toast('В выделенном нет звука этой дорожки'); return false; }
  const len = spans[spans.length - 1][1] - a0;
  clipboard = { items, len };
  markSelected();
  toast(`Скопировано ${spans.length > 1 ? `мест: ${spans.length}` : clock(len)} — поставьте курсор и нажмите «Вставить» (Ctrl+V)`);
  return true;
}

function paste() {
  const lane = tracks[selected];
  if (!clipboard) { toast('Сначала скопируйте кусок'); return; }
  if (!lane) { toast('Нажмите на дорожку, куда вставить'); return; }
  remember();
  haptic('light');
  const at = position();
  let last = null;
  const pasted = [];
  clipboard.items.forEach((item) => {
    // куски с разных дорожек -- на те же по счёту дорожки от выбранной
    const target = tracks[Math.min(tracks.length - 1, selected + (item.lane || 0))];
    last = makeClip(item.src, { offset: at + item.rel - item.trimStart, trimStart: item.trimStart, trimEnd: item.trimEnd });
    target.clips.push(last);
    target.clips.sort((x, y) => clipStart(x) - clipStart(y));
    pasted.push(last);
  });
  pick = last;
  picks = new Set(pasted);
  setMontage(true, true);
  updateDuration();
  render();
  save();
  if (playing) startAt(position());
  toast('Вставлено. Кусок можно тянуть мышью или пальцем');
}

// Удалить: выбранные куски, а если их нет -- выделенные места на выбранной
// дорожке (кусок, попавший на край, обрезается, середина -- уходит)
function deleteClip(quiet = false) {
  if (picks.size && !useRegions()) {
    remember();
    haptic('medium');
    const count = picks.size;
    picks.forEach((clip) => {
      const lane = tracks[laneOf(clip)];
      if (lane) lane.clips.splice(lane.clips.indexOf(clip), 1);
    });
    pick = null;
    picks = new Set();
    afterEdit();
    if (!quiet) toast(`${count > 1 ? `Удалено кусков: ${count}` : 'Кусок удалён'}. Вернуть — «Отменить» (Ctrl+Z)`);
    return true;
  }
  const lane = tracks[selected];
  if (regions.length && lane) {
    remember();
    haptic('medium');
    regions.forEach(({ a, b }) => {
      lane.clips = lane.clips.flatMap((c) => {
        if (clipEnd(c) <= a || clipStart(c) >= b) return [c];
        const parts = [];
        if (clipStart(c) < a - 0.02) parts.push({ ...c, trimEnd: a - c.offset });
        if (clipEnd(c) > b + 0.02) parts.push({ ...c, trimStart: b - c.offset });
        return parts;
      });
    });
    afterEdit();
    if (!quiet) toast('Выделенное убрано с дорожки. Вернуть — «Отменить» (Ctrl+Z)');
    return true;
  }
  toast('Нажмите на кусок (с Ctrl — несколько) или выделите место на линейке и выберите дорожку');
  return false;
}

function afterEdit() {
  updateDuration();
  render();
  save();
  if (playing) startAt(position());
}

// Вырезать = скопировать и убрать
function cutOut() {
  if (!copy()) return;
  if (deleteClip(true)) toast('Вырезано — поставьте курсор и нажмите «Вставить» (Ctrl+V)');
}

// Снять выделение: куски, места на линейке, повтор
function deselect() {
  pick = null;
  picks = new Set();
  regions = [];
  loop.on = false;
  loop.a = 0;
  loop.b = 0;
  pressed('loop', false);
  markSelected();
  redraw();
}

$('cutBtn').addEventListener('click', cut);
$('copyBtn').addEventListener('click', copy);
$('pasteBtn').addEventListener('click', paste);
$('delClipBtn').addEventListener('click', () => deleteClip());
$('cutXBtn').addEventListener('click', cutOut);
$('deselectBtn').addEventListener('click', deselect);
$('undoBtn').addEventListener('click', undo);
$('montage').addEventListener('click', () => setMontage(!montage));

// --------------------------------------------------- название дорожки
// «Что это за дорожка?»: список инструментов, свои названия из профиля
// (их человек вводил раньше -- на любом устройстве) и поле для своего.
const INSTRUMENTS = ['Вокал', 'Бэк-вокал', 'Гитара', 'Ритм-гитара', 'Соло-гитара', 'Вторая гитара', 'Акустика',
  'Бас', 'Барабаны', 'Перкуссия', 'Клавиши', 'Пиано', 'Синтезатор', 'Орган', 'Скретчи', 'Сэмплы', 'Струнные',
  'Скрипка', 'Виолончель', 'Духовые', 'Саксофон', 'Труба', 'Флейта', 'Аккордеон', 'Баян', 'Гармонь',
  'Подложка', 'Минус', 'Клик'];
let me = null;
let labeling = null;        // {i, before}

async function myLabels() {
  if (!me) me = await fetch('/api/me').then((r) => r.json()).catch(() => ({}));
  return ((me && me.prefs && me.prefs.instruments) || []).filter((x) => typeof x === 'string');
}

function chip(text, mine) {
  return `<span class="mx-chip${mine ? ' mine' : ''}"><button type="button" data-pick="${esc(text)}">${esc(text)}</button>`
    + `${mine ? `<button type="button" class="mx-chip-x" data-forget="${esc(text)}" title="Убрать из моих" aria-label="Убрать «${esc(text)}» из моих">×</button>` : ''}</span>`;
}

async function openLabel(i) {
  const t = tracks[i];
  if (!t) return;
  labeling = { i, before: t.name };
  $('labelInput').value = t.name;
  $('labelReset').hidden = !t.orig;
  $('labelSheet').hidden = false;
  $('labelInput').focus();
  $('labelInput').select();
  const mine = await myLabels();
  $('labelMine').innerHTML = mine.length
    ? `<small>Мои</small>${mine.map((x) => chip(x, true)).join('')}` : '';
  $('labelChips').innerHTML = INSTRUMENTS.filter((x) => !mine.some((m) => m.toLowerCase() === x.toLowerCase()))
    .map((x) => chip(x, false)).join('');
}

function closeLabel() { $('labelSheet').hidden = true; labeling = null; }

async function saveLabel(text, reset = false) {
  if (!labeling) return;
  const t = tracks[labeling.i];
  const name = reset ? t.orig : text.trim().replace(/\s+/g, ' ').slice(0, 40);
  closeLabel();
  if (!name || name === t.name) return;
  t.name = name;
  t.color = colorOf(name);
  if (!t.url) t.custom = true;               // своя пустая дорожка -- название хранится в раскладке
  render();
  save();
  const where = /\/api\/studio\/file\/([^/]+)\/([^/?]+)/.exec(t.url || '');
  if (where) {
    const form = new FormData();
    form.append('file', decodeURIComponent(where[2]));
    form.append('label', reset ? '' : name);
    const answer = await fetch(`/api/studio/${where[1]}/label`, { method: 'POST', body: form }).catch(() => null);
    if (!answer || !answer.ok) toast('Название не сохранилось на сервере — проверьте интернет');
  }
  // Своё название (не из списка) -- запомнить в профиле, чтобы предложить снова
  if (!reset && !INSTRUMENTS.some((x) => x.toLowerCase() === name.toLowerCase())) {
    const form = new FormData();
    form.append('key', 'instruments');
    form.append('add', name);
    const answer = await fetch('/api/me/prefs', { method: 'POST', body: form }).catch(() => null);
    const data = answer && answer.ok ? await answer.json() : null;
    if (data && me) me.prefs = { ...(me.prefs || {}), instruments: data.instruments };
  }
}

$('labelSheet').addEventListener('click', async (e) => {
  const pick = e.target.closest('[data-pick]');
  if (pick) { saveLabel(pick.dataset.pick); return; }
  const forget = e.target.closest('[data-forget]');
  if (forget) {
    const form = new FormData();
    form.append('key', 'instruments');
    form.append('remove', forget.dataset.forget);
    const answer = await fetch('/api/me/prefs', { method: 'POST', body: form }).catch(() => null);
    const data = answer && answer.ok ? await answer.json() : null;
    if (data && me) me.prefs = { ...(me.prefs || {}), instruments: data.instruments };
    if (labeling) openLabel(labeling.i);
    return;
  }
  if (e.target === $('labelSheet')) closeLabel();
});
$('labelOk').addEventListener('click', () => saveLabel($('labelInput').value));
$('labelCancel').addEventListener('click', closeLabel);
$('labelReset').addEventListener('click', () => saveLabel('', true));
$('labelInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); saveLabel($('labelInput').value); }
  if (e.key === 'Escape') closeLabel();
});

function showHidden() {
  $('restoreTracks').hidden = !hidden.size;
  $('restoreTracks').textContent = `↺ Вернуть убранные (${hidden.size})`;
}

$('restoreTracks').addEventListener('click', () => {
  hidden.clear();
  save();
  location.reload();
});

// Пики волны: максимум по модулю в каждом из n кусков
function peaksOf(buffer, n) {
  const step = Math.max(1, Math.floor(buffer.length / n));
  const channels = [...Array(buffer.numberOfChannels).keys()].map((c) => buffer.getChannelData(c));
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let peak = 0;
    const from = i * step;
    const to = Math.min(buffer.length, from + step);
    for (const data of channels) {
      for (let k = from; k < to; k += 16) peak = Math.max(peak, Math.abs(data[k]));
    }
    out[i] = peak;
  }
  const top = Math.max(...out) || 1;
  return out.map((v) => v / top);
}

let analysisSrc = null;    // {owner, name} -- чей разбор; туда же сохраняется свой темп

async function loadAnalysis(owner, name) {
  analysisSrc = { owner, name };
  for (let tries = 0; tries < 40; tries++) {
    let answer = await fetch(`/api/studio/${owner}/analysis?file=${encodeURIComponent(name)}`)
      .then((r) => (r.ok ? r.json() : { error: 'нет' })).catch(() => ({ error: 'сеть' }));
    if (!answer.pending) {
      if (answer.error) { $('factKey').textContent = '—'; return; }
      analysis = shifted ? shiftAnalysis(answer, shifted.shift) : answer;
      answer = analysis;
      $('factKey').textContent = answer.key || '—';
      $('factBpm').textContent = answer.bpm ? `${answer.bpm} BPM` : '—';
      $('tempoBtn').classList.toggle('fixed', Boolean(answer.fixed));
      beats = answer.beats && answer.beats.length ? answer.beats : gridBeats(answer.bpm);
      downbeats = new Set((answer.downbeats || []).map((b) => b.toFixed(2)));
      meter();
      if (playing) nextBeat = Math.max(0, beats.findIndex((b) => b >= position()));
      if (duration) redraw();
      return;
    }
    $('factKey').textContent = '…';
    $('factBpm').textContent = '…';
    await new Promise((r) => setTimeout(r, 2500));
  }
}

// Разбор оригинала -> разбор сдвинутой версии: аккорды и тональность
// транспонируются, доли и аккорды растягиваются по темпу. Заново слушать
// запись не нужно -- сдвиг известен точно.
const SHARPS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const FLATS = { Db: 'C#', Eb: 'D#', Gb: 'F#', Ab: 'G#', Bb: 'A#' };
function transpose(name, semitones) {
  const m = /^([A-G][#b]?)(.*)$/.exec(name || '');
  if (!m || !semitones) return name;
  const index = SHARPS.indexOf(FLATS[m[1]] || m[1]);
  return index < 0 ? name : SHARPS[(index + Number(semitones) + 120) % 12] + m[2];
}

function shiftAnalysis(a, shift) {
  const rate = shift.tempo || 1;
  const st = shift.semitones || 0;
  const t = (x) => Math.round((x / rate) * 1000) / 1000;
  return {
    key: transpose(a.key, st), bpm: a.bpm ? Math.round(a.bpm * rate) : a.bpm,
    autoBpm: a.autoBpm ? Math.round(a.autoBpm * rate) : a.autoBpm, fixed: a.fixed, tempoBy: a.tempoBy,
    beats: (a.beats || []).map(t), downbeats: (a.downbeats || []).map(t),
    chords: (a.chords || []).map(([x, y, n]) => [t(x), t(y), transpose(n, st)]),
  };
}

function shiftName(shift) {
  const st = shift.semitones || 0;
  const tone = st ? `${st > 0 ? '+' : '−'}${Math.abs(st)} полутона` : '';
  const tempo = Math.round((shift.tempo || 1) * 100) !== 100 ? `темп ${Math.round(shift.tempo * 100)}%` : '';
  return [tone, tempo].filter(Boolean).join(', ');
}

function versionPicker(shifts) {
  const done = shifts.filter((j) => j.status === 'done' && j.files.length);
  const busy = shifts.find((j) => ['queued', 'running'].includes(j.status));
  $('versionBox').hidden = !done.length;
  $('version').innerHTML = '<option value="">Оригинал</option>' + done.map((j) =>
    `<option value="${j.id}"${shifted && shifted.id === j.id ? ' selected' : ''}>${esc(shiftName(j.shift))}</option>`).join('');
  $('version').onchange = () => {
    const next = new URLSearchParams(location.search);
    if ($('version').value) next.set('v', $('version').value); else next.delete('v');
    location.search = next.toString();
  };
  if (busy) waitShift(busy.id);
}

async function waitShift(id) {
  $('shiftBtn').disabled = true;
  for (;;) {
    const fresh = await (await fetch('/api/studio')).json();
    const j = fresh.jobs.find((x) => x.id === id);
    if (!j) break;
    if (j.status === 'done') {
      const next = new URLSearchParams(location.search);
      next.set('v', id);
      location.search = next.toString();
      return;
    }
    if (j.status === 'error') { note(`Не получилось сдвинуть: ${esc(j.error || '')}`); break; }
    note(`Сдвигаем тональность и темп: ${esc(j.stage || 'в очереди')}… Страница обновится сама.`);
    await new Promise((r) => setTimeout(r, 3000));
  }
  $('shiftBtn').disabled = false;
}

function askShift() {
  return new Promise((resolve) => {
    const modal = $('shiftModal');
    // сдвиг всегда от оригинала -- и из открытой сдвинутой версии тоже
    const orig = shifted
      ? { key: transpose(analysis && analysis.key, -(shifted.shift.semitones || 0)),
        bpm: analysis && analysis.bpm ? Math.round(analysis.bpm / (shifted.shift.tempo || 1)) : null }
      : { key: analysis && analysis.key, bpm: analysis && analysis.bpm };
    const sync = () => {
      const tone = Number($('shiftTone').value);
      const rate = Number($('shiftTempo').value);
      $('shiftToneVal').textContent = tone > 0 ? `+${tone}` : String(tone);
      $('shiftTempoVal').textContent = `${rate}%`;
      const now = [orig.key, orig.bpm ? `${orig.bpm} BPM` : ''].filter(Boolean).join(' · ');
      const next = [transpose(orig.key, tone), orig.bpm ? `${Math.round(orig.bpm * rate / 100)} BPM` : '']
        .filter(Boolean).join(' · ');
      $('shiftNow').innerHTML = now ? `Оригинал: <b>${esc(now)}</b>${tone || rate !== 100
        ? ` → станет: <b class="st-key">${esc(next)}</b>` : ''}` : '';
      $('shiftOk').disabled = tone === 0 && rate === 100;
    };
    $('shiftTone').value = shifted ? shifted.shift.semitones || 0 : 0;
    $('shiftTempo').value = shifted ? Math.round((shifted.shift.tempo || 1) * 100) : 100;
    modal.oninput = sync;
    sync();
    modal.hidden = false;
    $('shiftOk').onclick = () => {
      modal.hidden = true;
      resolve({ semitones: $('shiftTone').value, tempo: $('shiftTempo').value });
    };
    $('shiftCancel').onclick = () => { modal.hidden = true; resolve(null); };
  });
}

$('shiftBtn').addEventListener('click', async () => {
  if (!base) return;
  const choice = await askShift();
  if (!choice) return;
  if (playing) pause();
  const form = new FormData();
  form.append('file', base.file);
  form.append('semitones', choice.semitones);
  form.append('tempo', choice.tempo);
  const response = await fetch(`/api/studio/${base.id}/shift`, { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { toast(data.detail || 'Не получилось запустить'); return; }
  toast('Подкручиваем колки у всех дорожек — это минута-другая 🎚');
  waitShift(data.jobId);
});

// Метроном: доля -> место в такте (0 -- сильная) и номер такта. Режимы --
// каждая доля, через долю (1 и 3), раз в такт, раз в два такта: на быстром
// темпе щёлкать каждую долю утомительно, а музыканту хватает «раз».
let beatPos = [];
let beatBar = [];
function meter() {
  beatPos = [];
  beatBar = [];
  let pos = -1;
  let bar = -1;
  const known = downbeats.size > 0;
  beats.forEach((b, k) => {
    if (known ? downbeats.has(b.toFixed(2)) || pos < 0 : k % 4 === 0) { pos = 0; bar += 1; } else pos += 1;
    beatPos.push(pos);
    beatBar.push(bar);
  });
}

function metroKeeps(k) {
  const mode = $('metroMode').value;
  if (mode === 'half') return beatPos[k] % 2 === 0;
  if (mode === 'bar') return beatPos[k] === 0;
  if (mode === 'bar2') return beatPos[k] === 0 && beatBar[k] % 2 === 0;
  return true;
}

// Высота дорожек: Alt + колесо (Ctrl + колесо -- масштаб по времени)
let trackHeight = 64;
function setTrackHeight(h, persist = true) {
  trackHeight = Math.round(Math.max(40, Math.min(220, h)));
  $('board').style.setProperty('--track-h', `${trackHeight}px`);
  sizeCanvases();
  if (persist) save();
}

function gridBeats(bpm) {
  if (!bpm || !duration) return [];
  const step = 60 / bpm;
  return Array.from({ length: Math.floor(duration / step) }, (_, i) => i * step);
}

// ---------------------------------------------------------------- разделение

// Смонтировано ли что-то: резка, сдвиги, обрезка, куски на чужих или
// своих дорожках. Тогда «Разделить» раскладывает склейку, а не исходник.
function edited() {
  return tracks.some((t) => t.clips.length !== 1 || t.custom || t.clips.some((c) => c.src.key !== t.key
    || Math.abs(c.offset) > 0.01 || c.trimStart > 0.01 || c.trimEnd < c.src.buffer.duration - 0.01));
}

let hasStems = false;
function refreshSplit() {
  if (!info || !info.services) return;
  const glued = tracks.length && edited();
  $('splitBox').hidden = hasStems && !glued;
  if ($('splitBox').hidden) return;
  $('splitTitle').textContent = glued ? 'Разложить склейку на дорожки' : 'Разложить трек на дорожки';
  $('splitText').textContent = glued
    ? 'Разложим то, что сейчас звучит на ленте, — вашу склейку: сохраним её отдельным треком «(склейка)» и разделим. Исходный трек останется как был.'
    : 'Вокал, гитара, бас, барабаны, клавиши — отдельно. Заглушите то, что сыграете вживую.';
}

function showSplit(splitting) {
  $('splitBox').hidden = false;
  const s = info.services.stems;
  const pro = info.services.stems_pro;
  const price = (x) => (info.unlimited ? '' : ` · ${x.pack} кредитов или ${x.price} ₽`);
  $('splitGo').textContent = `Разделить на дорожки${price(s)}`;
  $('splitPro').hidden = info.restyleEngine !== 'mureka';
  $('splitPro').textContent = `До 12 дорожек + MIDI${price(pro)}`;
  if (splitting) waitSplit(splitting);
  $('splitGo').onclick = () => split(false);
  $('splitPro').onclick = () => split(true);
}

async function split(pro) {
  if (edited()) { splitGlued(pro); return; }
  const form = new FormData();
  form.append('file', file);
  form.append('pro', pro);
  $('splitGo').disabled = true;
  $('splitPro').disabled = true;
  const response = await fetch(`/api/studio/${jobId}/stems`, { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    $('splitGo').disabled = false;
    $('splitPro').disabled = false;
    if (response.status === 402) {
      note(`${esc(data.detail || 'Не хватает на балансе')} <a href="/pricing?next=${encodeURIComponent(location.pathname + location.search)}">Пополнить</a>`);
    } else toast(data.detail || 'Не получилось запустить разделение');
    return;
  }
  waitSplit({ id: data.jobId, stage: 'В очереди' });
}

// Склейку -- отдельным треком «… (склейка)», и уже его -- на дорожки
async function splitGlued(pro) {
  if (!tracks.some((t) => audible(t) && t.clips.length)) { toast('Всё заглушено — раскладывать нечего'); return; }
  $('splitGo').disabled = true;
  $('splitPro').disabled = true;
  const fail = (text) => {
    note(esc(text));
    $('splitGo').disabled = false;
    $('splitPro').disabled = false;
  };
  try {
    note('Сводим склейку…');
    const rendered = await mixdown(0, duration).startRendering();
    const base = (job && job.name || 'Трек').replace(/\.[^.]+$/, '').replace(/ \(склейка\)$/, '');
    const form = new FormData();
    form.append('file', wav(rendered), `${base} (склейка).wav`);
    form.append('rights', ['own', 'cover'].includes(job && job.rights) ? job.rights : 'own');   // как у исходного трека
    note('Сохраняем склейку отдельным треком…');
    const up = await fetch('/api/studio/upload', { method: 'POST', body: form });
    const made = await up.json().catch(() => ({}));
    if (!up.ok) { fail(made.detail || 'Не получилось сохранить склейку'); return; }
    // Дождаться, пока сервер примет файл (перекодирует), -- потом разделять
    let glued = null;
    for (let tries = 0; tries < 120 && !glued; tries++) {
      await new Promise((r) => setTimeout(r, 2000));
      const fresh = await (await fetch('/api/studio')).json();
      const j = fresh.jobs.find((x) => x.id === made.jobId);
      if (j && j.status === 'error') { fail(`Склейка не сохранилась: ${j.error || ''}`); return; }
      if (j && j.status === 'done' && j.files.length) glued = j;
    }
    if (!glued) { fail('Сервер долго не отвечает — попробуйте ещё раз чуть позже'); return; }
    const ask = new FormData();
    ask.append('file', glued.files[0].name);
    ask.append('pro', pro);
    const response = await fetch(`/api/studio/${glued.id}/stems`, { method: 'POST', body: ask });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 402) {
        note(`${esc(data.detail || 'Не хватает на балансе')} Склейка сохранена в Студии — разделите её оттуда после пополнения. `
          + `<a href="/pricing?next=${encodeURIComponent(`/studio/mix/${glued.id}`)}">Пополнить</a>`);
        $('splitGo').disabled = false;
        $('splitPro').disabled = false;
        return;
      }
      fail(data.detail || 'Не получилось запустить разделение');
      return;
    }
    // Мультитрек склейки: там видно, как идёт разделение, и дорожки появятся сами
    location.href = `/studio/mix/${glued.id}?file=${encodeURIComponent(glued.files[0].name)}`;
  } catch (error) {
    fail('Не получилось свести склейку в этом браузере');
  }
}

async function waitSplit(splitting) {
  $('splitGo').disabled = true;
  $('splitPro').disabled = true;
  note(`Раскладываем на дорожки: ${esc(splitting.stage || 'в очереди')}… Страница обновится сама.`);
  for (;;) {
    await new Promise((r) => setTimeout(r, 5000));
    const fresh = await (await fetch('/api/studio')).json();
    const j = fresh.jobs.find((x) => x.id === splitting.id);
    if (!j) return;
    if (j.status === 'done') { location.reload(); return; }
    if (j.status === 'error') {
      note(`Не получилось разделить: ${esc(j.error || '')}. Деньги вернулись на баланс.`);
      $('splitGo').disabled = false;
      $('splitPro').disabled = false;
      return;
    }
    note(`Раскладываем на дорожки: ${esc(j.stage || 'в очереди')} · ${Math.round(j.progress || 0)}%… Страница обновится сама.`);
  }
}

// ---------------------------------------------------------------- дорожки

function render() {
  $('tracks').innerHTML = tracks.map((t, i) => `
    <div class="mx-row${i === selected ? ' picked' : ''}" data-i="${i}">
      <div class="mx-ctrl">
        <div class="mx-name" data-grab="${i}" title="Перетащите выше или ниже, чтобы поменять порядок">
          <span class="mx-grip" data-grip="${i}" aria-hidden="true">⠿</span><i style="background:${t.color}"></i><b data-noi18n data-label="${i}" title="${esc(t.name)} — нажмите, чтобы переименовать">${esc(t.name)}</b>${t.clips.length > 1 ? `<small class="mx-part" title="Кусков на дорожке">${t.clips.length}</small>` : ''}
          <button type="button" class="mx-rename" data-label="${i}" title="Название дорожки" aria-label="Название дорожки">${ICON.pencil}</button>
          ${rec && rec.lane === t ? '<em class="mx-recmark">● запись</em>'
    : t.uploading ? '<em title="Сохраняем при треке…">загрузка…</em>'
    : t.extra ? '<em title="Ваша дорожка — хранится при этом треке">своя</em>' : ''}</div>
        <div class="mx-btns">
          <button type="button" class="mx-ms" data-mute="${i}" aria-pressed="${t.mute}" title="Заглушить">M</button>
          <button type="button" class="mx-ms solo" data-solo="${i}" aria-pressed="${t.solo}" title="Только эта (соло)">S</button>
          <input type="range" class="mx-vol" data-vol="${i}" min="0" max="150" value="${t.vol}"
            aria-label="Громкость: ${esc(t.name)}" title="Громкость: ${t.vol}%">
          <button type="button" class="mx-ms mx-fxbtn" data-fx="${i}" aria-pressed="${Boolean(t.fx)}"
            title="Обработка: ${esc(FX[t.fx] ? FX[t.fx].title : FX[''].title)}">FX</button>
          <button type="button" class="mx-ms" data-lanemenu="${i}" title="Ещё: загрузить или скачать файл, убрать дорожку"
            aria-label="Ещё" aria-haspopup="menu">⋯</button>
        </div>
      </div>
      <div class="mx-lane" data-drop="${i}"><canvas class="mx-wave" data-wave="${i}" height="64"></canvas>
        ${t.clips.length || (rec && rec.lane === t) ? '' : `<span class="mx-empty"><button type="button" data-loadfile="${i}">⤒ Загрузить файл</button>
          <em>или перетащите сюда файл, кусок с другой дорожки, вставьте скопированное (Ctrl+V)</em></span>`}</div>
    </div>`).join('');
  applyGains();
  sizeCanvases();
  markSelected();
}

function sizeCanvases() {
  const dpr = window.devicePixelRatio || 1;
  document.querySelectorAll('.mx-lane canvas').forEach((c) => {
    c.width = Math.max(100, Math.round(c.clientWidth * dpr));
    c.height = Math.round((c.id === 'ruler' ? 60 : c.id === 'overview' ? 26 : Math.max(trackHeight, c.clientHeight)) * dpr);
  });
  redraw();
}

// ---------------------------------------------------------- порядок дорожек
// Мышью -- за название или ручку «⠿», пальцем -- только за ручку (иначе
// вертикальный свайп по названию перестал бы листать страницу).
let drag = null;
let lastDrag = 0;           // только что тянули -- щелчок по названию не открывает переименование

document.addEventListener('pointerdown', (e) => {
  const grab = e.target.closest('[data-grab]');
  if (!grab || e.button > 0 || e.target.closest('.mx-rename')) return;
  // Нажали на название и отпустили, не двигая, -- переименовать (dropTrack)
  const label = e.target.closest('b[data-label]');
  if (tracks.length < 2) return;        // одна дорожка: щелчок по названию -- обычный click
  if (e.pointerType !== 'mouse' && !e.target.closest('[data-grip]')) {
    // Пальцем по названию: подержать -- дорожка «поднимается» (отклик), можно нести
    if (e.pointerType === 'touch') holdToPick(e, grab);
    return;
  }
  startDrag(e, grab, label, false);
});

// Долгое нажатие на название дорожки (телефон): 380 мс без движения --
// дорожка поднимается с отжиком, дальше её несут пальцем. Сдвинули палец
// раньше -- это была прокрутка страницы, не трогаем.
function holdToPick(e, grab) {
  const x0 = e.clientX;
  const y0 = e.clientY;
  const id = e.pointerId;
  const cancel = () => { clearTimeout(timer); document.removeEventListener('pointermove', early); document.removeEventListener('pointerup', cancel); document.removeEventListener('pointercancel', cancel); };
  const early = (ev) => { if (ev.pointerId === id && Math.hypot(ev.clientX - x0, ev.clientY - y0) > 8) cancel(); };
  const timer = setTimeout(() => {
    cancel();
    startDrag(e, grab, null, true);
    haptic('pick');
    drag.row.classList.add('dragging');
    toast('Ведите пальцем вверх или вниз — дорожка встанет на новое место', 2500);
  }, 380);
  document.addEventListener('pointermove', early);
  document.addEventListener('pointerup', cancel);
  document.addEventListener('pointercancel', cancel);
}
// пока дорожку несут пальцем, страница не прокручивается
document.addEventListener('touchmove', (e) => { if (drag) e.preventDefault(); }, { passive: false });

function startDrag(e, grab, label, held) {
  const row = grab.closest('.mx-row');
  const rows = [...document.querySelectorAll('.mx-row[data-i]')];
  drag = { from: Number(grab.dataset.grab), to: Number(grab.dataset.grab), row, rows, y0: e.clientY,
    mids: rows.map((r) => { const b = r.getBoundingClientRect(); return b.top + b.height / 2; }),
    height: row.getBoundingClientRect().height, id: e.pointerId, moved: false, held,
    label: label ? Number(label.dataset.label) : null };
  try { grab.setPointerCapture(e.pointerId); } catch (error) { /* палец уже отпущен */ }
  if (!held) e.preventDefault();
}

document.addEventListener('pointermove', (e) => {
  if (!drag || e.pointerId !== drag.id) return;
  const dy = e.clientY - drag.y0;
  if (!drag.moved && Math.abs(dy) < 4) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.row.classList.add('dragging');
    $('board').classList.add('reordering');
    if (!drag.held) haptic('pick');
  }
  drag.row.style.transform = `translateY(${dy}px)`;
  // куда встанет: по серединам строк, свою не считаем
  const y = drag.mids[drag.from] + dy;
  let to = drag.from;
  drag.mids.forEach((mid, k) => {
    if (k < drag.from && y < mid) to = Math.min(to, k);
    if (k > drag.from && y > mid) to = Math.max(to, k);
  });
  if (to !== drag.to) haptic('tick');               // встала на новое место -- «щелчок»
  drag.to = to;
  drag.rows.forEach((r, k) => {
    if (k === drag.from) return;
    const shift = k > drag.from && k <= to ? -drag.height : k < drag.from && k >= to ? drag.height : 0;
    r.style.transform = shift ? `translateY(${shift}px)` : '';
  });
});

function dropTrack(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const { from, to, moved } = drag;
  const labelTap = drag.label;
  drag.rows.forEach((r) => { r.style.transform = ''; r.classList.remove('dragging'); });
  $('board').classList.remove('reordering');
  drag = null;
  if (moved) lastDrag = Date.now();
  if (!moved && e.type === 'pointerup' && labelTap !== null) openLabel(labelTap);
  if (moved) haptic('drop');
  if (!moved || from === to) return;
  const [t] = tracks.splice(from, 1);
  tracks.splice(to, 0, t);
  render();
  save();
}
document.addEventListener('pointerup', dropTrack);
document.addEventListener('pointercancel', dropTrack);

function audible(t) {
  const anySolo = tracks.some((x) => x.solo);
  return anySolo ? t.solo : !t.mute;
}

function applyGains() {
  tracks.forEach((t) => {
    const value = audible(t) ? t.vol / 100 : 0;
    t.gain.gain.setTargetAtTime(value, ctx.currentTime, 0.015);
  });
  document.querySelectorAll('.mx-row[data-i]').forEach((row) => {
    row.classList.toggle('silent', !audible(tracks[row.dataset.i]));
  });
}

function drawTrack(t, i) {
  const c = document.querySelector(`[data-wave="${i}"]`);
  if (!c || !duration) return;
  const g = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  const dpr = window.devicePixelRatio || 1;
  g.clearRect(0, 0, w, h);
  const x = (sec) => toX(sec, w);
  // такты -- тонкие линии, чтобы резать и склеивать ровно по сильной доле
  g.fillStyle = 'rgba(255,255,255,.06)';
  beats.forEach((b) => { if (downbeats.has(b.toFixed(2))) g.fillRect(Math.round(x(b)), 0, dpr, h); });
  const on = audible(t);
  const single = t.clips.length === 1;
  // выделенные на линейке места -- видны на выбранной дорожке: с ней их и правят
  const marks = () => shownRegions().forEach((r) => {
    g.fillStyle = i === selected ? 'rgba(255,236,206,.20)' : 'rgba(217,154,78,.06)';
    g.fillRect(x(r.a), 0, x(r.b) - x(r.a), h);
    if (i !== selected) return;
    g.fillStyle = 'rgba(246,197,132,.95)';
    g.fillRect(x(r.a), 0, 1.5 * dpr, h);
    g.fillRect(x(r.b) - 1.5 * dpr, 0, 1.5 * dpr, h);
  });
  t.clips.forEach((clip) => {
    const src = clip.src;
    const bars = src.peaks.length;
    const a = clipStart(clip);
    const b = clipEnd(clip);
    const mine = picks.has(clip);
    if (x(b) < 0 || x(a) > w) return;
    // кусок -- плашкой: видно, где он начинается и кончается
    g.fillStyle = mine ? 'rgba(217,154,78,.16)' : 'rgba(255,255,255,.035)';
    g.fillRect(x(a), 2 * dpr, x(b) - x(a), h - 4 * dpr);
    const step = Math.max(1, w / bars * (src.buffer.duration / viewLen()));
    for (let k = 0; k < bars; k++) {
      const sec = (k / bars) * src.buffer.duration;
      const at = clip.offset + sec;
      if (at < viewStart - 1 || at > viewStart + viewLen() + 1) continue;
      const inside = sec >= clip.trimStart && sec <= clip.trimEnd;
      // обрезанное -- бледно, только у единственного куска (видно, что можно вернуть)
      if (!inside && !(single && !montage)) continue;
      const amp = Math.max(1, src.peaks[k] * (h / 2 - 4 * dpr));
      g.fillStyle = !inside ? 'rgba(255,255,255,.10)' : on ? t.color : 'rgba(167,171,184,.35)';
      g.fillRect(x(at), h / 2 - amp, step, amp * 2);
    }
    if (single && !montage) {
      g.fillStyle = 'rgba(10,11,15,.55)';
      g.fillRect(0, 0, x(a), h);
      g.fillRect(x(b), 0, w - x(b), h);
    }
    // края -- ручки обрезки
    g.fillStyle = '#d99a4e';
    [a, b].forEach((edge) => g.fillRect(Math.round(x(edge)) - 2 * dpr, 8 * dpr, 4 * dpr, h - 16 * dpr));
    if (mine || (montage && t.clips.length > 1)) {
      g.strokeStyle = mine ? 'rgba(246,197,132,.95)' : 'rgba(255,255,255,.18)';
      g.lineWidth = (mine ? 2 : 1) * dpr;
      g.strokeRect(x(a) + dpr, 2 * dpr, x(b) - x(a) - 2 * dpr, h - 4 * dpr);
    }
  });
  marks();                                    // поверх волны -- видно, что именно выделено
  if (rec && rec.lane === t && rec.startTime !== undefined) drawTake(g, x, h, dpr);
}

// Идущий дубль: красная полоса от начала записи до курсора и живая волна
function drawTake(g, x, h, dpr) {
  const now = Math.max(rec.from, position());
  g.fillStyle = 'rgba(229,72,72,.16)';
  g.fillRect(x(rec.from), 2 * dpr, x(now) - x(rec.from), h - 4 * dpr);
  g.fillStyle = 'rgba(240,96,96,.9)';
  const sr = ctx.sampleRate;
  rec.peaks.forEach(({ at, v }) => {
    const sec = rec.startPos + (at / sr - rec.startTime) - rec.latency;
    if (sec < rec.from || sec > now) return;
    const amp = Math.max(dpr, Math.min(1, v) * (h / 2 - 4 * dpr));
    g.fillRect(x(sec), h / 2 - amp, Math.max(dpr, x(sec + 2048 / sr) - x(sec)), amp * 2);
  });
  g.fillRect(Math.round(x(rec.from)), 2 * dpr, 2 * dpr, h - 4 * dpr);
}

function drawRuler() {
  const c = $('ruler');
  if (!c || !duration) return;
  const g = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  const dpr = window.devicePixelRatio || 1;
  const x = (sec) => toX(sec, w);
  g.clearRect(0, 0, w, h);
  shownRegions().forEach((r) => {
    g.fillStyle = r === liveRegion ? 'rgba(217,154,78,.26)' : 'rgba(217,154,78,.14)';
    g.fillRect(x(r.a), 0, x(r.b) - x(r.a), h);
    g.fillStyle = 'rgba(217,154,78,.85)';
    g.fillRect(x(r.a), 0, dpr * 1.5, h);
    g.fillRect(x(r.b) - dpr * 1.5, 0, dpr * 1.5, h);
  });
  if (loop.on && loop.b > loop.a) {
    g.fillStyle = 'rgba(217,154,78,.18)';
    g.fillRect(x(loop.a), 0, x(loop.b) - x(loop.a), h);
  }
  // аккорды -- полосой снизу, названия там, где помещаются (или время трека)
  g.font = `${11 * dpr}px system-ui, sans-serif`;
  g.textBaseline = 'middle';
  if (rulerMode === 'time') {
    // шаг подписей -- чтобы между ними было не меньше 56 px
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];
    const step = steps.find((st) => (st / viewLen()) * w >= 56 * dpr) || 300;
    g.fillStyle = 'rgba(255,255,255,.06)';
    g.fillRect(0, h * 0.5, w, h * 0.5);
    for (let t = Math.floor(viewStart / step) * step; t <= viewStart + viewLen(); t += step) {
      const px = x(t);
      g.fillStyle = 'rgba(255,255,255,.35)';
      g.fillRect(Math.round(px), h * 0.5, dpr, h * 0.22);
      g.fillStyle = '#e8e3da';
      const label = step < 1 ? preciseClock(t).replace(/0$/, '') : clock(t);
      g.fillText(label, px + 3 * dpr, h * 0.78);
    }
  } else ((analysis && analysis.chords) || []).forEach(([a, b, name], k) => {
    g.fillStyle = k % 2 ? 'rgba(255,255,255,.05)' : 'rgba(255,255,255,.09)';
    if (x(b) < 0 || x(a) > w) return;
    g.fillRect(x(a), h * 0.5, x(b) - x(a) - dpr, h * 0.5);
    // начало аккорда левее окна -- подпись прижимается к краю экрана
    const left = Math.max(x(a), 0);
    if (x(b) - left > g.measureText(name).width + 6 * dpr) {
      g.fillStyle = '#e8e3da';
      g.fillText(name, left + 3 * dpr, h * 0.75);
    }
  });
  // такты -- номер над сильной долей (не гуще, чем раз в 36 px)
  g.fillStyle = 'rgba(255,255,255,.45)';
  let bar = 0;
  let lastX = -1e9;
  const strong = beats.filter((b) => downbeats.has(b.toFixed(2)));
  (strong.length ? strong : beats.filter((_, k) => k % 4 === 0)).forEach((b) => {
    bar += 1;
    const px = x(b);
    g.fillRect(Math.round(px), 0, dpr, h * 0.45);
    if (px - lastX > 36 * dpr) {
      g.fillText(String(bar), px + 3 * dpr, h * 0.22);
      lastX = px;
    }
  });
}

function redraw() {
  drawOverview();
  drawRuler();
  tracks.forEach(drawTrack);
  movePlayhead();
  $('zoomLabel').textContent = zoom > 1 ? `${Math.round(viewLen())} с` : 'вся песня';
}

function setZoom(next, around) {
  if (!duration) return;
  const center = around ?? viewStart + viewLen() / 2;
  const frac = (center - viewStart) / viewLen();
  zoom = Math.max(1, Math.min(48, next));
  viewStart = Math.max(0, Math.min(duration - viewLen(), center - frac * viewLen()));
  redraw();
}

// Крупно, как в Chord AI: что играть сейчас и что дальше
function chordNow() {
  const list = (analysis && analysis.chords) || [];
  const now = position();
  const k = list.findIndex(([a, b]) => now >= a && now < b);
  const cur = k >= 0 ? list[k] : null;
  let next = null;
  for (let j = k >= 0 ? k + 1 : list.findIndex(([a]) => a > now); j >= 0 && j < list.length; j++) {
    if (!cur || list[j][2] !== cur[2]) { next = list[j]; break; }
  }
  $('chordNow').textContent = cur ? cur[2] : list.length ? '—' : '…';
  $('chordNext').textContent = next ? next[2] : '';
  $('chordNextBox').hidden = !next;
}

function movePlayhead() {
  const lane = document.querySelector('.mx-ruler-row .mx-lane');
  if (!lane || !duration) return;
  const board = $('board').getBoundingClientRect();
  const box = lane.getBoundingClientRect();
  const frac = (position() - viewStart) / viewLen();
  // при игре окно едет за курсором
  // при игре окно едет за курсором. Только если оно правда сдвинулось:
  // в конце песни окну некуда ехать, и redraw -> movePlayhead -> redraw
  // крутился бы без конца (курсор вставал намертво)
  if (playing && zoom > 1 && (frac > 0.85 || frac < 0) && Date.now() > followPause) {
    const next = Math.max(0, Math.min(duration - viewLen(), position() - viewLen() * 0.1));
    if (Math.abs(next - viewStart) > 0.01) {
      viewStart = next;
      redraw();
      return;
    }
  }
  $('playhead').style.display = frac < 0 || frac > 1 ? 'none' : '';
  $('playhead').style.top = `${document.querySelector('.mx-ruler-row').offsetTop}px`;
  const left = box.left - board.left + frac * box.width;
  $('playhead').style.transform = `translateX(${left}px)`;
  chordNow();
  if (playing) drawOverview();
  $('now').textContent = clock(position());
  pulse();
}

// Кнопка «Играть» дышит в такт песне: вспышка на каждую долю, ярче -- на первую
let pulsed = -1;
function pulse() {
  if (!playing || !beats.length) return;
  const now = position();
  let k = pulsed >= 0 && pulsed < beats.length && beats[pulsed] <= now ? pulsed : 0;
  while (k + 1 < beats.length && beats[k + 1] <= now) k++;
  if (k === pulsed || beats[k] > now || now - beats[k] > 0.25) { pulsed = k; return; }
  pulsed = k;
  const button = $('play');
  button.classList.remove('beat', 'down');
  void button.offsetWidth;                  // перезапустить анимацию
  button.classList.add('beat');
  if (beatPos[k] === 0) button.classList.add('down');
}

// ---------------------------------------------------------------- звук

function position() {
  // во время такта отсчёта музыка ещё не пошла -- позиция стоит на месте
  return playing ? Math.min(duration, startPos + Math.max(0, ctx.currentTime - startTime)) : pos;
}

function stopSources() {
  sources.forEach((s) => { try { s.stop(); } catch (error) { /* уже остановлен */ } });
  sources = [];
}

// Запуск всех дорожек с позиции p от одного момента часов: через delay
// секунд (отсчёт) или почти сразу.
function startAt(p, delay = 0.06) {
  stopSources();
  const t0 = ctx.currentTime + delay;
  tracks.forEach((t) => t.clips.forEach((clip) => {
    const src = schedule(ctx, clip, p, t0, t.gain);
    if (src) sources.push(src);
  }));
  startTime = t0;
  startPos = p;
  playing = true;
  if (liveBus) duckFrom(liveBus.duck.gain, p, t0);
  nextBeat = beats.findIndex((b) => b >= p - 0.001);
  if (nextBeat < 0) nextBeat = beats.length;
  $('play').innerHTML = ICON.pause;
  $('play').classList.add('on');
}

// Кусок дорожки на ленте -- с позиции p ленты, в момент t0 часов ctx
// (и для живого звука, и для сведения в файл). end -- где лента кончается.
function schedule(c, clip, p, t0, dest, end = Infinity) {
  const buffer = clip.src.buffer;
  const from = Math.max(p - clip.offset, clip.trimStart);               // секунда файла
  const to = Math.min(clip.trimEnd, buffer.duration, end - clip.offset);
  if (to - from <= 0.01) return null;
  const src = c.createBufferSource();
  src.buffer = buffer;
  src.connect(dest);
  src.start(t0 + (clip.offset + from - p), from, to - from);
  return src;
}

function pause() {
  if (rec && !rec.stopping) { stopRecording(); return; }
  pos = position();
  playing = false;
  stopSources();
  if (liveBus) duckFrom(liveBus.duck.gain, 0, 0, false);
  $('play').innerHTML = ICON.play;
  $('play').classList.remove('on');
  movePlayhead();
}

async function play() {
  if (!tracks.length) return;
  await ctx.resume();
  if (playing) { pause(); return; }
  let from = pos >= duration - 0.05 ? 0 : pos;
  if (loop.on && (from < loop.a || from >= loop.b)) from = loop.a;
  const interval = analysis && analysis.bpm ? 60 / analysis.bpm : 0;
  if (countIn && interval) {
    // Такт отсчёта: четыре щелчка, на пятый -- музыка
    const t = ctx.currentTime + 0.1;
    for (let k = 0; k < 4; k++) click(t + k * interval, k === 0);
    startAt(from, 0.1 + 4 * interval);
  } else startAt(from);
}

function seek(sec) {
  if (rec) { stopRecording().then(() => seek(sec)); return; }
  sec = Math.max(0, Math.min(duration, sec));
  if (playing) startAt(sec);
  else pos = sec;
  movePlayhead();
}

function click(when, accent) {
  window.metroSound.play(ctx, metroGain, when, accent);
}

// Звук метронома: выбрали -- сразу слышно, как он звучит
window.metroSound.bindSelect($('metroSound'), () => {
  audio();
  ctx.resume();
  click(ctx.currentTime + 0.03, true);
  click(ctx.currentTime + 0.33, false);
});

// Планировщик: щелчки ставятся на доли песни чуть заранее (lookahead),
// повтор куска и конец трека -- здесь же. setInterval, а не кадры анимации:
// в фоновой вкладке кадры встают, а метроном и повтор должны идти.
setInterval(() => {
  if (!playing || !ctx) return;
  const now = position();
  if (metroOn) {
    while (nextBeat < beats.length && beats[nextBeat] < now + 0.15) {
      const b = beats[nextBeat];
      if (b >= startPos - 0.001 && (!loop.on || b < loop.b) && metroKeeps(nextBeat)) {
        click(startTime + (b - startPos), beatPos[nextBeat] === 0);
      }
      nextBeat += 1;
    }
  }
  if (loop.on && !rec && loop.b > loop.a && now >= loop.b) startAt(loop.a, 0.02);
  else if (now >= duration) { pause(); pos = 0; viewStart = 0; redraw(); }
}, 25);

(function frame() {
  // следующий кадр -- при любой ошибке, иначе курсор замирает до перезагрузки
  requestAnimationFrame(frame);
  if (playing) movePlayhead();
  if (rec) {
    const i = tracks.indexOf(rec.lane);
    if (i >= 0) drawTrack(rec.lane, i);
    $('recTime').textContent = clock(Math.max(0, position() - rec.from));
  }
  if (micMeter) micMeter();
}());

// ---------------------------------------------------------------- мышь

function secAt(e, el) {
  const box = el.getBoundingClientRect();
  return Math.max(0, Math.min(duration, viewStart + Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * viewLen()));
}

// Курсор (белая полоса) можно взять мышью и перетащить в нужное место
$('playhead').addEventListener('pointerdown', (e) => {
  if (!duration) return;
  e.preventDefault();
  e.stopPropagation();
  const lane = document.querySelector('.mx-ruler-row .mx-lane');
  const wasPlaying = playing;
  if (playing) pause();
  $('playhead').setPointerCapture(e.pointerId);
  $('playhead').classList.add('drag');
  const move = (ev) => { pos = secAt(ev, lane); movePlayhead(); };
  const up = () => {
    $('playhead').removeEventListener('pointermove', move);
    $('playhead').removeEventListener('pointerup', up);
    $('playhead').classList.remove('drag');
    if (wasPlaying) play();
  };
  $('playhead').addEventListener('pointermove', move);
  $('playhead').addEventListener('pointerup', up);
});

$('metroMode').addEventListener('change', () => {
  if (playing) nextBeat = beats.findIndex((b) => b >= position());
  save();
});

// Касания и мышь. Дорожка: тап/клик -- перемотка, протяжка -- сдвиг окна
// (как карта), у золотых краёв -- обрезка. Линейка: мышью протяжка --
// кусок для повтора; пальцем протяжка -- курсор, долгое нажатие и
// протяжка -- повтор. Два пальца -- масштаб. Пока человек двигает окно,
// оно не убегает за курсором.
let followPause = 0;
const touches = new Map();
let pinch = null;

document.addEventListener('pointerdown', (e) => {
  if (e.pointerType === 'touch' && e.target.closest('#board')) {
    touches.set(e.pointerId, e.clientX);
    if (touches.size === 2) {
      const [a, b] = [...touches.values()];
      const lane = document.querySelector('.mx-ruler-row .mx-lane').getBoundingClientRect();
      pinch = { dist: Math.abs(a - b) || 1, zoom, at: viewStart + ((a + b) / 2 - lane.left) / lane.width * viewLen() };
      return;
    }
  }
  const wave = e.target.closest('[data-wave]');
  if (wave && duration) {
    let index = Number(wave.dataset.wave);
    const lane = tracks[index];
    const sec = secAt(e, wave);
    const width = wave.getBoundingClientRect().width;
    const px = (x) => toX(x, width);
    const reach = e.pointerType === 'touch' ? 18 : 10;
    // кусок под пальцем: сначала края (обрезка), потом тело (сдвиг в «Монтаже»)
    let clip = null;
    let grab = null;
    for (const c of lane.clips) {
      if (Math.abs(px(sec) - px(clipStart(c))) < reach) { clip = c; grab = 'trimStart'; break; }
      if (Math.abs(px(sec) - px(clipEnd(c))) < reach) { clip = c; grab = 'trimEnd'; break; }
    }
    if (!clip) {
      clip = clipAt(lane, sec) || null;
      if (clip && montage) grab = 'move';
    }
    const add = e.ctrlKey || e.metaKey || e.shiftKey;
    if (add && clip) {                         // Ctrl/Shift + щелчок -- добавить кусок к выбранным
      choose(index, clip, true);
      setMontage(true, true);                  // выбранное -- сразу можно тянуть
      return;
    }
    if (selected !== index || !picks.has(clip)) choose(index, clip);
    else pick = clip;
    wave.setPointerCapture(e.pointerId);
    const x0 = e.clientX;
    const view0 = viewStart;
    const offset0 = clip ? clip.offset : 0;
    // тянут один из выбранных -- едут все выбранные
    const group = clip && picks.size > 1 && picks.has(clip) ? [...picks].map((c) => [c, c.offset]) : null;
    const rows = [...document.querySelectorAll('.mx-row[data-i]')].map((r) => r.getBoundingClientRect());
    let moved = false;
    let wasSnapped = false;
    const move = (ev) => {
      if (pinch) return;
      if (grab === 'move') {
        if (!moved && Math.abs(ev.clientX - x0) < 4 && Math.abs(ev.clientY - e.clientY) < 6) return;
        if (!moved) { remember(); moved = true; haptic('pick'); }
        const shift = (ev.clientX - x0) / width * viewLen();
        const raw = Math.max(-clip.trimStart, offset0 + shift);
        clip.offset = snapOffset(clip, raw, width);
        const snapped = Math.abs(clip.offset - raw) > 1e-6;          // прилип к такту или соседу -- «щелчок»
        if (snapped && !wasSnapped) haptic('tick');
        wasSnapped = snapped;
        if (group) {
          // все выбранные -- на тот же сдвиг, что и тот, за который тянут (с его прилипанием)
          const delta = clip.offset - offset0;
          group.forEach(([c, o]) => { c.offset = Math.max(-c.trimStart, o + delta); });
          updateDuration();
          redraw();
          return;
        }
        // на другую дорожку -- по строке под пальцем
        const to = rows.findIndex((r) => ev.clientY >= r.top && ev.clientY < r.bottom);
        if (to >= 0 && to !== index) {
          tracks[index].clips.splice(tracks[index].clips.indexOf(clip), 1);
          tracks[to].clips.push(clip);
          index = to;
          selected = to;
          haptic('tick');
          markSelected();
          document.querySelectorAll('.mx-empty').forEach((b) => { b.hidden = true; });
        }
        updateDuration();
        redraw();
        return;
      }
      if (grab) {
        if (!moved) { remember(); moved = true; }
        const x = Math.max(0, secAt(ev, wave) - clip.offset);
        if (grab === 'trimStart') clip.trimStart = Math.max(0, Math.min(x, clip.trimEnd - 0.1));
        else clip.trimEnd = Math.min(clip.src.buffer.duration, Math.max(x, clip.trimStart + 0.1));
        drawTrack(lane, index);
        return;
      }
      if (Math.abs(ev.clientX - x0) < 6 && !moved) return;
      moved = true;
      followPause = Date.now() + 4000;
      if (zoom > 1) {
        viewStart = Math.max(0, Math.min(duration - viewLen(), view0 - (ev.clientX - x0) / width * viewLen()));
        redraw();
      }
    };
    const up = (ev) => {
      wave.removeEventListener('pointermove', move);
      wave.removeEventListener('pointerup', up);
      wave.removeEventListener('pointercancel', up);
      if (grab && moved) {
        haptic('drop');
        tracks.forEach((t) => t.clips.sort((a, b) => clipStart(a) - clipStart(b)));
        render();
        save();
        if (playing) startAt(position());
        return;
      }
      if (!moved && !pinch && ev.type === 'pointerup') seek(sec);
    };
    wave.addEventListener('pointermove', move);
    wave.addEventListener('pointerup', up);
    wave.addEventListener('pointercancel', up);
    return;
  }
  const ruler = e.target.closest('#ruler');
  if (ruler && duration) {
    const rulerWidth = ruler.getBoundingClientRect().width;
    const from = snapSec(secAt(e, ruler), rulerWidth, e.altKey);
    const adding = e.ctrlKey || e.metaKey || e.shiftKey;     // с Ctrl -- ещё одно место к выделенным
    rulerDragging = true;
    ruler.setPointerCapture(e.pointerId);
    const finger = e.pointerType === 'touch';
    // пальцем -- повтор только после долгого нажатия, иначе протяжка двигает курсор
    let selecting = !finger;
    let dragged = false;
    const hold = finger ? setTimeout(() => {
      if (!dragged) { selecting = true; haptic('pick'); toast('Ведите пальцем — выделяем кусок'); }
    }, 450) : null;
    const move = (ev) => {
      if (pinch) return;
      const to = snapSec(secAt(ev, ruler), rulerWidth, ev.altKey);
      if (ev.pointerType !== 'touch') showHair(to);
      if (Math.abs(to - from) < viewLen() * 0.004) return;
      dragged = true;
      followPause = Date.now() + 4000;
      if (selecting) {
        loop.a = Math.min(from, to);
        loop.b = Math.max(from, to);
        liveRegion = { a: loop.a, b: loop.b };     // видно сразу -- на линейке и на дорожках
        liveAdding = adding;
        redraw();
      } else {
        clearTimeout(hold);
        pos = to;
        if (playing) startAt(to);
        movePlayhead();
      }
    };
    const up = (ev) => {
      clearTimeout(hold);
      rulerDragging = false;
      liveRegion = null;
      ruler.removeEventListener('pointermove', move);
      ruler.removeEventListener('pointerup', up);
      ruler.removeEventListener('pointercancel', up);
      if (ev.type !== 'pointerup' || pinch) return;
      if (!dragged) { seek(from); return; }
      if (!selecting) return;
      // Выделение -- только выделение: копировать его или включить «Повтор»
      // решает человек (раньше повтор включался сам, мешал копированию)
      // края уже стоят там, где показывал прицел (snapSec) -- не двигаем их
      if (loop.b - loop.a < 0.05) loop.b = loop.a + 0.05;
      const fresh = { a: loop.a, b: loop.b };
      regions = adding ? regions.filter((r) => r.b <= fresh.a || r.a >= fresh.b).concat([fresh]) : [fresh];
      regions.sort((x, y) => x.a - y.a);
      markSelected();
      redraw();
      toast(regions.length > 1
        ? `Выделено мест: ${regions.length} — «Копировать», «Вырезать» или «Удалить» на выбранной дорожке`
        : `Выделено ${clock(loop.a)} – ${clock(loop.b)}: «Копировать» (Ctrl+C) или «Повтор» — играть по кругу. С Ctrl — ещё места`);
    };
    ruler.addEventListener('pointermove', move);
    ruler.addEventListener('pointerup', up);
    ruler.addEventListener('pointercancel', up);
    return;
  }
  const overview = e.target.closest('#overview');
  if (overview && duration) {
    overview.setPointerCapture(e.pointerId);
    const jump = (ev) => {
      const box = overview.getBoundingClientRect();
      const at = Math.max(0, Math.min(1, (ev.clientX - box.left) / box.width)) * duration;
      viewStart = Math.max(0, Math.min(duration - viewLen(), at - viewLen() / 2));
      followPause = Date.now() + 2500;
      seek(at);
      redraw();
    };
    jump(e);
    const up = () => {
      overview.removeEventListener('pointermove', jump);
      overview.removeEventListener('pointerup', up);
    };
    overview.addEventListener('pointermove', jump);
    overview.addEventListener('pointerup', up);
  }
});

document.addEventListener('pointermove', (e) => {
  if (!pinch || !touches.has(e.pointerId)) return;
  touches.set(e.pointerId, e.clientX);
  const [a, b] = [...touches.values()];
  followPause = Date.now() + 4000;
  setZoom(pinch.zoom * (Math.abs(a - b) || 1) / pinch.dist, pinch.at);
});
['pointerup', 'pointercancel'].forEach((type) => document.addEventListener(type, (e) => {
  touches.delete(e.pointerId);
  if (touches.size < 2) setTimeout(() => { if (touches.size < 2) pinch = null; }, 0);
}));

// Обзор: вся песня мелко, рамка -- то, что видно сейчас
function drawOverview() {
  const c = $('overview');
  if (!c || !duration) return;
  const g = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  g.clearRect(0, 0, w, h);
  const n = 400;
  g.fillStyle = 'rgba(167,171,184,.45)';
  for (let k = 0; k < n; k++) {
    let peak = 0;
    tracks.forEach((t) => t.clips.forEach((clip) => {
      const sec = (k / n) * duration - clip.offset;
      if (sec < clip.trimStart || sec > clip.trimEnd) return;
      const i = Math.floor((sec / clip.src.buffer.duration) * clip.src.peaks.length);
      if (i >= 0 && i < clip.src.peaks.length) peak = Math.max(peak, clip.src.peaks[i]);
    }));
    const amp = Math.max(1, peak * (h / 2 - 2));
    g.fillRect((k / n) * w, h / 2 - amp, Math.max(1, w / n - 1), amp * 2);
  }
  g.fillStyle = 'rgba(217,154,78,.25)';
  shownRegions().forEach((r) => g.fillRect((r.a / duration) * w, 0, ((r.b - r.a) / duration) * w, h));
  g.strokeStyle = '#d99a4e';
  g.lineWidth = Math.max(1, (window.devicePixelRatio || 1) * 1.5);
  g.strokeRect((viewStart / duration) * w + 1, 1, Math.max(4, (viewLen() / duration) * w - 2), h - 2);
  g.fillStyle = '#fff';
  g.fillRect((position() / duration) * w - 1, 0, 2, h);
}

function setRulerMode(mode, persist = true) {
  rulerMode = mode;
  document.querySelectorAll('[data-rulermode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.rulermode === mode)));
  drawRuler();
  if (persist) save();
}
document.querySelectorAll('[data-rulermode]').forEach((b) => b.addEventListener('click', () => setRulerMode(b.dataset.rulermode)));

// Прицел на линейке: точное место под мышью. К доле прилипает, только
// когда она совсем рядом (8 пикселей), с Alt -- не прилипает вовсе: так
// можно выделить ровно по доле или ровно там, где нужно между долями.
function snapSec(sec, width, free) {
  if (free || !beats.length) return sec;
  const limit = (8 / width) * viewLen();
  let best = sec;
  let dist = limit;
  for (const b of beats) {
    const d = Math.abs(b - sec);
    if (d < dist) { dist = d; best = b; }
    if (b > sec + limit) break;
  }
  return best;
}

function barBeat(sec) {
  if (!beats.length || sec < beats[0]) return '';
  let k = 0;
  while (k + 1 < beats.length && beats[k + 1] <= sec + 1e-6) k++;
  const onBeat = Math.abs(beats[k] - sec) < 0.005;
  return ` · такт ${(beatBar[k] ?? 0) + 1}, доля ${(beatPos[k] ?? 0) + 1}${onBeat ? '' : '+'}`;
}

const preciseClock = (sec) => {
  const s = Math.max(0, sec);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, '0')}`;
};

function showHair(sec) {
  const hair = $('hair');
  const lane = document.querySelector('.mx-ruler-row .mx-lane');
  if (!lane || !duration || sec < viewStart || sec > viewStart + viewLen()) { hair.hidden = true; return; }
  const board = $('board').getBoundingClientRect();
  const box = lane.getBoundingClientRect();
  hair.hidden = false;
  hair.style.top = `${document.querySelector('.mx-ruler-row').offsetTop}px`;
  hair.style.transform = `translateX(${box.left - board.left + ((sec - viewStart) / viewLen()) * box.width}px)`;
  $('hairLabel').textContent = preciseClock(sec) + barBeat(sec);
  // подпись -- не за краем доски
  hair.classList.toggle('flip', box.left - board.left + ((sec - viewStart) / viewLen()) * box.width > board.width - 190);
}

document.addEventListener('pointermove', (e) => {
  const ruler = e.target.closest && e.target.closest('#ruler');
  if (!ruler || e.buttons || e.pointerType === 'touch') return;
  showHair(snapSec(secAt(e, ruler), ruler.getBoundingClientRect().width, e.altKey));
});
$('ruler').addEventListener('pointerleave', () => { if (!rulerDragging) $('hair').hidden = true; });
let rulerDragging = false;

// Края повтора -- к ближайшим долям: кусок звучит ровно, без рваного начала
function snapLoop() {
  if (!beats.length) return;
  const near = (s) => beats.reduce((best, b) => (Math.abs(b - s) < Math.abs(best - s) ? b : best), beats[0]);
  loop.a = near(loop.a);
  loop.b = Math.max(near(loop.b), loop.a + 0.5);
}

document.addEventListener('click', (e) => {
  // щелчок по пульту дорожки (мимо кнопок) -- выбрать её: туда и вставится скопированное
  const head = e.target.closest('.mx-row[data-i] .mx-ctrl');
  if (head && !e.target.closest('button, a, input, b[data-label], [data-grip]')) {
    choose(Number(head.closest('.mx-row').dataset.i), null);
    return;
  }
  const label = e.target.closest('.mx-rename[data-label]') || (tracks.length < 2 && e.target.closest('b[data-label]'));
  if (label && Date.now() - lastDrag > 400) { openLabel(Number(label.dataset.label)); return; }
  const mute = e.target.closest('[data-mute]');
  const solo = e.target.closest('[data-solo]');
  const remove = e.target.closest('[data-remove]');
  if (mute) { const t = tracks[mute.dataset.mute]; t.mute = !t.mute; mute.setAttribute('aria-pressed', t.mute); haptic('tick'); }
  if (solo) { const t = tracks[solo.dataset.solo]; t.solo = !t.solo; solo.setAttribute('aria-pressed', t.solo); haptic('tick'); }
  const loadFile = e.target.closest('[data-loadfile]');
  if (loadFile) {
    choose(Number(loadFile.dataset.loadfile), null);
    $('addFile').click();
    return;
  }
  if (remove) {
    if (tracks.length === 1) { toast('Последнюю дорожку убрать нельзя'); return; }
    const i = Number(remove.dataset.remove);
    const doomed = tracks[i];
    const used = (key) => tracks.some((t) => t !== doomed && t.clips.some((c) => c.src.key === key));
    const ownExtra = doomed.extra && !used(doomed.key);
    if (ownExtra && !confirm(`Удалить дорожку «${doomed.name}»? Её придётся загружать заново.`)) return;
    remember();
    tracks.splice(i, 1);
    doomed.gain.disconnect();
    if (doomed.fxNodes) doomed.fxNodes.output.disconnect();
    selected = -1;
    pick = null;
    picks = new Set();
    if (ownExtra) {
      undoStack.length = 0;                       // файл удалён на сервере -- вернуть нечем
      $('undoBtn').disabled = true;
      fetch(`/api/studio/${jobId}/extra/${doomed.extra}`, { method: 'DELETE' })
        .then((r) => toast(r.ok ? 'Дорожка удалена' : 'Не получилось удалить дорожку на сервере'));
    } else if (doomed.url && !doomed.extra && !used(doomed.key)) {
      hidden.add(doomed.key);
      showHidden();
    }
    save();
    updateDuration();
    render();
    if (playing) startAt(Math.min(position(), duration));
    return;
  }
  if (mute || solo) { applyGains(); tracks.forEach(drawTrack); save(); }
});

document.addEventListener('input', (e) => {
  const vol = e.target.closest('[data-vol]');
  if (vol) { tracks[vol.dataset.vol].vol = Number(vol.value); vol.title = `Громкость: ${vol.value}%`; applyGains(); save(); }
});

function pressed(id, on) {
  $(id).setAttribute('aria-pressed', String(on));
}

$('zoomIn').addEventListener('click', () => setZoom(zoom * 2, playing ? position() : undefined));
$('zoomOut').addEventListener('click', () => setZoom(zoom / 2, playing ? position() : undefined));
$('board').addEventListener('wheel', (e) => {
  if (!duration) return;
  const lane = document.querySelector('.mx-ruler-row .mx-lane').getBoundingClientRect();
  if (e.altKey) {
    e.preventDefault();
    setTrackHeight(trackHeight * (e.deltaY < 0 ? 1.15 : 0.87));
  } else if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    const at = viewStart + Math.max(0, Math.min(1, (e.clientX - lane.left) / lane.width)) * viewLen();
    setZoom(zoom * (e.deltaY < 0 ? 1.25 : 0.8), at);
  } else if ((e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) && zoom > 1) {
    e.preventDefault();
    const dx = e.deltaX || e.deltaY;
    viewStart = Math.max(0, Math.min(duration - viewLen(), viewStart + (dx / lane.width) * viewLen()));
    redraw();
  }
}, { passive: false });

$('play').addEventListener('click', play);
$('toStart').addEventListener('click', () => seek(loop.on ? loop.a : 0));
$('metro').addEventListener('click', () => {
  metroOn = !metroOn;
  pressed('metro', metroOn);
  if (metroOn && !beats.length) toast('Доли ещё считаем — метроном включится через пару секунд');
  if (playing) nextBeat = beats.findIndex((b) => b >= position());
  save();
});
$('countIn').addEventListener('click', () => { countIn = !countIn; pressed('countIn', countIn); save(); });
$('metroVol').addEventListener('input', () => {
  if (metroGain) metroGain.setVolume($('metroVol').value);
  $('metroVol').title = `Громкость метронома: ${$('metroVol').value}%`;
  save();
});
$('loop').addEventListener('click', () => {
  if (!(loop.b > loop.a)) { toast('Выделите кусок мышью на линейке с тактами над дорожками'); return; }
  if (!loop.on && regions.length > 1) toast(`Повтор — последнего выделенного места: ${clock(loop.a)} – ${clock(loop.b)}`);
  loop.on = !loop.on;
  pressed('loop', loop.on);
  drawRuler();
});
// ---------------------------------------------------------------- свой темп
// Автомат путает соседние пульсации на «качающихся» песнях (81 -> 108).
// Музыкант вписывает темп или отстукивает его под песню; «раз» ставит
// сильную долю. Всё -- в единицах открытой версии (сдвинутая по темпу
// версия пересчитывается к оригиналу при сохранении).
const tempo = { bpm: 0, start: 0, taps: [] };

function tempoNote(text) { $('tempoNote').textContent = text; }

$('tempoBtn').addEventListener('click', () => {
  if (!analysis || !analysisSrc) {
    toast($('factBpm').textContent === '…' ? 'Темп ещё определяется — секунду' : 'Не получилось разобрать запись');
    return;
  }
  tempo.bpm = analysis.bpm || 120;
  tempo.start = (analysis.downbeats && analysis.downbeats[0]) || (beats[0] || 0);
  tempo.taps = [];
  $('tempoVal').value = tempo.bpm;
  $('tempoReset').hidden = !analysis.fixed;
  const by = analysis.tempoBy === 'net' ? 'нейросеть долей' : 'простой алгоритм';
  tempoNote(analysis.fixed ? `Сейчас ваш темп. Автомат (${by}) определил ${analysis.autoBpm || '—'} BPM.`
    : `Темп определил${analysis.tempoBy === 'net' ? 'а' : ''} ${by}. Не уверены? Включите песню и нажимайте «Отстучать» на каждую долю.`);
  $('tempoPlay').textContent = playing ? '❚❚ Пауза' : '▶ Играть';
  $('tempoModal').hidden = false;
});

function setTempo(bpm) {
  tempo.bpm = Math.round(Math.max(30, Math.min(300, bpm)) * 2) / 2;
  $('tempoVal').value = tempo.bpm;
}
$('tempoVal').addEventListener('input', () => { tempo.bpm = Number($('tempoVal').value) || 0; });
$('tempoHalf').addEventListener('click', () => setTempo((Number($('tempoVal').value) || tempo.bpm) / 2));
$('tempoDouble').addEventListener('click', () => setTempo((Number($('tempoVal').value) || tempo.bpm) * 2));

function tap() {
  const now = performance.now();
  if (tempo.taps.length && now - tempo.taps[tempo.taps.length - 1].at > 2500) tempo.taps = [];
  tempo.taps.push({ at: now, pos: playing ? position() : null });
  const taps = tempo.taps.slice(-12);
  $('tempoTap').classList.add('hit');
  setTimeout(() => $('tempoTap').classList.remove('hit'), 90);
  if (taps.length < 2) { $('tapInfo').textContent = 'ещё…'; return; }
  const step = (taps[taps.length - 1].at - taps[0].at) / (taps.length - 1);
  setTempo(60000 / step);
  // Доли встают туда, куда вы стучите; «раз» -- на первый удар серии
  if (taps[0].pos !== null) tempo.start = taps[0].pos;
  $('tapInfo').textContent = taps.length < 4 ? `${taps.length} удара — ещё пару` : `${taps.length} ударов · ${tempo.bpm} BPM`;
}
$('tempoTap').addEventListener('pointerdown', (e) => { e.preventDefault(); tap(); });
$('tempoTap').addEventListener('keydown', (e) => { if (e.code === 'Enter') { e.preventDefault(); tap(); } });

$('tempoPlay').addEventListener('click', async () => {
  await play();
  $('tempoPlay').textContent = playing ? '❚❚ Пауза' : '▶ Играть';
});
$('tempoOne').addEventListener('click', () => {
  tempo.start = position();
  tempoNote(`«Раз» такта — на ${clock(tempo.start)}`);
});

function closeTempo() { $('tempoModal').hidden = true; }
$('tempoCancel').addEventListener('click', closeTempo);
$('tempoModal').addEventListener('click', (e) => { if (e.target === $('tempoModal')) closeTempo(); });

async function saveTempo(bpm) {
  const rate = (shifted && shifted.shift.tempo) || 1;
  const form = new FormData();
  form.append('file', analysisSrc.name);
  form.append('bpm', bpm ? String(bpm / rate) : '0');
  form.append('start', String(tempo.start * rate));
  const answer = await fetch(`/api/studio/${analysisSrc.owner}/tempo`, { method: 'POST', body: form });
  const data = await answer.json().catch(() => ({}));
  if (!answer.ok) { toast(data.detail || 'Не получилось сохранить темп'); return; }
  closeTempo();
  await loadAnalysis(analysisSrc.owner, analysisSrc.name);
  toast(bpm ? `Темп ${analysis && analysis.bpm} BPM сохранён — метроном идёт по нему`
    : 'Вернули автоматический темп');
}
$('tempoOk').addEventListener('click', () => {
  const bpm = Number($('tempoVal').value);
  if (!(bpm >= 30 && bpm <= 300)) { tempoNote('Темп — от 30 до 300 ударов в минуту'); return; }
  saveTempo(bpm);
});
$('tempoReset').addEventListener('click', () => saveTempo(0));

document.addEventListener('keydown', (e) => {
  if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && e.target.type !== 'range') return;
  if (document.querySelector('.mx-sheet:not([hidden])')) return;
  const mod = e.ctrlKey || e.metaKey;
  if (e.code === 'Space') { e.preventDefault(); play(); return; }
  if (mod && e.code === 'KeyC') { if (copy()) e.preventDefault(); return; }
  if (mod && e.code === 'KeyV') { e.preventDefault(); paste(); return; }
  if (mod && e.code === 'KeyZ') { e.preventDefault(); undo(); return; }
  if (mod && e.code === 'KeyX') { e.preventDefault(); cutOut(); return; }
  if (e.code === 'Escape') { deselect(); return; }
  if ((e.code === 'Delete' || e.code === 'Backspace') && (picks.size || regions.length)) { e.preventDefault(); deleteClip(); return; }
  if (e.code === 'KeyS' && !mod && tracks[selected]) { e.preventDefault(); cut(); }
  if (e.code === 'KeyR' && !mod) { e.preventDefault(); recButton(); }
});
window.addEventListener('resize', () => { clearTimeout(sizeCanvases.t); sizeCanvases.t = setTimeout(sizeCanvases, 120); });

// ---------------------------------------------------------------- своя дорожка и микс

// «＋ Дорожка» -- пустая дорожка: в неё загружают файл с компьютера (кнопкой
// или перетащив файл), перетаскивают кусок с другой дорожки, вставляют
// скопированное.
const addLane = () => {
  audio();
  remember();
  let n = tracks.length + 1;
  while (tracks.some((t) => t.name === `Дорожка ${n}`)) n++;
  const lane = makeLane(`Дорожка ${n}`, { custom: true });
  tracks.push(lane);
  selected = tracks.length - 1;
  pick = null;
  picks = new Set();
  render();
  save();
  document.querySelector(`.mx-row[data-i="${selected}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  toast('Пустая дорожка добавлена: загрузите в неё файл или перетащите кусок с другой дорожки');
};
$('addTrack').addEventListener('click', addLane);
$('addTrack2').addEventListener('click', addLane);

$('addFile').addEventListener('change', async (e) => {
  const chosen = e.target.files[0];
  e.target.value = '';
  if (chosen) await fileToLane(chosen, selected);
});

// Файл с компьютера -- кусок на дорожке, с курсора (на пустой -- с начала).
// Сохраняется при треке на сервере: на репетиции он уже будет на месте.
async function fileToLane(chosen, index) {
  audio();
  let lane = tracks[index];
  const name = chosen.name.replace(/\.[^.]+$/, '').slice(0, 40) || 'Своя дорожка';
  if (!lane) {
    lane = makeLane(name, { custom: true });
    tracks.push(lane);
    index = tracks.length - 1;
  }
  const at = lane.clips.length ? position() : 0;
  if (lane.custom && !lane.clips.length && /^Дорожка \d+$/.test(lane.name)) { lane.name = name; lane.color = colorOf(name); }
  let buffer = null;
  try { buffer = await ctx.decodeAudioData(await chosen.arrayBuffer()); } catch (error) { buffer = null; }
  remember();
  let src;
  lane.uploading = true;
  if (!buffer) {
    // Браузер не читает формат (amr, 3gp, иногда m4a) -- сервер перекодирует в mp3
    toast('Перекодируем запись на сервере…');
    render();
    const done = await uploadExtra(lane.name, chosen);
    lane.uploading = false;
    if (!done) { render(); return; }
    try {
      buffer = await ctx.decodeAudioData(await (await fetch(done.url)).arrayBuffer());
    } catch (error) { toast('Этот файл не получилось прочитать — попробуйте mp3 или wav'); render(); return; }
    src = addSource(keyOf(done.url), done.url, lane.name, buffer, done.name);
  } else {
    // Формат браузеру знаком -- играет сразу, на сервер уходит параллельно
    src = addSource(`local:${Date.now().toString(36)}`, '', lane.name, buffer, '', true);
  }
  lane.clips.push(makeClip(src, { offset: at }));
  lane.clips.sort((x, y) => clipStart(x) - clipStart(y));
  selected = index;
  pick = lane.clips.find((c) => c.src === src);
  picks = new Set(pick ? [pick] : []);
  updateDuration();
  render();
  if (playing) startAt(position());
  if (src.local) {
    toast('Дорожка добавлена, сохраняем её при треке…');
    await keepOnServer(lane, src, chosen);
  }
  lane.uploading = false;
  // Пустая своя дорожка с первым файлом -- теперь дорожка этого файла (название -- на сервере)
  if (!src.local && lane.custom && lane.clips.length === 1) {
    Object.assign(lane, { key: src.key, url: src.url, extra: src.extra, custom: false });
  }
  if (!src.local) toast('Сохранено при треке — будет здесь и в следующий раз, на любом устройстве');
  render();
  save();
}

// Файл с компьютера можно просто бросить на дорожку
document.addEventListener('dragover', (e) => {
  const zone = e.target.closest('[data-drop]');
  if (!zone || !e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
  e.preventDefault();
  zone.classList.add('dropping');
});
document.addEventListener('dragleave', (e) => {
  const zone = e.target.closest('[data-drop]');
  if (zone) zone.classList.remove('dropping');
});
document.addEventListener('drop', (e) => {
  const zone = e.target.closest('[data-drop]');
  if (!zone || !e.dataTransfer || !e.dataTransfer.files.length) return;
  e.preventDefault();
  zone.classList.remove('dropping');
  const chosen = [...e.dataTransfer.files].find((f) => /^audio\//.test(f.type) || /\.(mp3|wav|m4a|flac|ogg|aac|opus|amr|3gp|webm)$/i.test(f.name));
  if (!chosen) { toast('Нужен аудиофайл: mp3, wav, m4a, flac, ogg'); return; }
  fileToLane(chosen, Number(zone.dataset.drop));
});

// Файл, который уже играет из памяти браузера, -- на сервер; дальше кусок
// ссылается на сохранённый файл
async function keepOnServer(lane, src, chosen) {
  const done = await uploadExtra(lane.name, chosen);
  if (!done) return false;
  files.delete(src.key);
  Object.assign(src, { key: keyOf(done.url), url: done.url, extra: done.name, local: false });
  files.set(src.key, src);
  return true;
}

// Дорожка хранится при треке: на репетиции открыл -- она уже на месте
async function uploadExtra(label, chosen) {
  const form = new FormData();
  form.append('file', chosen);
  form.append('label', label);
  form.append('version', params.get('file') || '');
  try {
    const answer = await fetch(`/api/studio/${jobId}/extra`, { method: 'POST', body: form });
    const data = await answer.json().catch(() => ({}));
    if (!answer.ok) throw new Error(data.detail || 'сервер не принял файл');
    return data;
  } catch (error) {
    toast(`Дорожка не сохранилась: ${error.message}`, 7000);
    return null;
  }
}


// ---------------------------------------------------------------- запись голоса
// Как в Audition: песня играет в наушниках, вы поёте -- дубль ложится на
// свою дорожку ровно в такт. Задержку звука (вывод в наушники + ввод с
// микрофона) вычитаем сами; если на каком-то устройстве дубль всё же
// съехал -- «Сдвиг записи» в настройках поправит раз и навсегда.
// Обработка браузера (шумодав, эхоподавление, автогромкость) выключена:
// она «жуёт» голос; чистим своими цепочками (FX), их можно сменить потом.
const REC_KEY = 'naslux-rec';

function recPrefs() {
  try { return JSON.parse(localStorage.getItem(REC_KEY) || '{}'); } catch (error) { return {}; }
}
function setRecPrefs(patch) {
  try { localStorage.setItem(REC_KEY, JSON.stringify({ ...recPrefs(), ...patch })); } catch (error) { /* приватный режим */ }
}

async function openMic(deviceId = '') {
  if (mic && micFor === deviceId) return mic;
  closeMic(true);
  const wanted = { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 };
  if (deviceId) wanted.deviceId = { exact: deviceId };
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: wanted });
  } catch (error) {
    if (deviceId && error.name === 'OverconstrainedError') return openMic('');   // тот микрофон отключили
    throw error;
  }
  micFor = deviceId;
  return mic;
}

function closeMic(force = false) {
  if (!mic || (rec && !force)) return;
  mic.getTracks().forEach((t) => t.stop());
  mic = null;
  micFor = '';
}

function micError(error) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return 'Этот браузер не умеет записывать звук — откройте сайт в Chrome, Edge или Safari';
  if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
    return 'Нет доступа к микрофону: разрешите его в браузере (значок 🔒 или 🎤 слева от адреса сайта)';
  }
  if (error && error.name === 'NotFoundError') return 'Микрофон не найден — подключите его и попробуйте снова';
  return 'Микрофон занят другой программой или недоступен';
}

// Приёмник звука с микрофона: куски по ~2048 сэмплов с номером кадра часов
// звука -- по нему дубль и ставится на ленту
const RECORDER = `class NasluxRec extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048 + 128); this.n = 0; this.at = 0; }
  flush() { if (this.n) this.port.postMessage({ at: this.at, data: this.buf.slice(0, this.n) }); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) { this.flush(); return true; }
    if (this.n && currentFrame !== this.at + this.n) this.flush();
    if (!this.n) this.at = currentFrame;
    this.buf.set(ch, this.n);
    this.n += ch.length;
    if (this.n >= 2048) this.flush();
    return true;
  }
}
registerProcessor('naslux-rec', NasluxRec);`;

async function recorderNode(take) {
  if (ctx.audioWorklet && window.AudioWorkletNode) {
    try {
      if (!recorderReady) {
        recorderReady = ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([RECORDER], { type: 'text/javascript' })));
      }
      await recorderReady;
      const node = new AudioWorkletNode(ctx, 'naslux-rec', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: 'explicit' });
      node.port.onmessage = (e) => take(e.data.at, e.data.data);
      return node;
    } catch (error) { recorderReady = null; }
  }
  // Старый браузер -- запасной путь, чуть менее точный по времени
  const node = ctx.createScriptProcessor(2048, 1, 1);
  node.onaudioprocess = (e) => {
    const data = new Float32Array(e.inputBuffer.getChannelData(0));
    take(Math.round(ctx.currentTime * ctx.sampleRate) - data.length, data);
  };
  return node;
}

const nameFree = (base) => {
  let n = 1;
  const nameOf = (k) => (k === 1 ? base : `${base} ${k}`);
  while (tracks.some((t) => t.name === nameOf(n))) n++;
  return nameOf(n);
};

async function startRecording() {
  if (rec || !tracks.length) return;
  audio();
  await ctx.resume();
  if (playing) pause();
  const prefs = recPrefs();
  let stream;
  try { stream = await openMic(prefs.mic || ''); } catch (error) { toast(micError(error), 9000); return; }
  setRecPrefs({ ready: true });
  remember();
  // дубль -- на выбранную пустую дорожку, иначе на новую: «Голос», «Голос 2»…
  let index = tracks[selected] && !tracks[selected].clips.length && !tracks[selected].uploading ? selected : -1;
  if (index < 0) {
    tracks.push(makeLane(nameFree('Голос'), { custom: true }));
    index = tracks.length - 1;
  }
  const lane = tracks[index];
  if (lane.custom && /^Дорожка \d+$/.test(lane.name)) { lane.name = nameFree('Голос'); lane.color = colorOf(lane.name); }
  if (!lane.fx) { lane.fx = FX[prefs.fx] ? prefs.fx : prefs.fx === '' ? '' : 'clean'; route(lane); }
  selected = index;
  pick = null;
  picks = new Set();
  const chunks = [];
  const peaks = [];
  const take = (at, data) => {
    if (!rec || rec.chunks !== chunks) return;
    chunks.push({ at, data });
    let v = 0;
    for (let k = 0; k < data.length; k++) v = Math.max(v, Math.abs(data[k]));
    peaks.push({ at, v });
  };
  const node = await recorderNode(take);
  const source = ctx.createMediaStreamSource(stream);
  const sink = ctx.createGain();
  sink.gain.value = 0;
  source.connect(node);
  node.connect(sink).connect(ctx.destination);
  const settings = (stream.getAudioTracks()[0] && stream.getAudioTracks()[0].getSettings()) || {};
  const latency = (ctx.outputLatency || ctx.baseLatency || 0) + (Number(settings.latency) || 0)
    + (Number(prefs.shift) || 0) / 1000;
  const from = pos >= duration - 0.05 ? 0 : pos;
  rec = { lane, chunks, peaks, source, node, sink, from, latency };
  haptic('medium');
  render();
  const interval = analysis && analysis.bpm ? 60 / analysis.bpm : 0;
  if (countIn && interval) {
    const t = ctx.currentTime + 0.15;
    for (let k = 0; k < 4; k++) click(t + k * interval, k === 0);
    startAt(from, 0.15 + 4 * interval);
  } else startAt(from, 0.15);
  rec.startTime = startTime;
  rec.startPos = startPos;
  recUi();
}

async function stopRecording() {
  if (!rec || rec.stopping) return;
  const r = rec;
  r.stopping = true;
  haptic('medium');
  const until = position();
  pause();
  recUi();
  await new Promise((done) => { setTimeout(done, 250); });   // последние куски с микрофона
  try { r.source.disconnect(); r.node.disconnect(); r.sink.disconnect(); } catch (error) { /* уже */ }
  if (r.node.port) r.node.port.onmessage = null;
  r.node.onaudioprocess = null;
  rec = null;
  recUi();
  if ($('recSheet').hidden) closeMic();
  const sr = ctx.sampleRate;
  const n = Math.round((until - r.from) * sr);
  const index = tracks.indexOf(r.lane);
  if (index < 0) { render(); return; }
  if (n < sr * 0.3) {
    render();
    toast('Запись слишком короткая — нажмите ● и пойте, остановить — ● ещё раз или пробел');
    return;
  }
  // куски -- каждый на своё место по часам звука, за вычетом задержки
  const data = new Float32Array(n);
  let loud = 0;
  r.chunks.forEach(({ at, data: part }) => {
    const first = Math.round((r.startPos + (at / sr - r.startTime) - r.latency - r.from) * sr);
    for (let k = 0; k < part.length; k++) {
      const j = first + k;
      if (j >= 0 && j < n) { data[j] = part[k]; loud = Math.max(loud, Math.abs(part[k])); }
    }
  });
  if (loud < 0.002) {
    render();
    toast('Микрофон молчал: проверьте, тот ли выбран (настройки записи — 🎤 рядом с «Запись») и не выключен ли он', 9000);
    return;
  }
  const buffer = ctx.createBuffer(1, n, sr);
  buffer.copyToChannel(data, 0);
  const src = addSource(`local:${Date.now().toString(36)}`, '', r.lane.name, buffer, '', true);
  r.lane.clips.push(makeClip(src, { offset: r.from }));
  r.lane.clips.sort((x, y) => clipStart(x) - clipStart(y));
  selected = index;
  pick = r.lane.clips.find((c) => c.src === src);
  picks = new Set(pick ? [pick] : []);
  updateDuration();
  pos = r.from;
  r.lane.uploading = true;
  render();
  movePlayhead();
  toast(loud > 0.98 ? 'Дубль записан, но местами перегруз — отодвиньтесь от микрофона или убавьте его громкость'
    : 'Дубль записан — пробел, чтобы послушать. Сохраняем при треке…', 6000);
  const file = new File([wav(buffer)], `${r.lane.name}.wav`, { type: 'audio/wav' });
  if (await keepOnServer(r.lane, src, file)) {
    if (r.lane.custom && r.lane.clips.length === 1) {
      Object.assign(r.lane, { key: src.key, url: src.url, extra: src.extra, custom: false });
    }
    toast('Дубль сохранён при треке — будет здесь и на любом устройстве');
    haptic('success');
  }
  r.lane.uploading = false;
  render();
  save();
}

function recUi() {
  const on = Boolean(rec);
  $('recBtn').classList.toggle('on', on);
  $('recBtn').setAttribute('aria-pressed', on);
  $('recBtn').querySelector('span').textContent = on ? 'Стоп' : 'Запись';
  $('recTime').hidden = !on;
  if (!on) $('recTime').textContent = '0:00';
}

function recButton() {
  if (rec) { stopRecording(); return; }
  if (!tracks.length) return;
  if (recPrefs().ready) startRecording();
  else openRecSheet();
}
$('recBtn').addEventListener('click', recButton);
$('recSet').addEventListener('click', openRecSheet);

// Окно записи: микрофон и его уровень, обработка дубля, отсчёт, сдвиг
async function openRecSheet() {
  if (rec) return;
  audio();
  await ctx.resume();
  const prefs = recPrefs();
  $('recFx').innerHTML = Object.entries(FX).map(([id, f]) => `<option value="${id}">${esc(f.title)}</option>`).join('');
  $('recFx').value = FX[prefs.fx] || prefs.fx === '' ? prefs.fx : 'clean';
  $('recShift').value = Number(prefs.shift) || 0;
  $('recCount').checked = countIn;
  $('recCountBox').hidden = !(analysis && analysis.bpm);
  $('recSheet').hidden = false;
  $('recLevel').style.width = '0';
  try {
    await openMic(prefs.mic || '');
  } catch (error) {
    $('recMicNote').textContent = micError(error);
    $('recMicNote').hidden = false;
    return;
  }
  $('recMicNote').hidden = true;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput');
  $('recMic').innerHTML = devices.map((d, k) => `<option value="${esc(d.deviceId)}">${esc(d.label || `Микрофон ${k + 1}`)}</option>`).join('');
  const open = mic.getAudioTracks()[0];
  const now = open && open.getSettings ? open.getSettings().deviceId : '';
  if (now) $('recMic').value = now;
  watchLevel();
}

function watchLevel() {
  if (!mic) { micMeter = null; return; }
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  const source = ctx.createMediaStreamSource(mic);
  source.connect(analyser);
  const data = new Float32Array(analyser.fftSize);
  let shown = 0;
  micMeter = () => {
    if ($('recSheet').hidden) { source.disconnect(); micMeter = null; return; }
    analyser.getFloatTimeDomainData(data);
    let v = 0;
    for (let k = 0; k < data.length; k++) v = Math.max(v, Math.abs(data[k]));
    shown = Math.max(v, shown * 0.92);
    $('recLevel').style.width = `${Math.min(100, Math.round(Math.sqrt(shown) * 100))}%`;
    $('recLevel').classList.toggle('hot', shown > 0.9);
  };
}

$('recMic').addEventListener('change', async () => {
  setRecPrefs({ mic: $('recMic').value });
  try { await openMic($('recMic').value); watchLevel(); } catch (error) { toast(micError(error), 7000); }
});
$('recFx').addEventListener('change', () => setRecPrefs({ fx: $('recFx').value }));
$('recShift').addEventListener('change', () => {
  const v = Math.max(-500, Math.min(500, Math.round(Number($('recShift').value) || 0)));
  $('recShift').value = v;
  setRecPrefs({ shift: v });
});
$('recCount').addEventListener('change', () => { countIn = $('recCount').checked; pressed('countIn', countIn); save(); });
const closeRecSheet = () => { $('recSheet').hidden = true; closeMic(); };
$('recCancel').addEventListener('click', closeRecSheet);
$('recSheet').addEventListener('click', (e) => { if (e.target === $('recSheet')) closeRecSheet(); });
$('recGo').addEventListener('click', () => {
  setRecPrefs({ mic: $('recMic').value || recPrefs().mic || '', fx: $('recFx').value });
  $('recSheet').hidden = true;
  startRecording();
});

// Обработка дорожки (FX): готовые цепочки, звучат сразу и попадают в микс
let fxLane = -1;
function openFx(i) {
  fxLane = i;
  const t = tracks[i];
  $('fxTitle').textContent = `Обработка: ${t.name}`;
  $('fxList').innerHTML = Object.entries(FX).map(([id, f]) => `
    <button type="button" class="mx-fxpick" data-fxpick="${id}" aria-pressed="${(t.fx || '') === id}">
      <b>${esc(f.title)}</b><small>${esc(f.text)}</small></button>`).join('');
  $('fxMixNote').hidden = !(liveBus && roleOf(t) === 'voice');
  $('fxAi').hidden = !laneSource(t);
  $('fxAiNote').textContent = '';
  $('fxSheet').hidden = false;
}

// Нейросеть на сервере -- для своих дорожек (записанный голос, загруженный файл)
const laneSource = (t) => t && t.clips.map((c) => c.src).find((src) => src.extra && !src.local);
const VOICE_DONE = {
  denoise: 'Шум убран',
  tune: 'Ноты подтянуты к тональности',
  undo: 'Вернули исходную запись',
};
let voiceBusy = false;
async function voiceOp(i, op) {
  const t = tracks[i];
  const old = laneSource(t);
  if (!old || voiceBusy) return;
  const [kind, strength] = op.split(':');
  const form = new FormData();
  form.append('file', old.extra);
  form.append('op', kind);
  form.append('strength', strength || 'soft');
  form.append('key', (analysis && analysis.key) || '');
  voiceBusy = true;
  $('fxAi').querySelectorAll('button').forEach((b) => { b.disabled = true; });
  $('fxAiNote').textContent = kind === 'undo' ? 'Возвращаем исходник…'
    : 'Нейросеть обрабатывает запись на сервере — обычно до минуты, для длинной песни дольше…';
  try {
    const answer = await fetch(`/api/studio/${jobId}/voice`, { method: 'POST', body: form });
    const data = await answer.json().catch(() => ({}));
    if (!answer.ok) throw new Error(data.detail || 'сервер не ответил');
    const buffer = await ctx.decodeAudioData(await audioBytes(data.url));
    const src = addSource(keyOf(data.url), data.url, t.name, buffer, data.name);
    tracks.forEach((l) => {
      l.clips.forEach((c) => { if (c.src === old) c.src = src; });
      if (l.key === old.key) Object.assign(l, { key: src.key, url: src.url, extra: src.extra });
    });
    mixState.sig = '';
    render();
    save();
    if (playing) startAt(position(), 0.03);
    const cents = data.info && data.info.cents;
    const what = `${VOICE_DONE[kind]}${kind === 'tune' && cents ? ` — в среднем на ${cents} цента` : ''}`
      + `${kind === 'denoise' && data.info && data.info.by === 'ffmpeg' ? ' (простым шумодавом: нейросеть на сервере ещё ставится)' : ''}`;
    $('fxAiNote').textContent = kind === 'undo' ? `${what}.` : `${what}. Исходник сохранён — «Вернуть как было».`;
    toast(what);
  } catch (error) {
    $('fxAiNote').textContent = `Не получилось: ${error.message || error}`;
  }
  voiceBusy = false;
  $('fxAi').querySelectorAll('button').forEach((b) => { b.disabled = false; });
}
$('fxAi').addEventListener('click', (e) => {
  const b = e.target.closest('[data-voiceop]');
  if (b) voiceOp(fxLane, b.dataset.voiceop);
});
// «⋯» у дорожки: редкие действия -- чтобы ручке громкости хватало места
function openLaneMenu(button) {
  const i = Number(button.dataset.lanemenu);
  const t = tracks[i];
  if (!t) return;
  const menu = $('laneMenu');
  menu.innerHTML = `<button type="button" data-loadfile="${i}">⤒ Загрузить файл на дорожку (по курсору)</button>`
    + (t.url && !t.uploading ? `<a href="${t.url}" download>⤓ Скачать исходный файл дорожки</a>` : '')
    + `<button type="button" class="danger" data-remove="${i}">× Убрать дорожку из микса</button>`;
  menu.hidden = false;
  const box = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - menu.offsetWidth - 8, box.left)) + window.scrollX}px`;
  menu.style.top = `${box.bottom + window.scrollY + 4}px`;
}
document.addEventListener('click', (e) => {
  const opener = e.target.closest('[data-lanemenu]');
  if (opener) { openLaneMenu(opener); return; }
  if (!$('laneMenu').hidden && (!e.target.closest('#laneMenu') || e.target.closest('button, a'))) {
    setTimeout(() => { $('laneMenu').hidden = true; }, 0);
  }
});

$('fxList').addEventListener('click', (e) => {
  const chosen = e.target.closest('[data-fxpick]');
  const t = tracks[fxLane];
  if (!chosen || !t) return;
  remember();
  t.fx = chosen.dataset.fxpick;
  route(t);
  $('fxList').querySelectorAll('[data-fxpick]').forEach((b) => b.setAttribute('aria-pressed', b === chosen));
  render();
  save();
});
$('fxDone').addEventListener('click', () => { $('fxSheet').hidden = true; });
$('fxSheet').addEventListener('click', (e) => { if (e.target === $('fxSheet')) $('fxSheet').hidden = true; });
document.addEventListener('click', (e) => {
  const fx = e.target.closest('[data-fx]');
  if (fx) openFx(Number(fx.dataset.fx));
});

// ---------------------------------------------------------------- сведение
// «Свести»: голос встраивается в музыку, как у звукорежиссёра (что именно
// происходит -- в mixbus.js). Включается и выключается одной кнопкой --
// сравнить «до» и «после» на слух.
const songBpm = () => (analysis && analysis.bpm) || 0;

function buildLiveBus() {
  if (liveBus) { try { liveBus.glue.disconnect(); } catch (error) { /* */ } }
  liveBus = mixing.on && window.mixBus.STYLES[mixing.style]
    ? window.mixBus.bus(ctx, mixing.style, master, songBpm()) : null;
  if (liveBus) {
    liveBus.voice.gain.value = mixState.voice;
    liveBus.music.gain.value = mixState.music;
  }
  tracks.forEach(route);
  if (playing) startAt(position(), 0.03);
  mixUi();
}

// Подмешивание: громкость музыки по огибающей голоса -- с позиции p ленты,
// с момента t0 часов. go=false -- просто вернуть музыку на место.
function duckFrom(param, p, t0, go = true) {
  param.cancelScheduledValues(0);
  param.value = 1;
  const curve = mixState.curve;
  if (!go || !curve) return;
  const part = curve.subarray(Math.max(0, Math.floor(p * 50)));
  if (part.length < 2) return;
  try { param.setValueCurveAtTime(part, Math.max(t0, 0), part.length / 50); } catch (error) { /* старый браузер */ }
}

function mixSig() {
  return JSON.stringify([mixing.style, tracks.map((t) => [t.key, roleOf(t), audible(t), t.fx,
    t.clips.map((c) => [c.src.key, +c.offset.toFixed(3), +c.trimStart.toFixed(3), +c.trimEnd.toFixed(3)])])]);
}

// Замер: голос и музыку отдельно, через их обработку, при громкости 100% --
// ручки громкости дорожек остаются вашей поправкой поверх сведения
async function analyzeMix() {
  if (!mixing.on || !duration) return mixState;
  const sig = mixSig();
  if (sig === mixState.sig) return mixState;
  const rate = 22050;
  const len = Math.max(1, Math.ceil(duration * rate));
  const part = async (role) => {
    const lanes = tracks.filter((t) => audible(t) && t.clips.length && roleOf(t) === role);
    if (!lanes.length) return null;
    const off = new OfflineAudioContext(2, len, rate);
    lanes.forEach((t) => {
      const g = off.createGain();
      const chain = role === 'voice' ? window.mixBus.voiceChain(off, mixing.style) : fxChain(off, t.fx);
      g.connect(chain.input);
      chain.output.connect(off.destination);
      t.clips.forEach((c) => schedule(off, c, 0, 0, g, duration));
    });
    const out = await off.startRendering();
    return [out.getChannelData(0), out.getChannelData(1)];
  };
  const [v, m] = await Promise.all([part('voice'), part('music')]);
  if (sig !== mixSig() || !mixing.on) return mixState;      // пока мерили, всё поменялось -- замер ещё раз
  const s = window.mixBus.STYLES[mixing.style];
  const lv = v ? window.mixBus.loudness(v, rate) : -Infinity;
  const lm = m ? window.mixBus.loudness(m, rate) : -Infinity;
  let gv = Number.isFinite(lv) && Number.isFinite(lm) ? 10 ** ((lm + s.voice - lv) / 20) : 1;
  // сумма -- к одному уровню перед склейкой: компрессор работает одинаково на любом треке
  const power = (Number.isFinite(lm) ? 10 ** (lm / 10) : 0) + (Number.isFinite(lv) ? 10 ** ((lv + 20 * Math.log10(gv)) / 10) : 0);
  const k = power > 0 ? 10 ** ((window.mixBus.STAGE - 10 * Math.log10(power)) / 20) : 1;
  const clamp = (x) => Math.max(0.05, Math.min(16, x));
  gv = clamp(gv * k);
  const gm = clamp(k);
  mixState = { voice: gv, music: gm, sig, lv, lm,
    curve: v && m ? window.mixBus.duckCurve(v[0], rate, s.duck, duration) : null };
  if (liveBus) {
    liveBus.voice.gain.setTargetAtTime(gv, ctx.currentTime, 0.05);
    liveBus.music.gain.setTargetAtTime(gm, ctx.currentTime, 0.05);
    if (playing) duckFrom(liveBus.duck.gain, position(), ctx.currentTime + 0.02);
  }
  return mixState;
}

function mixUi() {
  const on = Boolean(mixing.on && liveBus);
  pressed('mixBtn', on);
  $('mixBtn').title = on ? `Сведение «${window.mixBus.STYLES[mixing.style].title}» включено — нажмите, чтобы сравнить с исходным звуком`
    : 'Влить голос в микс: уровни, обработка, общее пространство — как у звукорежиссёра';
}

function mixReport() {
  const st = mixState;
  const s = window.mixBus.STYLES[mixing.style];
  if (!Number.isFinite(st.lv)) return 'Голосовых дорожек нет — сведение выровняло и склеило музыку. Отметьте, какая дорожка — голос.';
  if (!Number.isFinite(st.lm)) return 'Только голос, без музыки — обработали голос и дали ему пространство.';
  const sign = s.voice > 0 ? `на ${s.voice} дБ громче музыки` : s.voice < 0 ? `на ${-s.voice} дБ тише музыки, внутри неё` : 'вровень с музыкой';
  return `Голос поставлен ${sign}; пока он звучит, музыка уступает ${s.duck} дБ и освобождает ему место в середине. `
    + `Общий зал${s.delay && songBpm() ? ' и эхо в темп песни' : ''} — одно пространство на всех.`;
}

let mixPick = '';
function openMix() {
  if (!tracks.length) return;
  mixPick = mixing.style || 'pop';
  const styles = window.mixBus.STYLES;
  $('mixStyles').innerHTML = Object.entries(styles).map(([id, st]) => `
    <button type="button" class="mx-fxpick" data-mixstyle="${id}" aria-pressed="${id === mixPick}">
      <b>${esc(st.title)}</b><small>${esc(st.text)}</small></button>`).join('');
  mixRoles();
  $('mixOff').hidden = !mixing.on;
  $('mixReport').hidden = !(mixing.on && mixState.sig);
  if (mixing.on && mixState.sig) $('mixReport').textContent = mixReport();
  $('mixSheet').hidden = false;
}

function mixRoles() {
  $('mixRoles').innerHTML = tracks.map((t, i) => {
    const voice = roleOf(t) === 'voice';
    return `<button type="button" class="mx-role${voice ? ' voice' : ''}" data-role="${i}" aria-pressed="${voice}"
      title="Нажмите, чтобы поменять: голос или музыка"><i style="background:${t.color}"></i><b data-noi18n>${esc(t.name)}</b>
      <small>${voice ? '🎤 голос' : '🎸 музыка'}</small></button>`;
  }).join('');
  const ai = aiVoices().filter((t) => audible(t));
  $('mixAiBox').hidden = !ai.length;
  $('mixAiNames').textContent = ai.map((t) => `«${t.name}»`).join(', ');
  $('mixAi').checked = true;
}

$('mixStyles').addEventListener('click', (e) => {
  const b = e.target.closest('[data-mixstyle]');
  if (!b) return;
  mixPick = b.dataset.mixstyle;
  $('mixStyles').querySelectorAll('[data-mixstyle]').forEach((x) => x.setAttribute('aria-pressed', x === b));
});
$('mixRoles').addEventListener('click', (e) => {
  const b = e.target.closest('[data-role]');
  const t = b && tracks[b.dataset.role];
  if (!t) return;
  mixing.roles[t.key] = roleOf(t) === 'voice' ? 'music' : 'voice';
  mixRoles();
});
$('mixGo').addEventListener('click', async () => {
  audio();
  const ai = $('mixAiBox').hidden || !$('mixAi').checked ? [] : aiVoices().filter((t) => audible(t));
  if (ai.length) {
    remember();
    ai.forEach((t) => { t.mute = true; t.solo = false; });
    render();
  }
  mixing.on = true;
  mixing.style = mixPick || 'pop';
  mixState.sig = '';
  buildLiveBus();
  $('mixGo').disabled = true;
  toast('Сводим: меряем голос и музыку…');
  try { await analyzeMix(); } catch (error) { /* звучит и без замера */ }
  $('mixGo').disabled = false;
  if (liveBus) {
    liveBus.voice.gain.value = mixState.voice;
    liveBus.music.gain.value = mixState.music;
  }
  $('mixSheet').hidden = true;
  save();
  toast(`Сведено: ${window.mixBus.STYLES[mixing.style].title}. ${mixReport()} Кнопка «Свести» — сравнить с исходным звуком.`, 9000);
});
$('mixOff').addEventListener('click', () => {
  mixing.on = false;
  buildLiveBus();
  $('mixSheet').hidden = true;
  save();
  toast('Сведение выключено — звучит как было');
});
$('mixCancel').addEventListener('click', () => { $('mixSheet').hidden = true; });
$('mixSheet').addEventListener('click', (e) => { if (e.target === $('mixSheet')) $('mixSheet').hidden = true; });
$('mixSet').addEventListener('click', openMix);
// Кнопка «Свести»: первый раз -- выбрать стиль, дальше -- вкл/выкл для сравнения
$('mixBtn').addEventListener('click', async () => {
  if (!tracks.length) return;
  if (!mixing.style) { openMix(); return; }
  audio();
  mixing.on = !mixing.on;
  if (mixing.on) mixState.sig = '';
  buildLiveBus();
  if (mixing.on) await analyzeMix().catch(() => {});
  save();
  toast(mixing.on ? `Сведение «${window.mixBus.STYLES[mixing.style].title}»` : 'Без сведения — как было', 2500);
});

// Скачать микс: то, что сейчас звучит (заглушённые и «соло» -- как на
// экране, громкость, обрезка, сдвиги монтажа), по желанию -- с метрономом и
// тактом отсчёта, целиком или только выделенный кусок (повтор).
$('exportMix').addEventListener('click', () => {
  if (!tracks.length) return;
  const on = tracks.filter((t) => audible(t) && t.clips.length);
  const off = tracks.filter((t) => !audible(t) && t.clips.length);
  const names = (list) => [...new Set(list.map((t) => t.name))].join(', ');
  $('exportWhat').innerHTML = `<b>Звучат:</b> ${esc(names(on)) || '—'}`
    + (off.length ? `<br><span class="muted"><b>Без:</b> ${esc(names(off))}</span>` : '');
  $('exportMetro').checked = metroOn;
  $('exportMetroBox').hidden = !beats.length;
  $('exportCount').checked = false;
  $('exportCountBox').hidden = !(analysis && analysis.bpm);
  $('exportLoopBox').hidden = !(loop.b > loop.a);
  $('exportLoop').checked = loop.on && loop.b > loop.a;
  $('exportLoud').checked = mixing.on;
  $('exportSheet').hidden = false;
});
// Сохранить микс треком в Студии: слушать в плеере, делиться ссылкой,
// показать музыкантам -- как шаблон песни
$('exportSave').addEventListener('click', async () => {
  $('exportSheet').hidden = true;
  $('exportMix').disabled = true;
  toast('Сводим микс…', 60000);
  try {
    const { rendered, part, from, to } = await renderMix();
    const form = new FormData();
    form.append('file', wav(rendered), `${mixName(part, from, to)}.wav`);
    form.append('rights', ['own', 'cover'].includes(job && job.rights) ? job.rights : 'own');
    toast('Сохраняем в Студию…', 60000);
    const up = await fetch('/api/studio/upload', { method: 'POST', body: form });
    const made = await up.json().catch(() => ({}));
    if (!up.ok) throw new Error(made.detail || 'сервер не принял файл');
    note(`Микс сохранён в Студии: «${esc(mixName(part, from, to))}». Там его можно послушать, скачать и `
      + `поделиться ссылкой. <a href="/studio">Открыть в Студии</a>`);
    toast('Микс сохранён в Студии');
  } catch (error) {
    toast(`Не получилось сохранить микс: ${error.message || error}`, 7000);
  }
  $('exportMix').disabled = false;
});
$('exportCancel').addEventListener('click', () => { $('exportSheet').hidden = true; });
$('exportSheet').addEventListener('click', (e) => { if (e.target === $('exportSheet')) $('exportSheet').hidden = true; });

// Готовый микс одним AudioBuffer -- как в окне «Скачать микс»
async function renderMix() {
  if (mixing.on) await analyzeMix();
  const rate = ctx.sampleRate;
  const part = $('exportLoop').checked && loop.b > loop.a;
  const from = part ? loop.a : 0;
  const to = part ? loop.b : duration;
  const interval = analysis && analysis.bpm ? 60 / analysis.bpm : 0;
  const lead = $('exportCount').checked && interval ? 4 * interval : 0;    // такт отсчёта перед музыкой
  const offline = mixdown(from, to, lead, rate);
  if ($('exportMetro').checked || lead) {
    const out = window.metroSound.output(offline, $('metroVol').value);
    for (let k = 0; lead && k < 4; k++) window.metroSound.play(offline, out, k * interval, k === 0);
    if ($('exportMetro').checked) {
      beats.forEach((b, k) => {
        if (b < from - 0.001 || b >= to || !metroKeeps(k)) return;
        window.metroSound.play(offline, out, lead + b - from, beatPos[k] === 0);
      });
    }
  }
  const rendered = await offline.startRendering();
  if ($('exportLoud').checked) window.mixBus.master(rendered);
  return { rendered, part, from, to };
}

const mixName = (part, from, to) => {
  const what = [part ? `${clock(from)}-${clock(to)}`.replace(/:/g, '.') : '', $('exportMetro').checked ? 'с метрономом' : '']
    .filter(Boolean).join(', ');
  return `${(job && job.name || 'naslux').replace(/\.[^.]+$/, '').replace(/ \((микс|склейка)\)$/, '')} — микс${what ? ` (${what})` : ''}`;
};

$('exportGo').addEventListener('click', async () => {
  $('exportSheet').hidden = true;
  $('exportMix').disabled = true;
  toast('Сводим микс…');
  try {
    const { rendered, part, from, to } = await renderMix();
    const blob = wav(rendered);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${mixName(part, from, to)}.wav`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  } catch (error) {
    toast('Не получилось свести микс в этом браузере');
  }
  $('exportMix').disabled = false;
});

// Сведение того, что звучит (заглушённые -- нет), с from по to ленты,
// со сдвигом lead секунд (такт отсчёта). Возвращает OfflineAudioContext:
// метроном в него можно добавить до startRendering.
function mixdown(from, to, lead = 0, rate = ctx.sampleRate) {
  const offline = new OfflineAudioContext(2, Math.max(1, Math.ceil((to - from + lead) * rate)), rate);
  const bus = offline.createGain();
  bus.connect(limiter(offline)).connect(offline.destination);
  const mb = mixing.on ? window.mixBus.bus(offline, mixing.style, bus, songBpm()) : null;
  if (mb) {
    mb.voice.gain.value = mixState.voice;
    mb.music.gain.value = mixState.music;
    duckFrom(mb.duck.gain, from, lead);
  }
  tracks.forEach((t) => {
    if (!audible(t) || !t.clips.length) return;
    const g = offline.createGain();
    g.gain.value = t.vol / 100;
    const voice = mb && roleOf(t) === 'voice';
    const fx = voice ? window.mixBus.voiceChain(offline, mixing.style) : fxChain(offline, t.fx);
    g.connect(fx.input);
    fx.output.connect(!mb ? bus : voice ? mb.voice : mb.music);
    t.clips.forEach((clip) => schedule(offline, clip, from, lead, g, to));
  });
  return offline;
}

// WAV 16 бит: стерео (микс) или моно (запись с микрофона -- вдвое легче)
function wav(buffer) {
  const n = buffer.length;
  const channels = buffer.numberOfChannels > 1 ? [buffer.getChannelData(0), buffer.getChannelData(1)]
    : [buffer.getChannelData(0)];
  const frame = channels.length * 2;
  const out = new DataView(new ArrayBuffer(44 + n * frame));
  const text = (at, s) => [...s].forEach((ch, k) => out.setUint8(at + k, ch.charCodeAt(0)));
  text(0, 'RIFF'); out.setUint32(4, 36 + n * frame, true); text(8, 'WAVE'); text(12, 'fmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, channels.length, true);
  out.setUint32(24, buffer.sampleRate, true); out.setUint32(28, buffer.sampleRate * frame, true);
  out.setUint16(32, frame, true); out.setUint16(34, 16, true); text(36, 'data'); out.setUint32(40, n * frame, true);
  let at = 44;
  for (let i = 0; i < n; i++) {
    for (const data of channels) {
      const v = Math.max(-1, Math.min(1, data[i]));
      out.setInt16(at, v < 0 ? v * 0x8000 : v * 0x7fff, true);
      at += 2;
    }
  }
  return new Blob([out], { type: 'audio/wav' });
}

init().catch((error) => {
  note(`Не получилось открыть мультитрек: ${esc(error.message || error)}`);
});

// Окна снизу на телефоне -- «шторки» как на iPhone: потянуть вниз за
// верх окна -- закрыть (то же, что нажать мимо окна). Порог -- отклик.
document.querySelectorAll('.mx-sheet').forEach((sheet) => {
  const box = sheet.querySelector('.mx-sheet-box');
  if (!box) return;
  let g = null;
  box.addEventListener('touchstart', (e) => {
    g = null;
    if (e.touches.length !== 1 || box.scrollTop > 0 || e.target.closest('input, select, textarea')) return;
    g = { y: e.touches[0].clientY, x: e.touches[0].clientX, dy: 0, on: false, armed: false };
  }, { passive: true });
  box.addEventListener('touchmove', (e) => {
    if (!g) return;
    g.dy = e.touches[0].clientY - g.y;
    const dx = e.touches[0].clientX - g.x;
    if (!g.on) {
      if (g.dy < 10 || Math.abs(dx) > g.dy) { if (Math.abs(dx) > 10 || g.dy < -10) g = null; return; }
      g.on = true;
    }
    e.preventDefault();
    box.style.transition = 'none';
    box.style.transform = `translateY(${Math.max(0, g.dy)}px)`;
    const armed = g.dy > 90;
    if (armed !== g.armed) { g.armed = armed; if (armed) haptic('tick'); }
  }, { passive: false });
  const end = () => {
    if (!g || !g.on) { g = null; return; }
    const close = g.armed;
    g = null;
    box.style.transition = 'transform .2s ease-out';
    box.style.transform = close ? 'translateY(100%)' : '';
    setTimeout(() => {
      box.style.transition = '';
      box.style.transform = '';
      if (close) {
        haptic('light');
        if (sheet.id === 'labelSheet') $('labelCancel').click();
        else sheet.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      }
    }, close ? 200 : 0);
  };
  box.addEventListener('touchend', end);
  box.addEventListener('touchcancel', end);
});
