// Студия в духе Suno: слева -- что сделать, справа -- мои треки; клик по
// треку открывает его страницу: версии, партии, текст. Музыка играет в
// общем плеере внизу -- обновление списка её не обрывает.

const $ = (id) => document.getElementById(id);
let info = null;
let mode = 'create';
let preset = 'numetal';
let file = null;
let polling = null;
let openJob = null;         // id трека, чья страница открыта
let playing = null;         // url, который сейчас в плеере

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rub = (n) => `${Math.round(n)} ₽`;
const time = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '');

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></svg>',
  tabs: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 10h16M4 14h16M4 18h16M9 4v16M15 4v16"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 5 8 12l7 7"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12"/></svg>',
};

const MODE = {
  create: { title: 'Песня с нуля', hint: 'Опишите стиль и вставьте или сочините текст — нейросеть напишет песню. Две версии на выбор.', button: 'Создать' },
  restyle: { title: 'Переделка', hint: 'Загрузите свою песню и выберите стиль — мелодия и текст сохранятся, инструменты и жанр сменятся.', button: 'Переделать' },
  stems: { title: 'Партии', hint: 'Разложим любой трек на вокал, гитару, бас, барабаны, клавиши и остальное.', button: 'Разделить' },
  enrich: { title: 'Дописать', hint: 'Допишем к вашей записи барабаны, бас или другую партию.', button: 'Дописать' },
};
const MODE_ICON = { create: '✦', restyle: '↻', stems: '≡', enrich: '+' };

// Обложка трека: свой градиент для каждого id -- чтобы список не был серым.
function cover(id, big) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `<div class="st-cover${big ? ' big' : ''}" style="background:linear-gradient(135deg,
    hsl(${h} 70% 45%), hsl(${(h + 60) % 360} 70% 30%))"></div>`;
}

// ------------------------------------------------------------ левая панель

function knobText() {
  [['audioKnob', 'audioVal'], ['styleKnob', 'styleVal'], ['weirdKnob', 'weirdVal'],
    ['melodyKnob', 'melodyVal']].forEach(([i, o]) => { $(o).textContent = `${$(i).value}%`; });
}

function showMode() {
  document.querySelectorAll('#modes button').forEach((b) =>
    b.classList.toggle('on', b.dataset.mode === mode));
  $('modeHint').textContent = MODE[mode].hint;
  const mureka = info && info.restyleEngine === 'mureka';
  $('drop').hidden = mode === 'create';
  $('titleBox').hidden = mode !== 'create';
  $('styleBox').hidden = mode === 'stems';
  $('trackBox').hidden = mode !== 'enrich';
  $('lyricsBox').hidden = !['create', 'restyle'].includes(mode);
  $('instrumentalBox').hidden = mode !== 'create';
  $('strengthBox').hidden = !(mode === 'restyle' && !mureka);
  $('lyricsNote').textContent = mode === 'restyle' && mureka ? '— обязателен' : '';
  updateStart();
}

function updateStart() {
  if (!info) return;
  const service = info.services[mode];
  const packCost = service.pack || 0;
  const byPack = packCost && info.studioCredits >= packCost;
  const enough = info.unlimited || byPack || info.balance >= service.price;
  const lyrics = $('lyrics').value.trim();
  const mureka = info.restyleEngine === 'mureka';
  let problem = '';
  if (!info.ready) problem = info.why;
  else if (mode === 'create' && !info.createOpen) problem = 'Песни с нуля скоро появятся.';
  else if (mode === 'restyle' && !info.restyleOpen) problem = 'Переделка скоро вернётся.';
  else if (mode !== 'create' && !file) problem = 'Загрузите трек.';
  else if (mode === 'restyle' && mureka && !lyrics) problem = 'Вставьте или сочините текст песни.';
  else if (mode === 'create' && !lyrics && !$('instrumental').checked) problem = 'Добавьте текст или отметьте «инструментал».';
  else if (!enough) problem = `На балансе ${rub(info.balance)} — <a href="/pricing">пополните</a> или возьмите пакет.`;
  $('start').disabled = Boolean(problem);
  const price = info.unlimited ? '' : byPack
    ? ` · ${packCost} ${packCost === 1 ? 'генерация' : 'генерации'} из пакета` : ` · ${rub(service.price)}`;
  $('start').textContent = `${MODE[mode].button}${price}`;
  $('msg').innerHTML = problem || (['create', 'restyle'].includes(mode)
    ? 'Две версии на выбор, обычно 1–3 минуты.' : 'Обычно пара минут.');
}

function pickFile(chosen) {
  if (!chosen) return;
  file = chosen;
  $('dropTitle').textContent = chosen.name;
  $('dropHint').textContent = `${(chosen.size / 1048576).toFixed(1)} МБ · нажмите, чтобы заменить`;
  $('drop').classList.add('has');
  updateStart();
}

function lyricsCount() {
  const lines = $('lyrics').value.split('\n').filter((l) => l.trim()).length;
  $('lyricsCount').textContent = `строк: ${lines} · ${$('lyrics').value.length} из 5000`;
}

function lyricsFull(open) {
  $('lyricsWrap').classList.toggle('full', open);
  document.body.style.overflow = open ? 'hidden' : '';
  if (open) $('lyrics').focus();
}

// ------------------------------------------------------------ правая часть

function statusLine(j) {
  if (j.status === 'error') return `<span class="bad">${esc(j.error)}</span>`;
  if (j.expired) return '<span class="muted">файлы удалены по сроку хранения</span>';
  if (j.status !== 'done') {
    return `<span class="muted">${esc(j.stage || 'В очереди')}</span>
      <div class="bar done"><i style="width:${Math.max(4, j.progress)}%"></i></div>`;
  }
  const when = new Date(j.at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
  const length = j.files[0] && j.files[0].seconds ? ` · ${time(j.files[0].seconds)}` : '';
  return `<span class="muted">${esc(MODE[j.mode] ? MODE[j.mode].title : j.title)}${length} · ${when}</span>`;
}

function playButton(f, title) {
  const on = playing === f.url && !$('audio').paused;
  return `<button type="button" class="play-btn${on ? ' on' : ''}" data-play="${f.url}"
    data-title="${esc(title)}" data-sub="${esc(f.label)}" aria-label="Слушать">${on ? ICON.pause : ICON.play}</button>`;
}

function renderList(jobs) {
  const top = jobs.filter((j) => !j.from);
  $('count').textContent = top.length ? `${top.length}` : '';
  if (!top.length) {
    $('jobs').innerHTML = '<p class="muted">Здесь появятся ваши песни.</p>';
    return;
  }
  $('jobs').innerHTML = top.map((j) => {
    const first = j.status === 'done' && j.files[0];
    return `<div class="st-row" data-open="${j.id}">
      ${cover(j.id)}<span class="st-mode">${MODE_ICON[j.mode] || ''}</span>
      <div class="st-row-main"><b>${esc(j.name)}</b>${statusLine(j)}</div>
      ${first ? playButton(first, j.name) : ''}
    </div>`;
  }).join('');
}

function renderDetail(jobs) {
  const j = jobs.find((x) => x.id === openJob);
  if (!j) { closeDetail(); return; }
  const children = jobs.filter((x) => x.from === j.id);
  const meta = [MODE[j.mode] ? MODE[j.mode].title : j.title,
    j.bpm ? `${j.bpm} BPM` : '', j.key || ''].filter(Boolean).join(' · ');
  const versions = j.status === 'done' ? j.files.map((f) => `
    <div class="st-version">
      ${playButton(f, j.name)}
      <div class="st-row-main"><b>${esc(f.label)}</b>
        <span class="muted">${time(f.seconds)}</span></div>
      <a class="icon-btn" href="${f.url}" download title="Скачать">${ICON.download}</a>
      <button type="button" class="icon-btn" data-tabs="${j.id}"
        data-file="${esc(f.name)}" title="Табы, аккорды и MIDI">${ICON.tabs}</button>
      ${j.mode === 'stems' ? '' : `<button type="button" class="icon-btn" data-split="${j.id}"
        data-file="${esc(f.name)}" title="Разделить на партии">${ICON.split}</button>`}
    </div>`).join('') : `<div class="st-version">${statusLine(j)}</div>`;
  const parts = children.map((c) => `
    <div class="st-sub"><div class="st-label">Партии · ${esc((c.title.split('·')[1] || '').trim())}</div>
      ${c.status === 'done' ? c.files.map((f) => `
        <div class="st-version small">${playButton(f, `${j.name}: ${f.label}`)}
          <div class="st-row-main"><b>${esc(f.label)}</b></div>
          <a class="icon-btn" href="${f.url}" download title="Скачать">${ICON.download}</a>
          <button type="button" class="icon-btn" data-tabs="${c.id}"
        data-file="${esc(f.name)}" title="Табы, аккорды и MIDI">${ICON.tabs}</button>
        </div>`).join('') : `<div class="st-version">${statusLine(c)}</div>`}
    </div>`).join('');
  const lyrics = j.lyrics ? `<details class="st-lyrics"><summary>Текст песни</summary>
    <pre>${esc(j.lyrics)}</pre></details>` : '';
  $('detailView').innerHTML = `
    <div class="st-dhead">
      <button type="button" class="icon-btn" id="back" title="Назад">${ICON.back}</button>
      ${cover(j.id, true)}
      <div class="st-row-main"><h2>${esc(j.name)}</h2><span class="muted">${esc(meta)}</span></div>
      ${['done', 'error'].includes(j.status)
    ? `<button type="button" class="icon-btn" data-del="${j.id}" title="Удалить">${ICON.trash}</button>` : ''}
    </div>
    <div class="st-label">${j.mode === 'stems' ? 'Партии' : 'Версии'}</div>
    ${versions}${parts}${lyrics}`;
}

function closeDetail() {
  openJob = null;
  $('detailView').hidden = true;
  $('listView').hidden = false;
}

async function load() {
  info = await (await fetch('/api/studio')).json();
  $('account').textContent = info.registered ? info.email : 'Вход';
  $('balance').textContent = info.unlimited ? 'Безлимит' : `Баланс: ${rub(info.balance)}`
    + (info.studioCredits > 0 ? ` · генераций: ${info.studioCredits}` : '');
  $('balance').className = info.unlimited || info.balance > 0 ? 'badge pro' : 'badge';
  if (!$('presets').children.length) {
    $('presets').innerHTML = Object.entries(info.presets).map(([key, title]) =>
      `<button type="button" class="preset${key === preset ? ' selected' : ''}" data-preset="${key}">${esc(title)}</button>`).join('');
    $('track').innerHTML = Object.entries(info.tracks).map(([key, title]) =>
      `<option value="${key}">${esc(title)}</option>`).join('');
    showMode();
  }
  renderList(info.jobs);
  if (openJob) renderDetail(info.jobs);
  updateStart();
  clearTimeout(polling);
  if (info.jobs.some((j) => j.status === 'queued' || j.status === 'running')) {
    polling = setTimeout(load, 5000);
  }
}

// ------------------------------------------------------------------ плеер

function play(url, title, sub) {
  const audio = $('audio');
  if (playing === url) {
    if (audio.paused) audio.play(); else audio.pause();
    return;
  }
  playing = url;
  audio.src = url;
  audio.play();
  $('playerTitle').textContent = title;
  $('playerSub').textContent = sub;
  $('player').hidden = false;
}

function syncButtons() {
  const audio = $('audio');
  document.querySelectorAll('[data-play]').forEach((b) => {
    const on = b.dataset.play === playing && !audio.paused;
    b.classList.toggle('on', on);
    b.innerHTML = on ? ICON.pause : ICON.play;
  });
  $('playerToggle').innerHTML = audio.paused ? ICON.play : ICON.pause;
}

// ---------------------------------------------------------------- события

$('modes').addEventListener('click', (e) => {
  const b = e.target.closest('[data-mode]');
  if (!b) return;
  mode = b.dataset.mode;
  showMode();
});
$('presets').addEventListener('click', (e) => {
  const b = e.target.closest('[data-preset]');
  if (!b) return;
  preset = b.dataset.preset;
  $('prompt').value = '';
  document.querySelectorAll('#presets .preset').forEach((x) => x.classList.toggle('selected', x === b));
});
['audioKnob', 'styleKnob', 'weirdKnob', 'melodyKnob'].forEach((id) => $(id).addEventListener('input', knobText));
$('drop').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', () => pickFile($('file').files[0]));
$('drop').addEventListener('dragover', (e) => { e.preventDefault(); $('drop').classList.add('over'); });
$('drop').addEventListener('dragleave', () => $('drop').classList.remove('over'));
$('drop').addEventListener('drop', (e) => {
  e.preventDefault();
  $('drop').classList.remove('over');
  pickFile(e.dataTransfer.files[0]);
});
$('lyrics').addEventListener('input', () => { lyricsCount(); updateStart(); });
$('instrumental').addEventListener('change', () => {
  $('lyrics').disabled = $('instrumental').checked;
  updateStart();
});
$('lyricsExpand').addEventListener('click', () => lyricsFull(true));
$('lyricsDone').addEventListener('click', () => lyricsFull(false));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('lyricsWrap').classList.contains('full')) lyricsFull(false);
});
$('lyricsAi').addEventListener('click', () => {
  $('aiBox').hidden = !$('aiBox').hidden;
  if (!$('aiBox').hidden) $('aiPrompt').focus();
});
$('aiGo').addEventListener('click', async () => {
  const prompt = $('aiPrompt').value.trim();
  if (!prompt) return;
  $('aiGo').disabled = true;
  $('aiGo').textContent = 'Сочиняю…';
  const form = new FormData();
  form.append('prompt', prompt);
  const response = await fetch('/api/studio/lyrics', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  $('aiGo').disabled = false;
  $('aiGo').textContent = 'Сочинить';
  if (!response.ok) {
    $('msg').innerHTML = `<span class="bad">${esc(data.detail || 'Не получилось сочинить текст')}</span>`;
    return;
  }
  $('lyrics').value = data.lyrics || '';
  if (data.title && !$('title').value) $('title').value = data.title;
  $('aiBox').hidden = true;
  lyricsCount();
  updateStart();
});

$('start').addEventListener('click', async () => {
  const form = new FormData();
  if (file && mode !== 'create') form.append('file', file);
  form.append('mode', mode);
  form.append('preset', preset);
  form.append('prompt', $('prompt').value);
  form.append('title', $('title').value);
  form.append('lyrics', $('instrumental').checked && mode === 'create' ? '' : $('lyrics').value);
  form.append('audio_influence', $('audioKnob').value / 100);
  form.append('style_influence', $('styleKnob').value / 100);
  form.append('weirdness', $('weirdKnob').value / 100);
  form.append('melody', $('melodyKnob').value / 100);
  form.append('track', $('track').value);
  $('start').disabled = true;
  $('msg').textContent = mode === 'create' ? 'Отправляем…' : 'Загружаем трек…';
  const response = await fetch('/api/studio', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    $('msg').innerHTML = `<span class="bad">${data.detail || 'Не получилось запустить.'}</span>`;
    $('start').disabled = false;
    return;
  }
  openJob = data.jobId;
  $('listView').hidden = true;
  $('detailView').hidden = false;
  await load();
});

document.addEventListener('click', async (e) => {
  const playBtn = e.target.closest('[data-play]');
  if (playBtn) {
    e.stopPropagation();
    play(playBtn.dataset.play, playBtn.dataset.title, playBtn.dataset.sub);
    return;
  }
  const row = e.target.closest('[data-open]');
  if (row) {
    openJob = row.dataset.open;
    renderDetail(info.jobs);
    $('listView').hidden = true;
    $('detailView').hidden = false;
    window.scrollTo({ top: $('detailView').offsetTop - 80, behavior: 'smooth' });
    return;
  }
  if (e.target.closest('#back')) { closeDetail(); return; }
  const split = e.target.closest('[data-split]');
  if (split) {
    const price = info.unlimited ? '' : ` за ${rub(info.services.stems.price)}`;
    if (!confirm(`Разделить эту версию на партии${price}?`)) return;
    const form = new FormData();
    form.append('file', split.dataset.file || '');
    const response = await fetch(`/api/studio/${split.dataset.split}/stems`, { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      alert((error.detail || 'Не получилось запустить').replace(/<[^>]+>/g, ''));
    }
    load();
    return;
  }
  const tabs = e.target.closest('[data-tabs]');
  if (tabs) {
    if (!confirm('Разобрать в табы, аккорды и MIDI? Это обычный разбор NASLUX — по вашему тарифу.')) return;
    const form = new FormData();
    form.append('file', tabs.dataset.file);
    const response = await fetch(`/api/studio/${tabs.dataset.tabs}/tabs`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (response.ok) location.href = `/player/${data.jobId}`;
    else alert((data.detail || 'Не получилось запустить').replace(/<[^>]+>/g, ''));
    return;
  }
  const del = e.target.closest('[data-del]');
  if (del && confirm('Удалить трек вместе с файлами?')) {
    await fetch(`/api/studio/${del.dataset.del}`, { method: 'DELETE' });
    closeDetail();
    load();
  }
});

$('playerToggle').addEventListener('click', () => {
  const audio = $('audio');
  if (audio.paused) audio.play(); else audio.pause();
});
['play', 'pause', 'ended'].forEach((ev) => $('audio').addEventListener(ev, syncButtons));

knobText();
lyricsCount();
load();
