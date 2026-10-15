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
const STORE = `naslux.mix.${jobId}.${params.get('file') || ''}`;

let ctx = null;
let master = null;
let metroGain = null;
// Дорожка на ленте -- кусок звука: {name, key, url, buffer, gain, mute, solo, vol,
// offset -- где на ленте время 0 файла, trimStart/trimEnd -- что звучит из файла
// (в секундах файла), local, peaks, color}. Один файл может лежать на ленте
// несколькими кусками (резка ✂): у них общий key.
const tracks = [];
let selected = -1;        // выбранная дорожка: её режет ✂ и двигает «Монтаж»
let montage = false;      // режим монтажа: протяжка двигает дорожку по времени
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
const loop = { on: false, a: 0, b: 0 };
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
  master.connect(ctx.destination);
  metroGain = window.metroSound.output(ctx, $('metroVol').value);
  return ctx;
}

function saved() {
  try { return JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (error) { return {}; }
}

// Настройки дорожек -- по ключу файла, а не по названию: название теперь
// можно менять. Куски одного файла (резка) -- списком под тем же ключом.
function save() {
  try {
    const kept = tracks.filter((t) => !t.local);
    const clips = {};
    kept.forEach((t) => {
      (clips[t.key] = clips[t.key] || []).push({ mute: t.mute, solo: t.solo, vol: t.vol, offset: t.offset,
        trimStart: t.trimStart, trimEnd: t.trimEnd });
    });
    localStorage.setItem(STORE, JSON.stringify({
      metro: metroOn, countIn, metroVol: $('metroVol').value, hidden: [...hidden],
      metroMode: $('metroMode').value, trackHeight, order: [...new Set(kept.map((t) => t.key))], clips,
    }));
  } catch (error) { /* приватный режим -- просто без памяти */ }
}

const keyOf = (url, name) => (url ? decodeURIComponent(url.split('?')[0].split('/').pop()) : '') || name;
const trackEnd = (t) => t.offset + t.buffer.duration;
function updateDuration() {
  duration = tracks.length ? Math.max(...tracks.map(trackEnd)) : 0;
  $('total').textContent = clock(duration);
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
  if (!stems) showSplit(splitting);

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
  metroGain.setVolume($('metroVol').value);
  pressed('metro', metroOn);
  pressed('countIn', countIn);
  // Убранные дорожки («×») не грузим вовсе -- и память, и трафик
  hidden = new Set(memory.hidden || []);
  list.forEach((item) => { item.key = keyOf(item.url, item.name); });
  const kept = list.filter((item) => !hidden.has(item.key) && !hidden.has(item.name));
  if (kept.length) list = kept; else hidden.clear();   // убрать все нельзя
  // Порядок, в который дорожки расставили перетаскиванием; новые -- в конец
  const order = memory.order || [];
  const rank = (item) => {
    let k = order.indexOf(item.key);
    if (k < 0) k = order.indexOf(item.name);           // память до 15.10 -- по названиям
    return k < 0 ? order.length : k;
  };
  list = list.map((item, k) => [item, k]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([item]) => item);
  showHidden();
  let done = 0;
  note(`Загружаем дорожки: 0 из ${list.length}…`);
  let missing = 0;
  await Promise.all(list.map(async (item, index) => {
    const data = await audioBytes(item.url).catch(() => null);
    if (!data) { missing++; return; }
    const buffer = await ctx.decodeAudioData(data);
    const clips = (memory.clips || {})[item.key] || [(memory.tracks || {})[item.name] || {}];
    tracks[index] = clips.map((remembered, part) => {
      const t = makeTrack(item.name, item.url, buffer, remembered);
      Object.assign(t, { extra: item.extra || '', key: item.key, owner: item.owner || '', file: item.file || '',
        orig: item.orig || '', part });
      return t;
    });
    note(`Загружаем дорожки: ${++done} из ${list.length}…`);
  }));
  const loaded = tracks.filter(Boolean).flat();
  tracks.length = 0;
  tracks.push(...loaded);
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
// «Монтаж»: протяжка двигает дорожку по времени; края прилипают к тактам и
// к краям других дорожек -- так куски склеиваются встык. ✂ режет выбранную
// дорожку по курсору на два куска; лишний кусок -- «×».
function markSelected() {
  document.querySelectorAll('.mx-row[data-i]').forEach((row) => {
    row.classList.toggle('picked', Number(row.dataset.i) === selected);
  });
  $('cutBtn').disabled = selected < 0;
}

function snapOffset(t, offset, width) {
  const start = offset + t.trimStart;
  const end = offset + t.trimEnd;
  const limit = (12 / width) * viewLen();        // 12 пикселей
  let best = null;
  const edges = [0];
  tracks.forEach((o) => { if (o !== t) edges.push(o.offset + o.trimStart, o.offset + o.trimEnd); });
  beats.forEach((b) => { if (downbeats.has(b.toFixed(2))) edges.push(b); });
  edges.forEach((edge) => {
    [[start, edge - start], [end, edge - end]].forEach(([, d]) => {
      if (Math.abs(d) < limit && (best === null || Math.abs(d) < Math.abs(best))) best = d;
    });
  });
  return Math.max(-t.trimStart, offset + (best || 0));
}

function cut() {
  const t = tracks[selected];
  if (!t) { toast('Нажмите на дорожку, которую резать, и поставьте курсор, где резать'); return; }
  const at = position() - t.offset;                 // секунда файла
  if (at <= t.trimStart + 0.05 || at >= t.trimEnd - 0.05) {
    toast('Поставьте курсор внутрь выбранной дорожки — там, где резать');
    return;
  }
  const piece = makeTrack(t.name, t.url, t.buffer, { mute: t.mute, solo: t.solo, vol: t.vol, offset: t.offset,
    trimStart: at, trimEnd: t.trimEnd }, t.local);
  Object.assign(piece, { key: t.key, extra: t.extra, orig: t.orig, peaks: t.peaks, part: t.part + 1 });
  t.trimEnd = at;
  tracks.splice(selected + 1, 0, piece);
  selected += 1;
  render();
  save();
  if (playing) startAt(position());
  toast(montage ? 'Разрезано. Тяните кусок, чтобы сдвинуть; лишний — «×»'
    : 'Разрезано. Включите «Монтаж», чтобы двигать куски; лишний — «×»');
}

$('cutBtn').addEventListener('click', cut);
$('montage').addEventListener('click', () => {
  montage = !montage;
  pressed('montage', montage);
  $('board').classList.toggle('montage', montage);
  if (montage) toast('Монтаж: тяните дорожку, чтобы сдвинуть по времени. Края прилипают к тактам и к другим дорожкам — так дорожки склеиваются. ✂ режет выбранную по курсору', 7000);
  redraw();
});

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
  tracks.filter((x) => x.key === t.key).forEach((x) => { x.name = name; x.color = colorOf(name); });
  render();
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

function makeTrack(name, url, buffer, remembered = {}, local = false) {
  const gain = ctx.createGain();
  gain.connect(master);
  const t = {
    name, url, buffer, gain, local, key: keyOf(url, name), part: 0,
    mute: Boolean(remembered.mute), solo: Boolean(remembered.solo),
    vol: remembered.vol ?? 100, offset: Math.max(0, remembered.offset || 0),
    trimStart: remembered.trimStart ?? 0,
    trimEnd: Math.min(remembered.trimEnd ?? buffer.duration, buffer.duration),
    peaks: peaksOf(buffer, 1600), color: colorOf(name),
  };
  return t;
}

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
          <span class="mx-grip" data-grip="${i}" aria-hidden="true">⠿</span><i style="background:${t.color}"></i><b data-noi18n data-label="${i}" title="${esc(t.name)} — нажмите, чтобы переименовать">${esc(t.name)}</b>${tracks.filter((x) => x.key === t.key).length > 1 ? `<small class="mx-part">${tracks.filter((x, k) => x.key === t.key && k <= i).length}</small>` : ''}
          <button type="button" class="mx-rename" data-label="${i}" title="Название дорожки" aria-label="Название дорожки">${ICON.pencil}</button>
          ${t.local ? `<em title="Загружаем на сервер…">${t.uploading ? 'загрузка…' : 'не сохранена'}</em>`
    : t.extra ? '<em title="Ваша дорожка — хранится при этом треке">своя</em>' : ''}</div>
        <div class="mx-btns">
          <button type="button" class="mx-ms" data-mute="${i}" aria-pressed="${t.mute}" title="Заглушить">M</button>
          <button type="button" class="mx-ms solo" data-solo="${i}" aria-pressed="${t.solo}" title="Только эта (соло)">S</button>
          <input type="range" class="mx-vol" data-vol="${i}" min="0" max="150" value="${t.vol}"
            aria-label="Громкость: ${esc(t.name)}" title="Громкость">
          ${t.local ? '' : `<a class="mx-ms" href="${t.url}" download title="Скачать дорожку">⤓</a>`}
          <button type="button" class="mx-ms" data-remove="${i}" title="Убрать дорожку из микса">×</button>
        </div>
      </div>
      <div class="mx-lane"><canvas class="mx-wave" data-wave="${i}" height="64"></canvas></div>
    </div>`).join('');
  applyGains();
  sizeCanvases();
  markSelected();
}

function sizeCanvases() {
  const dpr = window.devicePixelRatio || 1;
  document.querySelectorAll('.mx-lane canvas').forEach((c) => {
    c.width = Math.max(100, Math.round(c.clientWidth * dpr));
    c.height = Math.round((c.id === 'ruler' ? 46 : c.id === 'overview' ? 26 : Math.max(trackHeight, c.clientHeight)) * dpr);
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
  if (e.pointerType !== 'mouse' && !e.target.closest('[data-grip]')) return;
  const row = grab.closest('.mx-row');
  const rows = [...document.querySelectorAll('.mx-row[data-i]')];
  drag = { from: Number(grab.dataset.grab), to: Number(grab.dataset.grab), row, rows, y0: e.clientY,
    mids: rows.map((r) => { const b = r.getBoundingClientRect(); return b.top + b.height / 2; }),
    height: row.getBoundingClientRect().height, id: e.pointerId, moved: false,
    label: label ? Number(label.dataset.label) : null };
  grab.setPointerCapture(e.pointerId);
  e.preventDefault();
});

document.addEventListener('pointermove', (e) => {
  if (!drag || e.pointerId !== drag.id) return;
  const dy = e.clientY - drag.y0;
  if (!drag.moved && Math.abs(dy) < 4) return;
  if (!drag.moved) { drag.moved = true; drag.row.classList.add('dragging'); $('board').classList.add('reordering'); }
  drag.row.style.transform = `translateY(${dy}px)`;
  // куда встанет: по серединам строк, свою не считаем
  const y = drag.mids[drag.from] + dy;
  let to = drag.from;
  drag.mids.forEach((mid, k) => {
    if (k < drag.from && y < mid) to = Math.min(to, k);
    if (k > drag.from && y > mid) to = Math.max(to, k);
  });
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
  // такты -- тонкие линии, чтобы обрезать ровно по сильной доле
  g.fillStyle = 'rgba(255,255,255,.06)';
  beats.forEach((b) => { if (downbeats.has(b.toFixed(2))) g.fillRect(Math.round(x(b)), 0, dpr, h); });
  const bars = t.peaks.length;
  const on = audible(t);
  const a = t.offset + t.trimStart;          // кусок на ленте
  const b = t.offset + t.trimEnd;
  if (i === selected) {                       // выбранная -- подсветка куска
    g.fillStyle = 'rgba(217,154,78,.10)';
    g.fillRect(x(a), 0, x(b) - x(a), h);
  }
  for (let k = 0; k < bars; k++) {
    const sec = (k / bars) * t.buffer.duration;
    const at = t.offset + sec;
    if (at < viewStart - 1 || at > viewStart + viewLen() + 1) continue;
    const inside = sec >= t.trimStart && sec <= t.trimEnd;
    // за краями куска -- только для монтажа (видно, что можно вернуть)
    if (!inside && !montage && (t.part || tracks.some((o) => o !== t && o.key === t.key))) continue;
    const amp = Math.max(1, t.peaks[k] * (h / 2 - 3 * dpr));
    g.fillStyle = !inside ? 'rgba(255,255,255,.10)' : on ? t.color : 'rgba(167,171,184,.35)';
    g.fillRect(x(at), h / 2 - amp, Math.max(1, w / bars * (t.buffer.duration / viewLen())), amp * 2);
  }
  // обрезанное -- затемнено, края -- ручки
  g.fillStyle = 'rgba(10,11,15,.55)';
  g.fillRect(0, 0, x(a), h);
  g.fillRect(x(b), 0, w - x(b), h);
  g.fillStyle = '#d99a4e';
  [a, b].forEach((edge) => g.fillRect(Math.round(x(edge)) - 2 * dpr, 6 * dpr, 4 * dpr, h - 12 * dpr));
  if (montage && i === selected) {            // рамка: эту дорожку можно тянуть
    g.strokeStyle = 'rgba(217,154,78,.9)';
    g.lineWidth = 2 * dpr;
    g.strokeRect(x(a) + dpr, dpr, x(b) - x(a) - 2 * dpr, h - 2 * dpr);
  }
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
  if (loop.b > loop.a) {
    g.fillStyle = loop.on ? 'rgba(217,154,78,.22)' : 'rgba(217,154,78,.10)';
    g.fillRect(x(loop.a), 0, x(loop.b) - x(loop.a), h);
  }
  // аккорды -- полосой снизу, названия там, где помещаются
  g.font = `${11 * dpr}px system-ui, sans-serif`;
  g.textBaseline = 'middle';
  ((analysis && analysis.chords) || []).forEach(([a, b, name], k) => {
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
  tracks.forEach((t) => {
    const src = schedule(ctx, t, p, t0, t.gain);
    if (src) sources.push(src);
  });
  startTime = t0;
  startPos = p;
  playing = true;
  nextBeat = beats.findIndex((b) => b >= p - 0.001);
  if (nextBeat < 0) nextBeat = beats.length;
  $('play').innerHTML = ICON.pause;
  $('play').classList.add('on');
}

// Кусок дорожки на ленте -- с позиции p ленты, в момент t0 часов ctx
// (и для живого звука, и для сведения в файл). end -- где лента кончается.
function schedule(c, t, p, t0, dest, end = Infinity) {
  const from = Math.max(p - t.offset, t.trimStart);               // секунда файла
  const to = Math.min(t.trimEnd, t.buffer.duration, end - t.offset);
  if (to - from <= 0.01) return null;
  const src = c.createBufferSource();
  src.buffer = t.buffer;
  src.connect(dest);
  src.start(t0 + (t.offset + from - p), from, to - from);
  return src;
}

function pause() {
  pos = position();
  playing = false;
  stopSources();
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
  if (loop.on && loop.b > loop.a && now >= loop.b) startAt(loop.a, 0.02);
  else if (now >= duration) { pause(); pos = 0; viewStart = 0; redraw(); }
}, 25);

(function frame() {
  // следующий кадр -- при любой ошибке, иначе курсор замирает до перезагрузки
  requestAnimationFrame(frame);
  if (playing) movePlayhead();
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
    const index = Number(wave.dataset.wave);
    const t = tracks[index];
    const sec = secAt(e, wave);
    const width = wave.getBoundingClientRect().width;
    const px = (x) => toX(x, width);
    const reach = e.pointerType === 'touch' ? 18 : 10;
    const grab = Math.abs(px(sec) - px(t.offset + t.trimStart)) < reach ? 'trimStart'
      : Math.abs(px(sec) - px(t.offset + t.trimEnd)) < reach ? 'trimEnd'
        : montage && sec >= t.offset + t.trimStart && sec <= t.offset + t.trimEnd ? 'move' : null;
    if (selected !== index) { selected = index; markSelected(); redraw(); }
    wave.setPointerCapture(e.pointerId);
    const x0 = e.clientX;
    const view0 = viewStart;
    const offset0 = t.offset;
    let moved = false;
    const move = (ev) => {
      if (pinch) return;
      if (grab === 'move') {
        if (Math.abs(ev.clientX - x0) < 4 && !moved) return;
        moved = true;
        const shift = (ev.clientX - x0) / width * viewLen();
        t.offset = snapOffset(t, Math.max(-t.trimStart, offset0 + shift), width);
        updateDuration();
        redraw();
        return;
      }
      if (grab) {
        const x = Math.max(0, secAt(ev, wave) - t.offset);
        if (grab === 'trimStart') t.trimStart = Math.max(0, Math.min(x, t.trimEnd - 0.5));
        else t.trimEnd = Math.min(t.buffer.duration, Math.max(x, t.trimStart + 0.5));
        drawTrack(t, index);
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
      if (grab === 'move' && !moved && !pinch && ev.type === 'pointerup') { seek(sec); return; }
      if (grab) { save(); if (playing) startAt(position()); return; }
      if (!moved && !pinch && ev.type === 'pointerup') seek(sec);
    };
    wave.addEventListener('pointermove', move);
    wave.addEventListener('pointerup', up);
    wave.addEventListener('pointercancel', up);
    return;
  }
  const ruler = e.target.closest('#ruler');
  if (ruler && duration) {
    const from = secAt(e, ruler);
    ruler.setPointerCapture(e.pointerId);
    const finger = e.pointerType === 'touch';
    // пальцем -- повтор только после долгого нажатия, иначе протяжка двигает курсор
    let selecting = !finger;
    let dragged = false;
    const hold = finger ? setTimeout(() => {
      if (!dragged) { selecting = true; navigator.vibrate?.(15); toast('Ведите пальцем — выделяем кусок для повтора'); }
    }, 450) : null;
    const move = (ev) => {
      if (pinch) return;
      const to = secAt(ev, ruler);
      if (Math.abs(to - from) < viewLen() * 0.004) return;
      dragged = true;
      followPause = Date.now() + 4000;
      if (selecting) {
        loop.a = Math.min(from, to);
        loop.b = Math.max(from, to);
        drawRuler();
      } else {
        clearTimeout(hold);
        pos = to;
        if (playing) startAt(to);
        movePlayhead();
      }
    };
    const up = (ev) => {
      clearTimeout(hold);
      ruler.removeEventListener('pointermove', move);
      ruler.removeEventListener('pointerup', up);
      ruler.removeEventListener('pointercancel', up);
      if (ev.type !== 'pointerup' || pinch) return;
      if (!dragged) { seek(from); return; }
      if (!selecting) return;
      snapLoop();
      loop.on = true;
      pressed('loop', true);
      drawRuler();
      seek(loop.a);
      toast(`Повтор: ${clock(loop.a)} – ${clock(loop.b)}. Выключить — кнопка «Повтор»`);
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
    tracks.forEach((t) => {
      const sec = (k / n) * duration - t.offset;
      if (sec < t.trimStart || sec > t.trimEnd) return;
      const i = Math.floor((sec / t.buffer.duration) * t.peaks.length);
      if (i >= 0 && i < t.peaks.length) peak = Math.max(peak, t.peaks[i]);
    });
    const amp = Math.max(1, peak * (h / 2 - 2));
    g.fillRect((k / n) * w, h / 2 - amp, Math.max(1, w / n - 1), amp * 2);
  }
  if (loop.b > loop.a) {
    g.fillStyle = 'rgba(217,154,78,.25)';
    g.fillRect((loop.a / duration) * w, 0, ((loop.b - loop.a) / duration) * w, h);
  }
  g.strokeStyle = '#d99a4e';
  g.lineWidth = Math.max(1, (window.devicePixelRatio || 1) * 1.5);
  g.strokeRect((viewStart / duration) * w + 1, 1, Math.max(4, (viewLen() / duration) * w - 2), h - 2);
  g.fillStyle = '#fff';
  g.fillRect((position() / duration) * w - 1, 0, 2, h);
}

// Края повтора -- к ближайшим долям: кусок звучит ровно, без рваного начала
function snapLoop() {
  if (!beats.length) return;
  const near = (s) => beats.reduce((best, b) => (Math.abs(b - s) < Math.abs(best - s) ? b : best), beats[0]);
  loop.a = near(loop.a);
  loop.b = Math.max(near(loop.b), loop.a + 0.5);
}

document.addEventListener('click', (e) => {
  const label = e.target.closest('.mx-rename[data-label]') || (tracks.length < 2 && e.target.closest('b[data-label]'));
  if (label && Date.now() - lastDrag > 400) { openLabel(Number(label.dataset.label)); return; }
  const mute = e.target.closest('[data-mute]');
  const solo = e.target.closest('[data-solo]');
  const remove = e.target.closest('[data-remove]');
  if (mute) { const t = tracks[mute.dataset.mute]; t.mute = !t.mute; mute.setAttribute('aria-pressed', t.mute); }
  if (solo) { const t = tracks[solo.dataset.solo]; t.solo = !t.solo; solo.setAttribute('aria-pressed', t.solo); }
  if (remove) {
    if (tracks.length === 1) { toast('Последнюю дорожку убрать нельзя'); return; }
    const doomed = tracks[Number(remove.dataset.remove)];
    if (doomed.extra && !tracks.some((x) => x !== doomed && x.key === doomed.key) && !confirm(`Удалить дорожку «${doomed.name}»? Её придётся загружать заново.`)) return;
    const [t] = tracks.splice(Number(remove.dataset.remove), 1);
    t.gain.disconnect();
    selected = -1;
    if (tracks.some((x) => x.key === t.key)) {
      save();                                  // убрали кусок после резки -- файл и другие куски на месте
    } else if (t.extra) {
      fetch(`/api/studio/${jobId}/extra/${t.extra}`, { method: 'DELETE' })
        .then((r) => toast(r.ok ? 'Дорожка удалена' : 'Не получилось удалить дорожку на сервере'));
    } else if (!t.local) { hidden.add(t.key); save(); showHidden(); }
    updateDuration();
    render();
    if (playing) startAt(Math.min(position(), duration));
    return;
  }
  if (mute || solo) { applyGains(); tracks.forEach(drawTrack); save(); }
});

document.addEventListener('input', (e) => {
  const vol = e.target.closest('[data-vol]');
  if (vol) { tracks[vol.dataset.vol].vol = Number(vol.value); applyGains(); save(); }
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
  if (e.code !== 'Space' || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && e.target.type !== 'range') return;
  e.preventDefault();
  play();
});
window.addEventListener('resize', () => { clearTimeout(sizeCanvases.t); sizeCanvases.t = setTimeout(sizeCanvases, 120); });

// ---------------------------------------------------------------- своя дорожка и микс

$('addTrack').addEventListener('click', () => $('addFile').click());
$('addFile').addEventListener('change', async (e) => {
  const chosen = e.target.files[0];
  e.target.value = '';
  if (!chosen) return;
  audio();
  let name = chosen.name.replace(/\.[^.]+$/, '').slice(0, 40) || 'Своя дорожка';
  while (tracks.some((x) => x.name === name)) name += ' ·';
  let buffer = null;
  try { buffer = await ctx.decodeAudioData(await chosen.arrayBuffer()); } catch (error) { buffer = null; }
  if (!buffer) {
    // Браузер не читает формат (amr, 3gp, иногда m4a) -- сервер перекодирует в mp3
    toast('Перекодируем запись на сервере…');
    const saved = await uploadExtra(name, chosen);
    if (!saved) return;
    try {
      buffer = await ctx.decodeAudioData(await (await fetch(saved.url)).arrayBuffer());
    } catch (error) { toast('Этот файл не получилось прочитать — попробуйте mp3 или wav'); return; }
    const t = makeTrack(name, saved.url, buffer);
    t.extra = saved.name;
    addLoaded(t);
    toast('Дорожка сохранена — она будет здесь и в следующий раз, на любом устройстве');
    save();
    return;
  }
  // Формат браузеру знаком -- дорожка играет сразу, на сервер уходит параллельно
  const t = makeTrack(name, '', buffer, {}, true);
  t.uploading = true;
  addLoaded(t);
  toast('Дорожка добавлена, сохраняем её при треке…');
  const saved = await uploadExtra(name, chosen);
  t.uploading = false;
  if (saved) {
    Object.assign(t, { local: false, url: saved.url, extra: saved.name });
    toast('Дорожка сохранена — она будет здесь и в следующий раз, на любом устройстве');
    save();
  }
  render();
});

function addLoaded(t) {
  tracks.push(t);
  updateDuration();
  render();
  if (playing) startAt(position());
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

// Скачать микс: то, что сейчас звучит (заглушённые и «соло» -- как на
// экране, громкость, обрезка, сдвиги монтажа), по желанию -- с метрономом и
// тактом отсчёта, целиком или только выделенный кусок (повтор).
$('exportMix').addEventListener('click', () => {
  if (!tracks.length) return;
  const on = tracks.filter(audible);
  const off = tracks.filter((t) => !audible(t));
  const names = (list) => [...new Set(list.map((t) => t.name))].join(', ');
  $('exportWhat').innerHTML = `<b>Звучат:</b> ${esc(names(on)) || '—'}`
    + (off.length ? `<br><span class="muted"><b>Без:</b> ${esc(names(off))}</span>` : '');
  $('exportMetro').checked = metroOn;
  $('exportMetroBox').hidden = !beats.length;
  $('exportCount').checked = false;
  $('exportCountBox').hidden = !(analysis && analysis.bpm);
  $('exportLoopBox').hidden = !(loop.b > loop.a);
  $('exportLoop').checked = loop.on && loop.b > loop.a;
  $('exportSheet').hidden = false;
});
$('exportCancel').addEventListener('click', () => { $('exportSheet').hidden = true; });
$('exportSheet').addEventListener('click', (e) => { if (e.target === $('exportSheet')) $('exportSheet').hidden = true; });

$('exportGo').addEventListener('click', async () => {
  $('exportSheet').hidden = true;
  $('exportMix').disabled = true;
  toast('Сводим микс…');
  try {
    const rate = ctx.sampleRate;
    const part = $('exportLoop').checked && loop.b > loop.a;
    const from = part ? loop.a : 0;
    const to = part ? loop.b : duration;
    const interval = analysis && analysis.bpm ? 60 / analysis.bpm : 0;
    const lead = $('exportCount').checked && interval ? 4 * interval : 0;    // такт отсчёта перед музыкой
    const offline = new OfflineAudioContext(2, Math.ceil((to - from + lead) * rate), rate);
    tracks.forEach((t) => {
      if (!audible(t)) return;
      const g = offline.createGain();
      g.gain.value = t.vol / 100;
      g.connect(offline.destination);
      schedule(offline, t, from, lead, g, to);
    });
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
    const blob = wav(rendered);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const what = [part ? `${clock(from)}-${clock(to)}`.replace(/:/g, '.') : '', $('exportMetro').checked ? 'с метрономом' : '']
      .filter(Boolean).join(', ');
    a.download = `${(job && job.name || 'naslux').replace(/\.[^.]+$/, '')} — микс${what ? ` (${what})` : ''}.wav`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  } catch (error) {
    toast('Не получилось свести микс в этом браузере');
  }
  $('exportMix').disabled = false;
});

// WAV 16 бит, стерео
function wav(buffer) {
  const n = buffer.length;
  const channels = [buffer.getChannelData(0), buffer.getChannelData(buffer.numberOfChannels > 1 ? 1 : 0)];
  const out = new DataView(new ArrayBuffer(44 + n * 4));
  const text = (at, s) => [...s].forEach((ch, k) => out.setUint8(at + k, ch.charCodeAt(0)));
  text(0, 'RIFF'); out.setUint32(4, 36 + n * 4, true); text(8, 'WAVE'); text(12, 'fmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 2, true);
  out.setUint32(24, buffer.sampleRate, true); out.setUint32(28, buffer.sampleRate * 4, true);
  out.setUint16(32, 4, true); out.setUint16(34, 16, true); text(36, 'data'); out.setUint32(40, n * 4, true);
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
