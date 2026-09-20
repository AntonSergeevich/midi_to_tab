// Плеер: аккордовая лента, метроном по найденным долям, табы по запросу.

const $ = (id) => document.getElementById(id);
const PX_PER_SEC = 128;
const jobId = location.pathname.split('/').pop();

let data = null;
let ribs = [];
let fretEls = [];
let tabData = null;
let clock = null;
let metro = null;
let lyricEls = [];

const STRING_LABELS = {
  6: ['e', 'B', 'G', 'D', 'A', 'E'],
  7: ['e', 'B', 'G', 'D', 'A', 'E', 'B'],
  4: ['G', 'D', 'A', 'E'],
  5: ['G', 'D', 'A', 'E', 'B'],
};

const mmss = (s) => (!isFinite(s) ? '0:00'
  : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`);

// ---------------------------------------------------------------- загрузка

async function load() {
  const job = await (await fetch(`/api/job/${jobId}`)).json();
  if (job.status !== 'done') {
    $('title').textContent = 'Разбор ещё не готов';
    $('meta').textContent = job.stage || job.status;
    setTimeout(load, 1500);
    return;
  }
  data = job.result;
  $('title').textContent = job.name;
  const detected = data.tempoDetected ? ' (определён автоматически)' : '';
  $('meta').textContent =
    `Темп ${data.tempo}${detected} · аккордов ${data.chords.length} · партий ${data.parts.length}`;

  $('audio').src = data.audio;
  clock = audioClock($('audio'));
  metro = metronome(data.beats || [], data.downbeats || []);

  buildRibbon();
  buildParts();
  buildLyrics();
  bindControls();
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------- лента аккордов

function buildRibbon() {
  const track = $('ribTrack');
  track.innerHTML = '';
  if (!data.chords.length) {
    $('ribbon').innerHTML =
      '<p class="muted" style="padding:24px;text-align:center">' +
      'Аккорды не распознаны. Для MIDI-файлов лента не строится.</p>';
    ribs = [];
    return;
  }
  ribs = data.chords.map((chord) => {
    const el = document.createElement('div');
    el.className = 'rib';
    const shaky = chord.confidence < 0.55;
    el.innerHTML = `<div>${chord.name}` +
      (shaky ? '<small class="low-conf">не уверен</small>' : '<small></small>') + '</div>';
    el.style.left = `${((chord.start + chord.end) / 2) * PX_PER_SEC}px`;
    track.appendChild(el);
    return { el, chord };
  });
  track.style.width = `${(data.chords[data.chords.length - 1].end + 8) * PX_PER_SEC}px`;
}

// ------------------------------------------------------------------ партии

function buildParts() {
  $('parts').innerHTML = '';
  data.parts.forEach((part) => {
    const row = document.createElement('div');
    row.className = 'part';
    row.innerHTML = `
      <span class="name">${part.label}</span>
      <button data-act="listen">Слушать</button>
      <span class="spacer"></span>
      <span class="muted" data-role="status"></span>
      <button class="primary" data-act="tabs">Создать MIDI и табы</button>`;
    $('parts').appendChild(row);

    row.querySelector('[data-act="listen"]').onclick = () => {
      document.querySelectorAll('.part').forEach((p) => p.classList.remove('active'));
      row.classList.add('active');
      const wasPlaying = !clock.paused;
      clock.pause();
      $('audio').src = part.audio;
      if (wasPlaying) clock.play();
    };

    const button = row.querySelector('[data-act="tabs"]');
    const status = row.querySelector('[data-role="status"]');
    button.onclick = async () => {
      button.disabled = true;
      status.textContent = 'ставлю в очередь…';
      const response = await fetch(`/api/job/${jobId}/tabs/${part.key}`, { method: 'POST' });
      if (!response.ok) {
        status.innerHTML = `<span class="bad">${(await response.json()).detail}</span>`;
        button.disabled = false;
        return;
      }
      watchTabs((await response.json()).jobId, status, button, part.label);
    };
  });
}

function watchTabs(childId, status, button, label) {
  const timer = setInterval(async () => {
    const job = await (await fetch(`/api/job/${childId}`)).json();
    status.textContent = job.stage || job.status;
    if (job.status === 'done') {
      clearInterval(timer);
      status.innerHTML = '<span class="ok">готово</span>';
      button.disabled = false;
      showTabs(job.result, childId, label);
    } else if (job.status === 'error') {
      clearInterval(timer);
      status.innerHTML = `<span class="bad">${job.error}</span>`;
      button.disabled = false;
    }
  }, 1200);
}

function showTabs(result, childId, label) {
  tabData = result.tab;
  $('tabHint').style.display = 'none';
  $('tabCard').style.display = '';
  $('tabCard').querySelector('h2').textContent = ` Табулатура — ${label} `;
  $('tabText').textContent = result.tabText;
  $('tabSummary').innerHTML = (result.summary || []).map((s) => `<div>${s}</div>`).join('');
  const kinds = { gp5: 'Guitar Pro (.gp5)', txt: 'Текстовые табы (.txt)', mid: 'MIDI (.mid)' };
  $('tabFiles').innerHTML = Object.entries(result.files)
    .filter(([, has]) => has)
    .map(([k]) => `<a href="/api/file/${childId}/${k}"><button>${kinds[k]}</button></a>`)
    .join('');
  buildTabLane();
  document.querySelector('.stage').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function buildTabLane() {
  const count = tabData.strings;
  const laneHeight = $('tabLane').clientHeight || 190;
  const step = laneHeight / (count + 1);
  const labels = STRING_LABELS[count] || Array(count).fill('•');

  const lines = $('stringLines');
  lines.innerHTML = '';
  $('tabLane').querySelectorAll('.slabel').forEach((e) => e.remove());
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

  const tab = $('tabTrack');
  tab.innerHTML = '';
  fretEls = [];
  tabData.columns.forEach((column) => {
    column.notes.forEach((note) => {
      const el = document.createElement('div');
      el.className = 'fret';
      el.textContent = note.fret;
      el.style.left = `${column.t * PX_PER_SEC}px`;
      el.style.top = `${step * (count - note.string)}px`;
      tab.appendChild(el);
      fretEls.push({ el, start: column.t, end: column.end });
    });
  });
  tabData.measures.forEach((measure) => {
    const bar = document.createElement('div');
    bar.className = 'barline';
    bar.style.left = `${measure.start * PX_PER_SEC}px`;
    tab.appendChild(bar);
  });
  tab.style.width = `${(tabData.duration + 6) * PX_PER_SEC}px`;
}

// ------------------------------------------------------------- текст песни

function buildLyrics() {
  if (data.lyrics) {
    renderLyrics(data.lyrics, data.lyricsSource);
    return;
  }
  $('makeLyrics').onclick = async () => {
    $('makeLyrics').disabled = true;
    $('lyricsStatus').textContent = 'ставлю в очередь…';
    const response = await fetch(`/api/job/${jobId}/lyrics`, { method: 'POST' });
    if (!response.ok) {
      $('lyricsStatus').innerHTML =
        `<span class="bad">${(await response.json()).detail}</span>`;
      $('makeLyrics').disabled = false;
      return;
    }
    const timer = setInterval(async () => {
      const job = await (await fetch(`/api/job/${jobId}`)).json();
      $('lyricsStatus').textContent = job.stage || '';
      if (job.result && job.result.lyrics) {
        clearInterval(timer);
        renderLyrics(job.result.lyrics, job.result.lyricsSource);
      } else if ((job.stage || '').startsWith('Текст не распознан')) {
        clearInterval(timer);
        $('makeLyrics').disabled = false;
      }
    }, 2000);
  };
}

function renderLyrics(lyrics, source) {
  $('lyricsBox').style.display = 'none';
  const box = $('lyricsLines');
  box.style.display = '';
  box.innerHTML =
    `<p class="muted" style="margin:0 0 10px">Язык: ${lyrics.language || '—'}` +
    (source ? ` · источник: ${source}` : '') + '</p>';
  lyricEls = lyrics.lines.map((line) => {
    const el = document.createElement('div');
    el.textContent = line.text;
    el.style.cssText =
      'padding:5px 0;color:var(--muted);cursor:pointer;transition:color .15s,font-size .15s';
    el.onclick = () => { clock.time = line.start; };
    box.appendChild(el);
    return { el, line };
  });
}

// ------------------------------------------------------------------ время

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

// Метроном щёлкает по НАЙДЕННЫМ долям, а не по среднему темпу:
// живая игра всегда чуть плывёт, и отсчёт от BPM разъезжается с записью.
function metronome(beats, downbeats) {
  let ctx = null;
  let on = false;
  let index = 0;
  const strong = new Set(downbeats.map((b) => b.toFixed(2)));

  function click(strongBeat) {
    if (!ctx) return;
    const when = ctx.currentTime + 0.01;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = strongBeat ? 1600 : 1000;
    gain.gain.setValueAtTime(strongBeat ? 0.28 : 0.15, when);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + 0.06);
    osc.connect(gain).connect(ctx.destination);
    osc.start(when);
    osc.stop(when + 0.08);
  }

  return {
    get enabled() { return on; },
    toggle() {
      on = !on;
      if (on && !ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx) ctx.resume();
      return on;
    },
    sync(now) {
      if (!on || !beats.length) return;
      if (index >= beats.length || beats[index] > now + 0.4) {
        index = beats.findIndex((b) => b >= now - 0.05);
        if (index < 0) index = beats.length;
      }
      while (index < beats.length && beats[index] <= now + 0.02) {
        click(strong.has(beats[index].toFixed(2)));
        index++;
      }
    },
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
  $('metro').onclick = () => {
    const on = metro.toggle();
    $('metro').classList.toggle('primary', on);
    $('metro').textContent = on ? '🥁 Метроном вкл' : '🥁 Метроном';
  };
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.code === 'Space') { e.preventDefault(); clock.paused ? clock.play() : clock.pause(); }
    if (e.code === 'ArrowLeft') clock.time = clock.time - 5;
    if (e.code === 'ArrowRight') clock.time = clock.time + 5;
    if (e.code === 'KeyM') $('metro').click();
  });
}

// --------------------------------------------------------------------- кадр

function tick() {
  // При уходе со страницы кадр может успеть выполниться, когда элементов
  // уже нет: тогда в консоль сыплются ошибки на ровном месте.
  const ribbon = $('ribbon');
  if (!ribbon) return;

  const now = clock ? clock.time : 0;
  // Одна и та же вертикаль и один и тот же сдвиг для обеих лент
  const centre = ribbon.clientWidth / 2;
  const shift = centre - now * PX_PER_SEC;
  $('nowLine').style.left = `${centre}px`;
  $('ribTrack').style.transform = `translateX(${shift}px)`;

  // Размер подписи зависит от того, насколько она близка к текущему моменту
  ribs.forEach(({ el, chord }) => {
    const current = now >= chord.start && now < chord.end;
    const near = !current && Math.abs((chord.start + chord.end) / 2 - now) < 4;
    el.classList.toggle('current', current);
    el.classList.toggle('near', near);
    el.classList.toggle('past', chord.end <= now);
  });

  if (tabData) {
    $('tabTrack').style.transform = `translateX(${shift}px)`;
    fretEls.forEach((f) => f.el.classList.toggle('now', now >= f.start && now < f.end));
  }

  // текущая строка текста крупнее и ярче -- её же можно нажать и перейти
  lyricEls.forEach(({ el, line }) => {
    const active = now >= line.start && now < line.end;
    el.style.color = active ? 'var(--accent)' : 'var(--muted)';
    el.style.fontSize = active ? '18px' : '15px';
    el.style.fontWeight = active ? '600' : '400';
  });

  metro.sync(now);
  $('time').textContent = `${mmss(now)} / ${mmss(clock.duration)}`;
  if (clock.duration) $('seek').value = (now / clock.duration) * 1000;
  requestAnimationFrame(tick);
}

load();
