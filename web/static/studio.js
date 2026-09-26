// Студия: переделка трека в другой стиль, дописывание партии и разделение
// на партии нейросетями на видеокарте (RunPod). Деньги списываются с
// баланса при запуске и возвращаются, если задача не удалась.

const $ = (id) => document.getElementById(id);
let info = null;
let mode = 'restyle';
let preset = 'numetal';
let file = null;
let polling = null;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rub = (n) => `${Math.round(n)} ₽`;

// Три крутилки, как в Suno: значения 0..1 уходят воркеру как есть, а
// что они значат для нейросети -- решает он (worker/handler.py, restyle).
const knobs = { audioKnob: 'audioVal', styleKnob: 'styleVal', weirdKnob: 'weirdVal' };

function knobText() {
  Object.entries(knobs).forEach(([input, out]) => { $(out).textContent = `${$(input).value}%`; });
}

function showMode() {
  document.querySelectorAll('#modes .choice').forEach((b) =>
    b.classList.toggle('selected', b.dataset.mode === mode));
  $('styleBox').style.display = mode === 'stems' ? 'none' : '';
  $('strengthBox').style.display = mode === 'restyle' ? '' : 'none';
  $('lyricsBox').style.display = mode === 'restyle' ? '' : 'none';
  $('trackBox').style.display = mode === 'enrich' ? '' : 'none';
  updateStart();
}

function updateStart() {
  if (!info) return;
  const price = info.services[mode].price;
  const byPack = ['restyle', 'enrich'].includes(mode) && info.studioCredits > 0;
  const enough = info.unlimited || byPack || info.balance >= price;
  $('start').disabled = !info.ready || !file || !enough;
  $('start').textContent = info.unlimited ? 'Запустить'
    : byPack ? `Запустить · из пакета, осталось ${info.studioCredits}`
      : `Запустить за ${rub(price)}`;
  if (!info.ready) {
    $('msg').textContent = info.why;
  } else if (!file) {
    $('msg').textContent = 'Выберите трек.';
  } else if (!enough) {
    $('msg').innerHTML = `На балансе ${rub(info.balance)} — <a href="/pricing">пополните</a> `
      + 'или возьмите пакет генераций, чтобы запустить.';
  } else {
    $('msg').textContent = mode === 'stems' ? 'Обычно 1–3 минуты.'
      : 'Сделаем две версии — выберите лучшую. Обычно 2–5 минут, страницу можно закрыть.';
  }
}

function pickFile(chosen) {
  if (!chosen) return;
  file = chosen;
  $('dropTitle').textContent = chosen.name;
  $('dropHint').textContent = `${(chosen.size / 1048576).toFixed(1)} МБ · нажмите, чтобы выбрать другой`;
  updateStart();
}

function jobHtml(j) {
  const when = new Date(j.at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
  let body = '';
  if (j.expired) {
    body = '<div class="muted">Файлы удалены по сроку хранения — 14 дней.</div>';
  } else if (j.status === 'done') {
    const meta = j.bpm ? `<div class="muted">Исходник: ${j.bpm} BPM · ${esc(j.key || '')}</div>` : '';
    body = meta + j.files.map((f) => `
      <div class="studio-file">
        <div class="muted">${esc(f.label)}</div>
        <audio controls preload="none" src="${f.url}"></audio>
        <a href="${f.url}" download>Скачать</a>
        ${j.mode === 'stems' ? '' : `<button type="button" class="split" data-split="${j.id}"
          data-file="${esc(f.name)}">Разделить на партии</button>`}
      </div>`).join('');
  } else if (j.status === 'error') {
    body = `<div class="bad">${esc(j.error)}</div>`;
  } else {
    body = `<div class="muted" data-stage>${esc(j.stage || 'В очереди')}</div>
      <div class="bar done"><i data-progress style="width:${Math.max(4, j.progress)}%"></i></div>`;
  }
  const del = j.status === 'done' || j.status === 'error'
    ? `<button class="del" data-del="${j.id}" title="Удалить">✕</button>` : '';
  return `<div style="display:flex;gap:10px;align-items:baseline">
      <b style="flex:1;min-width:0;overflow-wrap:anywhere">${esc(j.title)} · ${esc(j.name)}</b>
      <span class="muted">${when}</span>${del}
    </div>${body}`;
}

// Список обновляется раз в несколько секунд, пока что-то считается. Раньше
// он каждый раз пересобирался целиком -- вместе с плеерами, и песня,
// которую человек слушал, обрывалась на каждом шаге полоски прогресса.
// Теперь карточка пересобирается, только когда у неё сменился статус или
// набор файлов, а ход задачи двигает одну полоску.
function renderJobs(jobs) {
  const box = $('jobs');
  if (!jobs.length) {
    box.className = 'muted';
    box.textContent = 'Пока пусто.';
    return;
  }
  if (box.classList.contains('muted')) {
    box.className = '';
    box.textContent = '';
  }
  const seen = new Set();
  let previous = null;
  jobs.forEach((j) => {
    seen.add(j.id);
    const key = [j.status, j.expired, j.error, j.files.map((f) => f.name).join()].join('|');
    let card = box.querySelector(`[data-job="${j.id}"]`);
    if (!card || card.dataset.key !== key) {
      const fresh = document.createElement('div');
      fresh.className = 'studio-job';
      fresh.dataset.job = j.id;
      fresh.dataset.key = key;
      fresh.innerHTML = jobHtml(j);
      if (card) card.replaceWith(fresh); else box.insertBefore(fresh, previous ? previous.nextSibling : box.firstChild);
      card = fresh;
    } else if (j.status === 'queued' || j.status === 'running') {
      const stage = card.querySelector('[data-stage]');
      const bar = card.querySelector('[data-progress]');
      if (stage) stage.textContent = j.stage || 'В очереди';
      if (bar) bar.style.width = `${Math.max(4, j.progress)}%`;
    }
    previous = card;
  });
  box.querySelectorAll('[data-job]').forEach((card) => {
    if (!seen.has(card.dataset.job)) card.remove();
  });
}

async function load() {
  info = await (await fetch('/api/studio')).json();
  $('account').textContent = info.registered ? info.email : 'Вход';
  $('balance').textContent = info.unlimited ? 'Безлимит' : `Баланс: ${rub(info.balance)}`
    + (info.studioCredits > 0 ? ` · генераций: ${info.studioCredits}` : '');
  $('balance').className = info.unlimited || info.balance > 0 ? 'badge pro' : 'badge';
  if (!info.ready) {
    $('notReady').style.display = '';
    $('notReady').textContent = info.why;
  }
  document.querySelectorAll('[data-price]').forEach((el) => {
    const s = info.services[el.dataset.price];
    el.textContent = info.unlimited ? el.dataset.desc : `${rub(s.price)} · ${el.dataset.desc}`;
  });
  if (!$('presets').children.length) {
    $('presets').innerHTML = Object.entries(info.presets).map(([key, title]) =>
      `<button type="button" class="preset${key === preset ? ' selected' : ''}" data-preset="${key}">${esc(title)}</button>`).join('');
    $('track').innerHTML = Object.entries(info.tracks).map(([key, title]) =>
      `<option value="${key}">${esc(title)}</option>`).join('');
  }
  renderJobs(info.jobs);
  updateStart();
  const busy = info.jobs.some((j) => j.status === 'queued' || j.status === 'running');
  clearTimeout(polling);
  if (busy) polling = setTimeout(load, 5000);
}

$('modes').addEventListener('click', (e) => {
  const button = e.target.closest('[data-mode]');
  if (!button) return;
  mode = button.dataset.mode;
  showMode();
});

$('presets').addEventListener('click', (e) => {
  const button = e.target.closest('[data-preset]');
  if (!button) return;
  preset = button.dataset.preset;
  $('prompt').value = '';
  document.querySelectorAll('#presets .preset').forEach((b) =>
    b.classList.toggle('selected', b === button));
});

Object.keys(knobs).forEach((id) => $(id).addEventListener('input', knobText));

const drop = $('drop');
drop.addEventListener('click', () => $('file').click());
$('file').addEventListener('change', () => pickFile($('file').files[0]));
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  pickFile(e.dataTransfer.files[0]);
});

$('start').addEventListener('click', async () => {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('mode', mode);
  form.append('preset', preset);
  form.append('prompt', $('prompt').value);
  form.append('lyrics', $('lyrics').value);
  form.append('audio_influence', $('audioKnob').value / 100);
  form.append('style_influence', $('styleKnob').value / 100);
  form.append('weirdness', $('weirdKnob').value / 100);
  form.append('track', $('track').value);
  form.append('language', $('language').value);
  $('start').disabled = true;
  $('msg').textContent = 'Загружаем трек…';
  const response = await fetch('/api/studio', { method: 'POST', body: form });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    $('msg').innerHTML = error.detail || 'Не получилось запустить.';
    $('start').disabled = false;
    return;
  }
  $('msg').textContent = 'Запущено — результат появится ниже.';
  await load();
  $('jobs').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// Текст песни во весь экран: в узком поле длинный текст не отредактировать.
function lyricsFull(open) {
  $('lyricsWrap').classList.toggle('full', open);
  document.body.style.overflow = open ? 'hidden' : '';
  if (open) $('lyrics').focus();
}
function lyricsCount() {
  const lines = $('lyrics').value.split('\n').filter((l) => l.trim()).length;
  $('lyricsCount').textContent = `строк: ${lines} · символов: ${$('lyrics').value.length} из 5000`;
}
$('lyricsExpand').addEventListener('click', () => lyricsFull(true));
$('lyricsDone').addEventListener('click', () => lyricsFull(false));
$('lyrics').addEventListener('input', lyricsCount);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('lyricsWrap').classList.contains('full')) lyricsFull(false);
});

$('jobs').addEventListener('click', async (e) => {
  const split = e.target.closest('[data-split]');
  if (split) {
    const price = info.unlimited ? '' : ` за ${rub(info.services.stems.price)}`;
    if (!confirm(`Разделить эту версию на партии${price}?`)) return;
    split.disabled = true;
    const form = new FormData();
    form.append('file', split.dataset.file || '');
    const response = await fetch(`/api/studio/${split.dataset.split}/stems`,
      { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      alert((error.detail || 'Не получилось запустить').replace(/<[^>]+>/g, ''));
      split.disabled = false;
      return;
    }
    load();
    return;
  }
  const button = e.target.closest('[data-del]');
  if (!button || !confirm('Удалить эту работу вместе с файлами?')) return;
  await fetch(`/api/studio/${button.dataset.del}`, { method: 'DELETE' });
  load();
});

knobText();
showMode();
load();
lyricsCount();
