// Плеер: дорожка едет под неподвижной линией воспроизведения.
//
// Позиция считается от audio.currentTime, а не накоплением в таймере:
// таймер неизбежно расходится со звуком, а currentTime — это и есть
// настоящее положение в записи.

const $ = (id) => document.getElementById(id);
const PX_PER_SEC = 150;      // масштаб дорожки
const HEAD_X = 180;          // положение линии воспроизведения

const jobId = location.pathname.split('/').pop();
let data = null;
let chordEls = [];
let fretEls = [];
let clock = null;            // источник времени: аудио или синтезатор

const STRING_LABELS = {
  6: ['e', 'B', 'G', 'D', 'A', 'E'],
  7: ['e', 'B', 'G', 'D', 'A', 'E', 'B'],
  4: ['G', 'D', 'A', 'E'],
  5: ['G', 'D', 'A', 'E', 'B'],
};

function mmss(seconds) {
  if (!isFinite(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function load() {
  const job = await (await fetch(`/api/job/${jobId}`)).json();
  if (job.status !== 'done') {
    $('title').textContent = 'Разбор ещё не готов';
    $('meta').textContent = job.stage || job.status;
    return;
  }
  data = job.result;
  $('title').textContent = job.name;
  const p = data.player;
  $('meta').textContent =
    `${p.tuning} · темп ${p.tempo} · тактов ${p.measures.length} · аккордов ${p.chords.length}`;
  $('tabText').textContent = data.tab;
  $('summary').innerHTML = (data.summary || []).map((s) => `<div>${s}</div>`).join('');

  const kinds = { gp5: 'Guitar Pro (.gp5)', txt: 'Текстовые табы (.txt)', mid: 'MIDI (.mid)' };
  $('files').innerHTML = Object.entries(data.files)
    .filter(([, has]) => has)
    .map(([kind]) => `<a href="/api/file/${jobId}/${kind}"><button>${kinds[kind]}</button></a>`)
    .join('');

  // Браузер не умеет играть .mid, поэтому для MIDI-исходника ноты
  // синтезируются на месте через Web Audio.
  if (data.hasAudio) {
    $('audio').src = data.audio;
    clock = audioClock($('audio'));
  } else {
    clock = synthClock(p);
    $('meta').textContent += ' · звук синтезирован из нот';
  }
  buildLanes(p);
  bindControls();
  requestAnimationFrame(tick);
}

function buildLanes(p) {
  // аккорды
  const track = $('chordTrack');
  track.innerHTML = '';
  chordEls = p.chords.map((chord) => {
    const el = document.createElement('div');
    el.className = 'chord';
    el.textContent = chord.name;
    el.style.left = `${chord.start * PX_PER_SEC}px`;
    el.style.width = `${Math.max(62, (chord.end - chord.start) * PX_PER_SEC - 6)}px`;
    track.appendChild(el);
    return { el, chord };
  });
  track.style.width = `${(p.duration + 4) * PX_PER_SEC}px`;

  // линии струн и подписи
  const laneHeight = $('tabLane').clientHeight || 190;
  const count = p.strings;
  const step = laneHeight / (count + 1);
  const lines = $('stringLines');
  lines.innerHTML = '';
  const labels = STRING_LABELS[count] || Array(count).fill('•');
  for (let row = 0; row < count; row++) {
    const y = step * (row + 1);
    const line = document.createElement('i');
    line.style.top = `${y}px`;
    lines.appendChild(line);
    const label = document.createElement('div');
    label.className = 'slabel';
    label.style.top = `${y}px`;
    label.textContent = labels[row];
    $('tabLane').appendChild(label);
  }

  // лады и тактовые черты
  const tab = $('tabTrack');
  tab.innerHTML = '';
  fretEls = [];
  p.columns.forEach((column) => {
    column.notes.forEach((note) => {
      const row = count - 1 - note.string;   // сверху самая высокая струна
      const el = document.createElement('div');
      el.className = 'fret';
      el.textContent = note.fret;
      el.style.left = `${column.t * PX_PER_SEC}px`;
      el.style.top = `${step * (row + 1)}px`;
      tab.appendChild(el);
      fretEls.push({ el, start: column.t, end: column.end });
    });
  });
  p.measures.forEach((measure) => {
    const bar = document.createElement('div');
    bar.className = 'barline';
    bar.style.left = `${measure.start * PX_PER_SEC}px`;
    tab.appendChild(bar);
    const num = document.createElement('div');
    num.className = 'barnum';
    num.style.left = `${measure.start * PX_PER_SEC + 4}px`;
    num.textContent = measure.number;
    tab.appendChild(num);
  });
  tab.style.width = track.style.width;
}

// --- два источника времени с одинаковым интерфейсом ---------------------

function audioClock(audio) {
  return {
    get time() { return audio.currentTime || 0; },
    set time(v) { audio.currentTime = Math.max(0, v); },
    get duration() { return audio.duration || 0; },
    get paused() { return audio.paused; },
    play: () => audio.play(),
    pause: () => audio.pause(),
    setRate: (r) => { audio.playbackRate = r; },
    onState: (fn) => { audio.onplay = fn; audio.onpause = fn; },
  };
}

function synthClock(player) {
  // Щипок струны: две расстроенные пилы через полосовой фильтр и
  // экспоненциальное затухание. Без сэмплов и без загрузок.
  let ctx = null;
  let startedAt = 0;      // время ctx в момент запуска
  let offset = 0;         // позиция в песне на момент запуска
  let playing = false;
  let rate = 1;
  let timer = null;
  let listener = () => {};
  const duration = player.duration + 0.5;

  const freq = (pitch) => 440 * Math.pow(2, (pitch - 69) / 12);

  function pluck(when, pitch, seconds, velocity) {
    const gain = ctx.createGain();
    const level = 0.09 * (0.35 + 0.65 * (velocity || 90) / 127);
    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(level, when + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + Math.max(0.25, seconds));
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(3600, when);
    filter.frequency.exponentialRampToValueAtTime(900, when + Math.max(0.25, seconds));
    gain.connect(filter).connect(ctx.destination);
    [0, 0.6].forEach((detune) => {
      const osc = ctx.createOscillator();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(freq(pitch), when);
      osc.detune.setValueAtTime(detune * 8, when);
      osc.connect(gain);
      osc.start(when);
      osc.stop(when + Math.max(0.3, seconds) + 0.05);
    });
  }

  function schedule() {
    // планируем на 1.5 секунды вперёд, чтобы не держать тысячи узлов сразу
    const horizon = 1.5;
    const now = position();
    for (const column of player.columns) {
      if (column._done) continue;
      if (column.t < now - 0.05) { column._done = true; continue; }
      if (column.t > now + horizon) break;
      const when = startedAt + (column.t - offset) / rate;
      column.notes.forEach((n) =>
        pluck(when, n.pitch, (column.end - column.t) / rate, n.vel));
      column._done = true;
    }
  }

  function position() {
    if (!playing) return offset;
    return offset + (ctx.currentTime - startedAt) * rate;
  }

  return {
    get time() { return Math.min(position(), duration); },
    set time(v) {
      const was = playing;
      this.pause();
      offset = Math.max(0, v);
      player.columns.forEach((c) => { c._done = c.t < offset; });
      if (was) this.play();
    },
    get duration() { return duration; },
    get paused() { return !playing; },
    play() {
      if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
      ctx.resume();
      startedAt = ctx.currentTime + 0.05;
      playing = true;
      timer = setInterval(schedule, 200);
      schedule();
      listener();
    },
    pause() {
      if (!playing) return;
      offset = position();
      playing = false;
      clearInterval(timer);
      listener();
    },
    setRate(r) {
      const was = playing;
      this.pause();
      rate = r;
      if (was) this.play();
    },
    onState(fn) { listener = fn; },
  };
}

function bindControls() {
  $('play').onclick = () => (clock.paused ? clock.play() : clock.pause());
  clock.onState(() => {
    $('play').textContent = clock.paused ? '▶ Играть' : '❚❚ Пауза';
  });
  $('rate').onchange = () => clock.setRate(parseFloat($('rate').value));
  $('seek').oninput = () => {
    if (clock.duration) clock.time = ($('seek').value / 1000) * clock.duration;
  };
  document.addEventListener('keydown', (e) => {
    if (e.code === 'Space') { e.preventDefault(); clock.paused ? clock.play() : clock.pause(); }
    if (e.code === 'ArrowLeft') clock.time = Math.max(0, clock.time - 5);
    if (e.code === 'ArrowRight') clock.time = clock.time + 5;
  });
}

function tick() {
  const now = clock ? clock.time : 0;
  const shift = HEAD_X - now * PX_PER_SEC;
  $('chordTrack').style.transform = `translateX(${shift}px)`;
  $('tabTrack').style.transform = `translateX(${shift}px)`;

  // текущий аккорд и следующий за ним
  let current = null;
  let upcoming = null;
  for (const item of chordEls) {
    const active = now >= item.chord.start && now < item.chord.end;
    item.el.classList.toggle('now', active);
    item.el.classList.remove('next');
    if (active) current = item;
    else if (!upcoming && item.chord.start > now) upcoming = item;
  }
  if (upcoming && $('count').checked) upcoming.el.classList.add('next');

  const head = current ? current.chord.name : '—';
  const ahead = upcoming && $('count').checked
    ? `  →  ${upcoming.chord.name} через ${(upcoming.chord.start - now).toFixed(1)} с`
    : '';
  $('nowChord').textContent = head;
  $('nowChord').title = ahead;
  document.title = current ? `${current.chord.name} · MidiToTab` : 'MidiToTab — плеер';

  for (const item of fretEls) {
    item.el.classList.toggle('now', now >= item.start && now < item.end);
  }

  $('time').textContent = `${mmss(now)} / ${mmss(clock.duration)}`;
  if (clock.duration) $('seek').value = (now / clock.duration) * 1000;

  requestAnimationFrame(tick);
}

load();
