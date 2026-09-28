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
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
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
const tracks = [];        // {name, url, buffer, gain, mute, solo, vol, trimStart, trimEnd, local, peaks, color}
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
  const Ctx = window.AudioContext || window.webkitAudioContext;
  // 32 кГц: на репетиции разницы не слышно, а памяти на 12 дорожек нужно
  // вдвое меньше, чем на 44.1 кГц.
  try { ctx = new Ctx({ sampleRate: 32000 }); } catch (error) { ctx = new Ctx(); }
  master = ctx.createGain();
  master.connect(ctx.destination);
  metroGain = ctx.createGain();
  metroGain.gain.value = Number($('metroVol').value) / 100;
  metroGain.connect(ctx.destination);
  return ctx;
}

function saved() {
  try { return JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (error) { return {}; }
}

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      metro: metroOn, countIn, metroVol: $('metroVol').value,
      tracks: Object.fromEntries(tracks.filter((t) => !t.local).map((t) => [t.name, {
        mute: t.mute, solo: t.solo, vol: t.vol, trimStart: t.trimStart, trimEnd: t.trimEnd,
      }])),
    }));
  } catch (error) { /* приватный режим -- просто без памяти */ }
}

// ---------------------------------------------------------------- загрузка

async function init() {
  $('play').innerHTML = ICON.play;
  info = await (await fetch('/api/studio')).json();
  job = info.jobs.find((j) => j.id === jobId);
  if (!job || job.status !== 'done') {
    $('title').textContent = 'Трек не найден';
    note('Этот трек не найден среди ваших или ещё не готов. <a href="/studio">Вернуться в Студию</a>');
    return;
  }
  if (job.expired) {
    $('title').textContent = job.name;
    note('Файлы этого трека удалены по сроку хранения (14 дней).');
    return;
  }
  file = params.get('file') || (job.files[0] && job.files[0].name) || '';
  $('title').textContent = job.name;
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

  const list = shifted ? shifted.files.map((f) => ({ name: stems ? f.label : 'Трек целиком', url: f.url }))
    : stems ? stems.files.map((f) => ({ name: f.label, url: f.url }))
      : [{ name: version.label === 'Оригинал' ? 'Трек целиком' : version.label, url: version.url }];
  $('sub').textContent = stems
    ? `${list.length} ${plural(list.length, 'дорожка', 'дорожки', 'дорожек')}${job.mode === 'stems' ? '' : ` · ${version.label}`}`
    : 'Одна дорожка — разделите трек, чтобы глушить отдельные инструменты';
  if (!stems) showSplit(splitting);

  loadAnalysis(job.mode === 'stems' ? 'source' : version.name);
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
  metroGain.gain.value = Number($('metroVol').value) / 100;
  pressed('metro', metroOn);
  pressed('countIn', countIn);
  let done = 0;
  note(`Загружаем дорожки: 0 из ${list.length}…`);
  await Promise.all(list.map(async (item, index) => {
    const data = await (await fetch(item.url)).arrayBuffer();
    const buffer = await ctx.decodeAudioData(data);
    const remembered = (memory.tracks || {})[item.name] || {};
    tracks[index] = makeTrack(item.name, item.url, buffer, remembered);
    note(`Загружаем дорожки: ${++done} из ${list.length}…`);
  }));
  note('');
  duration = Math.max(...tracks.map((t) => t.buffer.duration));
  $('total').textContent = clock(duration);
  render();
}

function makeTrack(name, url, buffer, remembered = {}, local = false) {
  const gain = ctx.createGain();
  gain.connect(master);
  const t = {
    name, url, buffer, gain, local,
    mute: Boolean(remembered.mute), solo: Boolean(remembered.solo),
    vol: remembered.vol ?? 100,
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

async function loadAnalysis(name) {
  for (let tries = 0; tries < 40; tries++) {
    let answer = await fetch(`/api/studio/${jobId}/analysis?file=${encodeURIComponent(name)}`)
      .then((r) => (r.ok ? r.json() : { error: 'нет' })).catch(() => ({ error: 'сеть' }));
    if (!answer.pending) {
      if (answer.error) { $('factKey').textContent = '—'; return; }
      analysis = shifted ? shiftAnalysis(answer, shifted.shift) : answer;
      answer = analysis;
      $('factKey').textContent = answer.key || '—';
      $('factBpm').textContent = answer.bpm ? `${answer.bpm} BPM` : '—';
      beats = answer.beats && answer.beats.length ? answer.beats : gridBeats(answer.bpm);
      downbeats = new Set((answer.downbeats || []).map((b) => b.toFixed(2)));
      drawRuler();
      tracks.forEach(drawTrack);
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
    <div class="mx-row" data-i="${i}">
      <div class="mx-ctrl">
        <div class="mx-name"><i style="background:${t.color}"></i><b title="${esc(t.name)}">${esc(t.name)}</b>
          ${t.local ? '<em title="Только в этом окне, на сервер не отправляется">своя</em>' : ''}</div>
        <div class="mx-btns">
          <button type="button" class="mx-ms" data-mute="${i}" aria-pressed="${t.mute}" title="Заглушить">M</button>
          <button type="button" class="mx-ms solo" data-solo="${i}" aria-pressed="${t.solo}" title="Только эта (соло)">S</button>
          <input type="range" class="mx-vol" data-vol="${i}" min="0" max="150" value="${t.vol}"
            aria-label="Громкость: ${esc(t.name)}" title="Громкость">
          ${t.local ? `<button type="button" class="mx-ms" data-remove="${i}" title="Убрать дорожку">×</button>`
    : `<a class="mx-ms" href="${t.url}" download title="Скачать дорожку">⤓</a>`}
        </div>
      </div>
      <div class="mx-lane"><canvas class="mx-wave" data-wave="${i}" height="64"></canvas></div>
    </div>`).join('');
  applyGains();
  sizeCanvases();
}

function sizeCanvases() {
  const dpr = window.devicePixelRatio || 1;
  document.querySelectorAll('.mx-lane canvas').forEach((c) => {
    c.width = Math.max(100, Math.round(c.clientWidth * dpr));
    c.height = Math.round((c.id === 'ruler' ? 46 : Math.max(64, c.clientHeight)) * dpr);
  });
  drawRuler();
  tracks.forEach(drawTrack);
  movePlayhead();
}

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
  const x = (sec) => (sec / duration) * w;
  // такты -- тонкие линии, чтобы обрезать ровно по сильной доле
  g.fillStyle = 'rgba(255,255,255,.06)';
  beats.forEach((b) => { if (downbeats.has(b.toFixed(2))) g.fillRect(Math.round(x(b)), 0, dpr, h); });
  const bars = t.peaks.length;
  const on = audible(t);
  for (let k = 0; k < bars; k++) {
    const sec = (k / bars) * t.buffer.duration;
    const inside = sec >= t.trimStart && sec <= t.trimEnd;
    const amp = Math.max(1, t.peaks[k] * (h / 2 - 3 * dpr));
    g.fillStyle = !inside ? 'rgba(255,255,255,.10)' : on ? t.color : 'rgba(167,171,184,.35)';
    g.fillRect(x(sec), h / 2 - amp, Math.max(1, w / bars * (t.buffer.duration / duration)), amp * 2);
  }
  // обрезанное -- затемнено, края -- ручки
  g.fillStyle = 'rgba(10,11,15,.55)';
  g.fillRect(0, 0, x(t.trimStart), h);
  g.fillRect(x(t.trimEnd), 0, w - x(t.trimEnd), h);
  g.fillStyle = '#d99a4e';
  [t.trimStart, t.trimEnd].forEach((edge) => g.fillRect(Math.round(x(edge)) - 2 * dpr, 6 * dpr, 4 * dpr, h - 12 * dpr));
}

function drawRuler() {
  const c = $('ruler');
  if (!c || !duration) return;
  const g = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  const dpr = window.devicePixelRatio || 1;
  const x = (sec) => (sec / duration) * w;
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
    g.fillRect(x(a), h * 0.5, x(b) - x(a) - dpr, h * 0.5);
    if (x(b) - x(a) > g.measureText(name).width + 6 * dpr) {
      g.fillStyle = '#e8e3da';
      g.fillText(name, x(a) + 3 * dpr, h * 0.75);
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

function movePlayhead() {
  const lane = document.querySelector('.mx-ruler-row .mx-lane');
  if (!lane || !duration) return;
  const board = $('board').getBoundingClientRect();
  const box = lane.getBoundingClientRect();
  const left = box.left - board.left + (position() / duration) * box.width;
  $('playhead').style.transform = `translateX(${left}px)`;
  $('now').textContent = clock(position());
}

// ---------------------------------------------------------------- звук

function position() {
  return playing ? Math.min(duration, startPos + (ctx.currentTime - startTime)) : pos;
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
    const from = Math.max(p, t.trimStart);
    const to = Math.min(t.trimEnd, t.buffer.duration);
    if (to - from <= 0.01) return;
    const src = ctx.createBufferSource();
    src.buffer = t.buffer;
    src.connect(t.gain);
    src.start(t0 + (from - p), from, to - from);
    sources.push(src);
  });
  startTime = t0;
  startPos = p;
  playing = true;
  nextBeat = beats.findIndex((b) => b >= p - 0.001);
  if (nextBeat < 0) nextBeat = beats.length;
  $('play').innerHTML = ICON.pause;
  $('play').classList.add('on');
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
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.frequency.value = accent ? 1760 : 1100;
  g.gain.setValueAtTime(0.0001, when);
  g.gain.exponentialRampToValueAtTime(accent ? 1 : 0.6, when + 0.002);
  g.gain.exponentialRampToValueAtTime(0.0001, when + 0.06);
  o.connect(g);
  g.connect(metroGain);
  o.start(when);
  o.stop(when + 0.07);
}

// Планировщик: щелчки ставятся на доли песни чуть заранее (lookahead),
// повтор куска и конец трека -- здесь же. setInterval, а не кадры анимации:
// в фоновой вкладке кадры встают, а метроном и повтор должны идти.
setInterval(() => {
  if (!playing || !ctx) return;
  const now = position();
  if (metroOn) {
    while (nextBeat < beats.length && beats[nextBeat] < now + 0.15) {
      const b = beats[nextBeat];
      if (b >= startPos - 0.001 && (!loop.on || b < loop.b)) {
        click(startTime + (b - startPos), downbeats.has(b.toFixed(2)));
      }
      nextBeat += 1;
    }
  }
  if (loop.on && loop.b > loop.a && now >= loop.b) startAt(loop.a, 0.02);
  else if (now >= duration) { pause(); pos = 0; movePlayhead(); }
}, 25);

(function frame() {
  if (playing) movePlayhead();
  requestAnimationFrame(frame);
}());

// ---------------------------------------------------------------- мышь

function secAt(e, el) {
  const box = el.getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * duration;
}

document.addEventListener('pointerdown', (e) => {
  const wave = e.target.closest('[data-wave]');
  if (wave && duration) {
    const t = tracks[wave.dataset.wave];
    const sec = secAt(e, wave);
    const px = (s) => (s / duration) * wave.getBoundingClientRect().width;
    const grab = Math.abs(px(sec) - px(t.trimStart)) < 10 ? 'trimStart'
      : Math.abs(px(sec) - px(t.trimEnd)) < 10 ? 'trimEnd' : null;
    if (!grab) { seek(sec); return; }
    wave.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const s = secAt(ev, wave);
      if (grab === 'trimStart') t.trimStart = Math.max(0, Math.min(s, t.trimEnd - 0.5));
      else t.trimEnd = Math.min(t.buffer.duration, Math.max(s, t.trimStart + 0.5));
      drawTrack(t, Number(wave.dataset.wave));
    };
    const up = () => {
      wave.removeEventListener('pointermove', move);
      wave.removeEventListener('pointerup', up);
      save();
      if (playing) startAt(position());
    };
    wave.addEventListener('pointermove', move);
    wave.addEventListener('pointerup', up);
    return;
  }
  const ruler = e.target.closest('#ruler');
  if (ruler && duration) {
    const from = secAt(e, ruler);
    ruler.setPointerCapture(e.pointerId);
    let dragged = false;
    const move = (ev) => {
      const to = secAt(ev, ruler);
      if (Math.abs(to - from) < duration * 0.004) return;
      dragged = true;
      loop.a = Math.min(from, to);
      loop.b = Math.max(from, to);
      drawRuler();
    };
    const up = () => {
      ruler.removeEventListener('pointermove', move);
      ruler.removeEventListener('pointerup', up);
      if (!dragged) { seek(from); return; }
      snapLoop();
      loop.on = true;
      pressed('loop', true);
      drawRuler();
      seek(loop.a);
      toast(`Повтор: ${clock(loop.a)} – ${clock(loop.b)}. Выключить — кнопка «Повтор»`);
    };
    ruler.addEventListener('pointermove', move);
    ruler.addEventListener('pointerup', up);
  }
});

// Края повтора -- к ближайшим долям: кусок звучит ровно, без рваного начала
function snapLoop() {
  if (!beats.length) return;
  const near = (s) => beats.reduce((best, b) => (Math.abs(b - s) < Math.abs(best - s) ? b : best), beats[0]);
  loop.a = near(loop.a);
  loop.b = Math.max(near(loop.b), loop.a + 0.5);
}

document.addEventListener('click', (e) => {
  const mute = e.target.closest('[data-mute]');
  const solo = e.target.closest('[data-solo]');
  const remove = e.target.closest('[data-remove]');
  if (mute) { const t = tracks[mute.dataset.mute]; t.mute = !t.mute; mute.setAttribute('aria-pressed', t.mute); }
  if (solo) { const t = tracks[solo.dataset.solo]; t.solo = !t.solo; solo.setAttribute('aria-pressed', t.solo); }
  if (remove) {
    const [t] = tracks.splice(Number(remove.dataset.remove), 1);
    t.gain.disconnect();
    duration = Math.max(...tracks.map((x) => x.buffer.duration));
    $('total').textContent = clock(duration);
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
  if (metroGain) metroGain.gain.value = Number($('metroVol').value) / 100;
  save();
});
$('loop').addEventListener('click', () => {
  if (!(loop.b > loop.a)) { toast('Выделите кусок мышью на линейке с тактами над дорожками'); return; }
  loop.on = !loop.on;
  pressed('loop', loop.on);
  drawRuler();
});
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
  try {
    const buffer = await ctx.decodeAudioData(await chosen.arrayBuffer());
    const name = chosen.name.replace(/\.[^.]+$/, '').slice(0, 40) || 'Своя дорожка';
    tracks.push(makeTrack(name, '', buffer, {}, true));
    duration = Math.max(duration, buffer.duration);
    $('total').textContent = clock(duration);
    render();
    if (playing) startAt(position());
    toast('Дорожка добавлена — она живёт только в этом окне, на сервер не отправляется');
  } catch (error) {
    toast('Этот файл не получилось прочитать — попробуйте mp3 или wav');
  }
});

$('exportMix').addEventListener('click', async () => {
  if (!tracks.length) return;
  $('exportMix').disabled = true;
  toast('Сводим микс…');
  try {
    const rate = ctx.sampleRate;
    const offline = new OfflineAudioContext(2, Math.ceil(duration * rate), rate);
    tracks.forEach((t) => {
      if (!audible(t)) return;
      const g = offline.createGain();
      g.gain.value = t.vol / 100;
      g.connect(offline.destination);
      const src = offline.createBufferSource();
      src.buffer = t.buffer;
      src.connect(g);
      src.start(t.trimStart, t.trimStart, Math.max(0, t.trimEnd - t.trimStart));
    });
    const rendered = await offline.startRendering();
    const blob = wav(rendered);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(job && job.name || 'naslux').replace(/\.[^.]+$/, '')} — микс.wav`;
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
