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
    $('title').textContent = job.name || 'Разбор ещё не готов';
    const percent = Math.round(job.progress || 0);
    $('meta').innerHTML = job.status === 'error'
      ? `<span class="bad">${job.error}</span>`
      : `${job.stage || job.status} — ${percent}%` +
        `<div class="bar done" style="margin-top:8px"><i style="width:${percent}%"></i></div>`;
    if (job.status !== 'error') setTimeout(load, 1500);
    return;
  }
  data = job.result;
  $('title').textContent = job.name;
  const detected = data.tempoDetected ? ' (определён автоматически)' : '';
  const source = data.chordSource ? ` (${data.chordSource})` : '';
  const circle = [...new Set(data.chords.map((c) => c.name))];
  $('meta').textContent =
    (data.key ? `Тональность ${data.key} · ` : '') +
    `темп ${data.tempo}${detected} · партий ${data.parts.length}` +
    (circle.length ? ` · круг: ${circle.join(' ')}${source}` : '');

  $('audio').src = data.audio;
  watchAudioErrors(data);
  clock = audioClock($('audio'));
  metro = metronome(...beatGrid(data));

  buildRibbon();
  buildParts();
  buildSplit();
  buildMade(job.made || []);
  buildLyrics();
  buildGrips();
  bindControls();
  bindDrag();
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
    // Метки "не уверен" здесь больше нет. На трёх размеченных песнях она
    // ловила ноль процентов настоящих ошибок, зато висела на каждом
    // пятом ВЕРНОМ аккорде -- в том числе на очевидном до-мажоре.
    // Предупреждение, которое не предупреждает, хуже его отсутствия:
    // человек либо перестаёт ему верить, либо зря сомневается в себе.
    el.innerHTML = `<div>${chord.name}<small></small></div>`;
    el.style.left = `${((chord.start + chord.end) / 2) * PX_PER_SEC}px`;
    // Нажатие по подписи переносит воспроизведение к ней: так возвращаются
    // к месту, которое не выходит, не целясь в ползунок.
    el.onclick = () => { clock.time = chord.start; };
    track.appendChild(el);
    return { el, chord };
  });

  // Границы тактов на ленте: по ним видно, где аккорд начинается, а
  // не только какой он. Без этого подпись висит в пустоте.
  (data.downbeats || []).forEach((moment, index) => {
    const line = document.createElement('div');
    line.className = 'ribbar';
    line.style.left = `${moment * PX_PER_SEC}px`;
    line.dataset.n = index + 1;
    track.appendChild(line);
  });
  track.style.width = `${(data.chords[data.chords.length - 1].end + 8) * PX_PER_SEC}px`;
}

// ------------------------------------------------------------------ партии

function buildParts() {
  $('parts').innerHTML = '';
  // Табы из полного микса бесполезны: Basic Pitch слышит вокал и
  // барабаны наравне с гитарой, и всё это ложится на один гриф.
  const canSplit = data.parts.every((p) => p.key === 'full') && !data.isMidi;
  data.parts.forEach((part) => {
    const row = document.createElement('div');
    row.className = 'part';
    row.innerHTML = `
      <span class="name">${part.label}</span>
      <button data-act="listen">Слушать</button>
      <a href="${part.audio}" download><button>Скачать</button></a>
      <span class="spacer"></span>
      <span class="muted" data-role="status"></span>
      ${part.key === 'full' && !data.isMidi && canSplit
        ? '<span class="muted">табы — после разделения на партии</span>'
        : '<button class="primary" data-act="tabs">Создать MIDI и табы</button>'}`;
    $('parts').appendChild(row);

    row.querySelector('[data-act="listen"]').onclick = () => {
      document.querySelectorAll('.part').forEach((p) => p.classList.remove('active'));
      row.classList.add('active');
      const wasPlaying = !clock.paused;
      clock.pause();
      // MIDI браузер не играет -- слушаем озвучку (её отдаёт адрес звука разбора)
      $('audio').src = data.isMidi ? data.audio : part.audio;
      if (wasPlaying) clock.play();
    };

    const button = row.querySelector('[data-act="tabs"]');
    const status = row.querySelector('[data-role="status"]');
    if (!button) return;
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

// Разделение вторым заходом: сначала берут аккорды, партии нужны позже.
function buildSplit() {
  const split = data.parts.every((p) => p.key === 'full');
  if (!split || data.isMidi) return;
  $('splitBox').style.display = '';
  $('split').onclick = async () => {
    $('split').disabled = true;
    $('splitStatus').textContent = 'ставлю в очередь…';
    const response = await fetch(`/api/job/${jobId}/separate`, { method: 'POST' });
    if (!response.ok) {
      $('splitStatus').innerHTML = `<span class="bad">${(await response.json()).detail}</span>`;
      $('split').disabled = false;
      return;
    }
    const timer = setInterval(async () => {
      const job = await (await fetch(`/api/job/${jobId}`)).json();
      $('splitStatus').textContent =
        `${job.stage || job.status} — ${Math.round(job.progress || 0)}%`;
      if (job.status === 'done') {
        clearInterval(timer);
        location.reload();   // партии, аккорды и файлы -- всё заново
      } else if (job.status === 'error') {
        clearInterval(timer);
        $('splitStatus').innerHTML = `<span class="bad">${job.error}</span>`;
        $('split').disabled = false;
      }
    }, 2000);
  };
}

// Табы, сделанные в прошлый заход. Без этого человек возвращался к треку,
// видел пустую страницу и запускал распознавание заново -- поверх уже
// готовых файлов, которые всё это время лежали на диске.
const FILE_NAMES = { gp5: 'Guitar Pro (.gp5)', txt: 'Табы (.txt)', mid: 'MIDI (.mid)' };

function buildMade(made) {
  const ready = made.filter((m) => m.status === 'done' && m.files.length);
  if (!ready.length) return;
  $('madeCard').style.display = '';
  $('madeList').innerHTML = ready.map((m) => `
    <div class="part">
      <span class="name">${m.label || m.stem}</span>
      <span class="spacer"></span>
      ${m.files.map((f) =>
        `<a href="/api/file/${m.id}/${f}" download><button>${FILE_NAMES[f] || f}</button></a>`
      ).join(' ')}
      <button data-show="${m.id}">Показать табы</button>
    </div>`).join('');
  $('madeList').querySelectorAll('[data-show]').forEach((button) => {
    button.onclick = async () => {
      const job = await (await fetch(`/api/job/${button.dataset.show}`)).json();
      if (job.result) showTabs(job.result, button.dataset.show,
        ready.find((m) => m.id === button.dataset.show).label);
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
      fetch(`/api/job/${jobId}`).then((r) => r.json()).then((j) => buildMade(j.made || []));
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
  $('tabFiles').innerHTML = Object.entries(result.files)
    .filter(([, has]) => has)
    .map(([k]) => `<a href="/api/file/${childId}/${k}" download>` +
                  `<button>${FILE_NAMES[k] || k}</button></a>`)
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
  fetch('/api/me').then((r) => r.json()).then((me) => {
    const fill = (id, values, chosen) => {
      $(id).innerHTML = values
        .map((v) => `<option${v === chosen ? ' selected' : ''}>${v}</option>`).join('');
    };
    fill('lyricsLang', me.lyricsLanguages || ['русский'], 'русский');
    fill('lyricsModel', me.lyricsModels || [], (me.lyricsModels || [])[3]);
  });

  $('makeLyrics').onclick = async () => {
    $('makeLyrics').disabled = true;
    $('lyricsStatus').textContent = 'ставлю в очередь…';
    const form = new FormData();
    form.append('model', $('lyricsModel').value);
    form.append('language', $('lyricsLang').value);
    const response = await fetch(`/api/job/${jobId}/lyrics`, { method: 'POST', body: form });
    if (!response.ok) {
      $('lyricsStatus').innerHTML =
        `<span class="bad">${(await response.json()).detail}</span>`;
      $('makeLyrics').disabled = false;
      return;
    }
    const timer = setInterval(async () => {
      const job = await (await fetch(`/api/job/${jobId}`)).json();
      // Разделение занимает минуты, и без процента человек не понимает,
      // идёт работа или всё повисло.
      const percent = Math.round(job.progress || 0);
      $('lyricsStatus').innerHTML = job.stage
        ? `${job.stage} — ${percent}%` +
          `<div class="bar done" style="margin-top:6px;max-width:320px">` +
          `<i style="width:${percent}%"></i></div>`
        : '';
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

// Плавные часы: Safari на iPhone обновляет currentTime ступеньками, и
// метроном по таким часам щёлкал вразнобой с нотами (06.10). Между
// обновлениями время досчитывается по точному таймеру страницы.
function audioClock(audio) {
  let last = { t: -1, at: 0 };
  return {
    get time() {
      const t = audio.currentTime || 0;
      const at = performance.now();
      if (audio.paused || t !== last.t || at - last.at > 500) { last = { t, at }; return t; }
      return t + ((at - last.at) / 1000) * (audio.playbackRate || 1);
    },
    get rate() { return audio.playbackRate || 1; },
    set time(v) { audio.currentTime = Math.max(0, v); last = { t: -1, at: 0 }; },
    get duration() { return audio.duration || 0; },
    get paused() { return audio.paused; },
    play: () => audio.play(),
    pause: () => audio.pause(),
    setRate: (r) => { audio.playbackRate = r; },
    onState: (fn) => { audio.onplay = fn; audio.onpause = fn; },
  };
}

// Доли для метронома: найденные в записи, а если их нет (разборы из MIDI
// до 06.10) -- ровная сетка по темпу, такт на 4
function beatGrid(result) {
  if ((result.beats || []).length) return [result.beats, result.downbeats || []];
  const step = 60 / (result.tempo || 120);
  const end = Math.max(...(result.chords || []).map((c) => c.end), 0) + 4;
  const beats = [];
  for (let t = 0; t < end; t += step) beats.push(Math.round(t * 1000) / 1000);
  return [beats, beats.filter((_, i) => i % 4 === 0)];
}

// Метроном щёлкает по НАЙДЕННЫМ долям, а не по среднему темпу:
// живая игра всегда чуть плывёт, и отсчёт от BPM разъезжается с записью.
function metronome(beats, downbeats) {
  let ctx = null;
  let on = false;
  let index = 0;
  let lastNow = null;
  const strong = new Set(downbeats.map((b) => b.toFixed(2)));

  function click(strongBeat, delay = 0) {
    if (!ctx) return;
    const when = ctx.currentTime + 0.01 + delay;
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
      if (on && !ctx) {
        try { if (navigator.audioSession) navigator.audioSession.type = 'playback'; } catch (error) { /* */ }
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (ctx) ctx.resume();
      lastNow = null;                       // включили -- найти долю заново
      return on;
    },
    // Щелчки планируются заранее (на 0.15 с вперёд) на точное время звуковой
    // карты -- а не «когда кадр заметил долю»: так они ровные и на iPhone
    sync(now, rate = 1) {
      if (!on || !beats.length || !ctx) return;
      // Перемотка (время скакнуло) -- начать с ближайшей доли впереди
      if (lastNow === null || now < lastNow - 0.05 || now > lastNow + 0.5) {
        index = beats.findIndex((b) => b >= now - 0.01);
        if (index < 0) index = beats.length;
      }
      lastNow = now;
      while (index < beats.length && beats[index] <= now + 0.15) {
        const delay = Math.max(0, (beats[index] - now) / rate);
        click(strong.has(beats[index].toFixed(2)), delay);
        index++;
      }
    },
  };
}

// Громкость. На iPhone громкость <audio> из скрипта не меняется, поэтому
// там звук идёт через Web Audio. Но созданный ДО нажатия Web Audio Safari
// держит на паузе -- «Играть» не играла (06.10); и в беззвучном режиме он
// молчит. Поэтому Web Audio -- только на iPhone/iPad, только по нажатию и в
// режиме «воспроизведение» (Safari 16.4+); без этого режима ползунок
// прячется. Остальные устройства -- обычная громкость <audio>.
const IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const IOS_VOLUME = IOS && 'audioSession' in navigator;
let gainNode = null;
let audioContext = null;
let wantedLevel = 1;
function volumeGain() {
  if (gainNode || !IOS_VOLUME) return gainNode;
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return null;
  try {
    navigator.audioSession.type = 'playback';   // играть и в беззвучном режиме
    audioContext = new Context();
    const source = audioContext.createMediaElementSource($('audio'));
    gainNode = audioContext.createGain();
    gainNode.gain.value = wantedLevel;
    source.connect(gainNode).connect(audioContext.destination);
  } catch (e) {
    gainNode = null;
  }
  return gainNode;
}

// Звук не загрузился (на iPhone «Играть» -> NotSupportedError, 06.10):
// пробуем запасной файл (партию целиком), а причину показываем словами --
// что ответил сервер по адресу звука.
function watchAudioErrors(result) {
  const audio = $('audio');
  const spare = [result.studioAudio, ...(result.parts || []).map((p) => p.audio)]
    .filter((url, i, all) => url && url !== result.audio && all.indexOf(url) === i);
  audio.addEventListener('error', async () => {
    const failed = audio.currentSrc || audio.src;
    if (spare.length) { audio.src = spare.shift(); audio.load(); return; }
    let answer = '';
    try {
      const probe = await fetch(failed, { headers: { Range: 'bytes=0-1' } });
      answer = `сервер: ${probe.status}, ${probe.headers.get('content-type') || 'без типа'}`;
    } catch (error) { answer = `сервер не ответил: ${error.name}`; }
    const code = audio.error ? audio.error.code : '?';
    if (!$('audioError')) {
      $('meta').insertAdjacentHTML('beforeend', '<p class="bad" id="audioError" style="margin:8px 0 0"></p>');
    }
    $('audioError').textContent = `Звук не загрузился (код ${code}; ${answer}). Пришлите этот текст — разберёмся.`;
  });
}

// Запуск -- всегда из нажатия: там же будится Web Audio (на iPhone)
function startPlayback() {
  if (IOS_VOLUME && wantedLevel < 1) volumeGain();
  if (audioContext && audioContext.state !== 'running') audioContext.resume().catch(() => {});
  const started = clock.play();
  if (started && started.catch) {
    started.catch((error) => {
      $('meta').insertAdjacentHTML('beforeend',
        `<p class="bad" style="margin:8px 0 0">Не получилось включить звук (${error.name}). Обновите страницу и нажмите «Играть» ещё раз.</p>`);
    });
  }
}

function bindControls() {
  $('play').onclick = () => (clock.paused ? startPlayback() : clock.pause());
  clock.onState(() => {
    $('play').textContent = clock.paused ? '▶ Играть' : '❚❚ Пауза';
  });
  $('rate').onchange = () => clock.setRate(parseFloat($('rate').value));

  // Громкость запоминается: разбирают песни подолгу, и каждый раз
  // подкручивать ползунок заново -- раздражает.
  // На iPhone и iPad громкость <audio> из скрипта не меняется вовсе (Safari
  // держит volume = 1) -- ползунок «не работал». Поэтому звук идёт через
  // Web Audio: элемент -> усилитель -> выход; усилитель слушается везде.
  if (IOS && !IOS_VOLUME) $('vol').closest('label, div').style.display = 'none';
  const volume = (percent, touched) => {
    const level = Math.max(0, Math.min(1, percent / 100));
    wantedLevel = level;
    if (!IOS) {
      $('audio').volume = level;
    } else if (touched || gainNode) {
      const gain = volumeGain();               // из движения ползунка -- это нажатие
      if (gain) gain.gain.value = level;
      if (audioContext && audioContext.state !== 'running') audioContext.resume().catch(() => {});
    }
    $('volValue').textContent = `${percent}%`;
    try { localStorage.setItem('naslux.volume', percent); } catch (e) { /* режим инкогнито */ }
  };
  let saved = 100;
  try { saved = parseInt(localStorage.getItem('naslux.volume'), 10) || 100; } catch (e) { /* */ }
  $('vol').value = saved;
  volume(saved);
  $('vol').oninput = () => volume(parseInt($('vol').value, 10), true);
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
    if (e.code === 'Space') { e.preventDefault(); clock.paused ? startPlayback() : clock.pause(); }
    if (e.code === 'ArrowLeft') clock.time = clock.time - 5;
    if (e.code === 'ArrowRight') clock.time = clock.time + 5;
    if (e.code === 'KeyM') $('metro').click();
  });
}

// --------------------------------------------------------------------- кадр

// ------------------------------------------------------------ аппликатуры

// Название аккорда мало что даёт, если не помнишь, как он берётся --
// а за этим сервис и открывают. Сетку рисуем на SVG: она должна
// оставаться чёткой и на телефоне, и при увеличении страницы.
const STRINGS_LABEL = { 6: 'EADGBE', 7: 'BEADGBE', 4: 'GCEA' };
let gripEls = [];

function grip(name, shape) {
  const strings = shape.frets.length;
  const rows = 5;                       // сколько ладов в окошке
  const step = 20;                      // расстояние между струнами
  const w = step * (strings - 1);
  const h = 19 * rows;
  const px = 14, py = 26;
  const parts = [];

  // Лады и струны. Верхний порожек рисуется толстым -- по нему и видно,
  // что аккорд берётся в первой позиции, а не где-то посреди грифа.
  for (let r = 0; r <= rows; r++) {
    const y = py + (h / rows) * r;
    const nut = r === 0 && shape.base <= 1;
    parts.push(`<line x1="${px}" y1="${y}" x2="${px + w}" y2="${y}"
      stroke="${nut ? 'var(--muted)' : 'var(--border)'}"
      stroke-width="${nut ? 3.5 : 1}"/>`);
  }
  for (let c = 0; c < strings; c++) {
    const x = px + step * c;
    parts.push(`<line x1="${x}" y1="${py}" x2="${x}" y2="${py + h}"
      stroke="var(--border)" stroke-width="1"/>`);
  }

  // Баррэ -- одной полосой, а не шестью точками: так его и показывают
  if (shape.barre) {
    const row = shape.barre - shape.base;
    const y = py + (h / rows) * (row + 0.5);
    parts.push(`<rect x="${px - 5}" y="${y - 6.5}" width="${w + 10}" height="13"
      rx="6.5" fill="var(--accent)" opacity=".9"/>`);
  }

  shape.frets.forEach((fret, index) => {
    const x = px + step * index;
    if (fret === null) {
      parts.push(`<text x="${x}" y="${py - 8}" text-anchor="middle"
        font-size="13" fill="var(--muted)">×</text>`);
    } else if (fret === 0) {
      parts.push(`<circle cx="${x}" cy="${py - 12}" r="4.5" fill="none"
        stroke="var(--muted)" stroke-width="1.6"/>`);
    } else if (fret !== shape.barre) {
      const y = py + (h / rows) * (fret - shape.base + 0.5);
      parts.push(`<circle cx="${x}" cy="${y}" r="7" fill="var(--accent)"/>`);
    }
  });

  if (shape.base > 1) {
    parts.push(`<text x="${px + w + 8}" y="${py + h / rows * 0.8}"
      font-size="12" fill="var(--muted)">${shape.base}</text>`);
  }

  return `<svg viewBox="0 0 ${w + 30} ${h + 36}" width="${w + 30}" height="${h + 36}"
    role="img" aria-label="${name}">${parts.join('')}</svg>`;
}

function buildGrips() {
  const table = data.shapes || {};
  const used = [...new Set(data.chords.map((c) => c.name))]
    .filter((name) => (table[name] || []).length);
  if (!used.length) return;

  $('grips').innerHTML = used.map((name) => `
    <div class="grip" data-chord="${name}">
      <div class="grip-name">${name}</div>
      ${grip(name, table[name][0])}
      ${table[name].length > 1
        ? `<button class="grip-more" data-alt="${name}">ещё вариант</button>` : ''}
    </div>`).join('');
  $('gripCard').style.display = '';

  // Второй и третий вариант показываем по запросу: новичку нужен один,
  // а кто ищет удобнее -- нажмёт.
  const shown = {};
  $('grips').querySelectorAll('[data-alt]').forEach((button) => {
    button.onclick = () => {
      const name = button.dataset.alt;
      shown[name] = ((shown[name] || 0) + 1) % table[name].length;
      const box = button.parentElement;
      box.querySelector('svg').outerHTML = grip(name, table[name][shown[name]]);
      button.textContent = shown[name] ? 'ещё вариант' : 'первый вариант';
    };
  });

  gripEls = [...$('grips').querySelectorAll('.grip')].map((el) => ({
    el, name: el.dataset.chord,
  }));
}

// ------------------------------------------------------- перемотка лентой

// Лента -- это и есть шкала времени песни, и тянуть её мышкой
// естественнее, чем целиться в тонкий ползунок под плеером. Особенно
// когда разбираешь место, которое не выходит: отмотал на полтакта назад,
// послушал, ещё раз.
function bindDrag() {
  const stage = document.querySelector('.stage');
  let dragging = false;
  let startX = 0;
  let startTime = 0;
  let moved = 0;
  let wasPlaying = false;

  const pointX = (event) =>
    event.touches ? event.touches[0].clientX : event.clientX;

  const begin = (event) => {
    if (event.target.closest('.controls')) return;   // кнопки живут своей жизнью
    dragging = true;
    moved = 0;
    startX = pointX(event);
    startTime = clock.time;
    wasPlaying = !clock.paused;
    if (wasPlaying) clock.pause();
    stage.classList.add('dragging');
  };

  const move = (event) => {
    if (!dragging) return;
    const delta = pointX(event) - startX;
    moved = Math.max(moved, Math.abs(delta));
    const limit = clock.duration || Infinity;
    clock.time = Math.max(0, Math.min(limit, startTime - delta / PX_PER_SEC));
    if (event.cancelable) event.preventDefault();
  };

  const end = () => {
    if (!dragging) return;
    dragging = false;
    stage.classList.remove('dragging');
    // Короткий тык -- это не перетаскивание, а «продолжай играть»
    if (wasPlaying || moved < 4) clock.play();
  };

  stage.addEventListener('mousedown', begin);
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', end);
  stage.addEventListener('touchstart', begin, { passive: true });
  stage.addEventListener('touchmove', move, { passive: false });
  stage.addEventListener('touchend', end);

  // Колесо и горизонтальная прокрутка тачпада -- то же самое
  stage.addEventListener('wheel', (event) => {
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ? event.deltaX : event.deltaY;
    const limit = clock.duration || Infinity;
    clock.time = Math.max(0, Math.min(limit, clock.time + delta / PX_PER_SEC));
    event.preventDefault();
  }, { passive: false });
}

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

  // Подсвечиваем аппликатуру того аккорда, что звучит сейчас
  const playing = (ribs.find(({ chord }) => now >= chord.start && now < chord.end) || {}).chord;
  gripEls.forEach(({ el, name }) =>
    el.classList.toggle('now', !!playing && playing.name === name));

  if (clock && !clock.paused) metro.sync(now, clock.rate);
  $('time').textContent = `${mmss(now)} / ${mmss(clock.duration)}`;
  if (clock.duration) $('seek').value = (now / clock.duration) * 1000;
  requestAnimationFrame(tick);
}

load();
