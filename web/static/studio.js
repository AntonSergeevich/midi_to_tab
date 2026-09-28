// Студия в духе Suno: слева -- что сделать, справа -- мои треки; клик по
// треку открывает его страницу: версии, партии, текст. Музыка играет в
// общем плеере внизу -- обновление списка её не обрывает.

const $ = (id) => document.getElementById(id);
let info = null;
let mode = 'create';
let voice = '';
let file = null;
let again = null;
let useReference = false;  // «Как в образце»: стиль берём из загруженной песни           // «Повторить»: исходник берём из этой прошлой работы
let polling = null;
let openJob = null;         // id трека, чья страница открыта
let playing = null;         // url, который сейчас в плеере

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rub = (n) => `${Math.round(n)} ₽`;
const plural = (n, one, few, many) => {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  return m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many;
};
const time = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '');

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></svg>',
  tabs: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 10h16M4 14h16M4 18h16M9 4v16M15 4v16"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 5 8 12l7 7"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M6 5h2v14H6zM20 5v14L9 12z"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M16 5h2v14h-2zM4 5v14l11-7z"/></svg>',
  expand: '<svg viewBox="0 0 24 24"><path d="M15 4h5v5M9 20H4v-5M20 4l-6 6M4 20l6-6"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  tempo: '<svg viewBox="0 0 24 24"><path d="M9 3h6l3 18H6L9 3zM12 14l5-6M8 17h8"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
  again: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15m0 5v-5h5"/></svg>',
};

// Музыкальный сленг: ожидание и неудачи -- по-человечески, с юмором.
const WAIT = [
  'Настраиваем гитары…', 'Барабанщик считает «раз-два-три-четыре»…', 'Вокалист распевается…',
  'Басист ищет тонику…', 'Звукорежиссёр крутит ручки…', 'Сводим, мастерим, не дышим…',
  'Ловим грув…', 'Подтягиваем струны…', 'Прогоняем припев ещё разок…',
];
const OOPS = [
  'Ой, эпик фейл! Это не струна порвалась — грув уже на выезде и чинит.',
  'Фальшивая нота вышла. Бывает и у рок-звёзд.',
  'Сет прервался — техник уже бежит с запасным кабелем.',
];
const pick = (list, seed) => list[Math.abs(seed) % list.length];
const seedOf = (id) => [...id].reduce((a, c) => a + c.charCodeAt(0), 0);

const STYLE_IDEAS = [
  'мощный ню-метал, рваный дроп-рифф, скретчи, рэп-куплет и мелодичный припев',
  'тёплая акустика, пальцевый перебор, лёгкая перкуссия, душевный вокал',
  'синтвейв 80-х, аналоговые синтезаторы, драм-машина, ночная трасса',
  'поп-панк, быстрые барабаны, дерзкие гитары, заряжающий припев',
  'лоу-фай хип-хоп, виниловый треск, мягкие клавиши, расслабленный бит',
  'эпичный оркестр, струнные, хор, большие барабаны, кинематографично',
  'фанк 70-х, слэп-бас, вау-гитара, духовые, танцевальный грув',
  'русский рок, живые гитары, хриплый вокал, гимн для стадиона',
  'джаз-трио, контрабас, щёточки, рояль, дымный клуб',
  'дип-хаус, 122 BPM, тёплый бас, воздушный женский вокал',
];

const MODE = {
  create: { title: 'Песня с нуля', hint: 'Опишите стиль и вставьте или сочините текст — нейросеть напишет песню. Две версии на выбор.', button: 'Создать' },
  restyle: { title: 'Переделка', hint: 'Загрузите свою песню и выберите стиль — мелодия и текст сохранятся, инструменты и жанр сменятся.', button: 'Переделать' },
  stems: { title: 'Партии', hint: 'Разложим любой трек на вокал, гитару, бас, барабаны, клавиши и остальное.', button: 'Разделить' },
  enrich: { title: 'Дописать', hint: 'Допишем к вашей записи барабаны, бас или другую партию.', button: 'Дописать' },
};
const MODE_ICON = { create: '✦', restyle: '↻', stems: '≡', enrich: '+' };

// Обложка трека: свой градиент для каждого id -- чтобы список не был серым.
function cover(j, big) {
  let h = 0;
  for (const ch of j.id) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const art = j.cover ? `<img src="${j.cover}" alt="" loading="lazy">` : '';
  return `<div class="st-cover${big ? ' big' : ''}" style="background:linear-gradient(135deg,
    hsl(${h} 70% 45%), hsl(${(h + 60) % 360} 70% 30%))">${art}</div>`;
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
  const reference = mode === 'create' && useReference;
  $('drop').hidden = mode === 'create' && !reference;
  $('refToggle').hidden = !(mode === 'create' && mureka);
  $('refToggle').classList.toggle('on', reference);
  $('refToggle').textContent = reference ? '✓ Как в образце — стиль возьмём из загруженной песни (отменить)'
    : '＋ Как в образце: взять стиль из песни';
  $('prompt').disabled = reference;
  $('prompt').placeholder = reference ? 'Стиль возьмём из образца — описание Mureka с ним не сочетает'
    : 'Жанр, настроение, инструменты, темп. Например: мощный ню-метал, рваный рифф, скретчи, агрессивный рэп-куплет и мелодичный припев';
  $('titleBox').hidden = mode !== 'create';
  $('styleBox').hidden = mode === 'stems';
  $('trackBox').hidden = mode !== 'enrich';
  const keep = mode === 'restyle' && mureka && $('keepVocals').checked;
  $('keepBox').hidden = !(mode === 'restyle' && mureka);
  $('proBox').hidden = !(mode === 'stems' && mureka);
  $('voiceBox').hidden = !['create', 'restyle'].includes(mode) || !mureka || keep;
  $('lyricsBox').hidden = !['create', 'restyle'].includes(mode) || keep;
  $('instrumentalBox').hidden = mode !== 'create';
  $('strengthBox').hidden = !(mode === 'restyle' && !mureka);
  saveDraft();
  $('lyricsNote').textContent = mode === 'restyle' && mureka ? '— обязателен' : '';
  updateStart();
}

function updateStart() {
  if (!info) return;
  const service = mode === 'stems' && $('stemsPro').checked ? info.services.stems_pro : info.services[mode];
  const packCost = (mode === 'restyle' && $('keepVocals').checked ? service.packKeep : service.pack) || 0;
  const byPack = packCost && info.studioCredits >= packCost;
  const enough = info.unlimited || byPack || info.balance >= service.price;
  const lyrics = $('lyrics').value.trim();
  const mureka = info.restyleEngine === 'mureka';
  let problem = '';
  if (!info.ready) problem = info.why;
  else if (mode === 'create' && !info.createOpen) problem = 'Песни с нуля скоро появятся.';
  else if (mode === 'restyle' && !info.restyleOpen) problem = 'Переделка скоро вернётся.';
  else if (mode !== 'create' && !file && !again) problem = 'Загрузите трек.';
  else if (mode === 'create' && useReference && !file) problem = 'Загрузите песню-образец (возьмём ~30 секунд).';
  else if (mode === 'create' && !lyrics && !$('instrumental').checked) problem = 'Добавьте текст или отметьте «инструментал».';
  else if (mode !== 'create' && mode !== 'stems' && !$('prompt').value.trim()) problem = 'Опишите стиль.';
  // Не хватает денег или нет аккаунта -- кнопка не гаснет, а ведёт к оплате:
  // черновик сохранён, после регистрации и оплаты человек вернётся сюда же.
  needPay = !problem && !enough;
  $('start').disabled = Boolean(problem);
  const price = info.unlimited ? '' : byPack
    ? ` · ${packCost} ${plural(packCost, 'кредит', 'кредита', 'кредитов')}` : ` · ${rub(service.price)}`;
  $('start').textContent = needPay ? `Оплатить и ${MODE[mode].button.toLowerCase()}${price}`
    : `${MODE[mode].button}${price}`;
  $('msg').innerHTML = problem || (needPay
    ? (info.registered ? 'Пополните баланс — всё заполненное дождётся вас здесь.'
      : 'Заведите аккаунт и пополните баланс — всё заполненное дождётся вас здесь.')
    : ['create', 'restyle'].includes(mode) ? 'Две версии на выбор, обычно 1–3 минуты.' : 'Обычно пара минут.');
}

let needPay = false;
let rights = '';            // отметка «чья музыка» для текущего файла (оферта 6.3)
let rightsFor = null;

// ------------------------------------------------------ чья это музыка
// Надёжно узнать песню по звуку бесплатно нельзя (AcoustID -- только для
// некоммерческих, ACRCloud/AudD -- платные), поэтому догадываемся по имени
// файла «Исполнитель - Песня» и тегу ID3 TPE1, а решает человек -- галочкой.
async function guessArtist(f) {
  if (!f) return '';
  try {
    const head = new Uint8Array(await f.slice(0, 65536).arrayBuffer());
    const text = new TextDecoder('latin1').decode(head);
    const at = text.indexOf('TPE1');
    if (at >= 0 && text.startsWith('ID3')) {
      const size = (head[at + 4] << 24) | (head[at + 5] << 16) | (head[at + 6] << 8) | head[at + 7];
      const body = head.slice(at + 11, at + 10 + size);
      const enc = head[at + 10];
      const decoded = new TextDecoder(enc === 1 ? 'utf-16' : enc === 2 ? 'utf-16be' : enc === 3 ? 'utf-8' : 'windows-1251')
        .decode(body).replace(/\u0000/g, '').trim();
      if (decoded) return decoded;
    }
  } catch (error) { /* не mp3 или битый тег -- смотрим на имя */ }
  const m = f.name.replace(/\.[^.]+$/, '').match(/^(.{2,60}?)\s[-–—]\s/);
  return m ? m[1].trim() : '';
}

function askRights() {
  return new Promise(async (resolve) => {
    const artist = await guessArtist(file);
    const modal = $('rightsModal');
    const name = file ? file.name : $('dropTitle').textContent;
    $('rightsGuess').textContent = artist
      ? `Похоже, это песня исполнителя «${artist}» — ${name}.`
      : `Файл: ${name}.`;
    document.querySelectorAll('[name="rights"]').forEach((r) => { r.checked = r.value === (artist ? 'cover' : ''); });
    $('rightsAgree').checked = false;
    const sync = () => {
      const kind = (document.querySelector('[name="rights"]:checked') || {}).value || '';
      $('rightsText').innerHTML = kind === 'own'
        ? 'Подтверждаю, что права на запись у меня (<a href="/offer#rights" target="_blank">п. 6 оферты</a>).'
        : 'Понимаю: переделки чужих песен — только для личного использования. Публикация и коммерческое использование — под мою ответственность (<a href="/offer#rights" target="_blank">п. 6 оферты</a>).';
      $('rightsOk').disabled = !kind || !$('rightsAgree').checked;
    };
    modal.onchange = sync;
    sync();
    modal.hidden = false;
    $('rightsOk').onclick = () => {
      modal.hidden = true;
      resolve(document.querySelector('[name="rights"]:checked').value);
    };
    $('rightsCancel').onclick = () => { modal.hidden = true; resolve(''); };
  });
}

// ------------------------------------------------------------ черновик
// Всё, что человек заполнил, живёт в localStorage: перезагрузка, вход,
// регистрация и оплата его не стирают. Файл браузер хранить не даёт --
// его придётся выбрать снова (кроме «Повторить», там исходник на сервере).
const DRAFT = 'naslux.studio.draft';
function saveDraft() {
  try {
    localStorage.setItem(DRAFT, JSON.stringify({
      mode, voice, again, title: $('title').value, prompt: $('prompt').value,
      lyrics: $('lyrics').value, aiPrompt: $('aiPrompt').value,
      instrumental: $('instrumental').checked, keep: $('keepVocals').checked,
      againName: again ? $('dropTitle').textContent : '',
    }));
  } catch (error) { /* приватный режим -- просто без черновика */ }
}
function restoreDraft() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem(DRAFT) || 'null'); } catch (error) { d = null; }
  if (!d) return false;
  mode = d.mode || mode;
  voice = d.voice || '';
  ['title', 'prompt', 'lyrics', 'aiPrompt'].forEach((k) => { if (d[k]) $(k).value = d[k]; });
  $('instrumental').checked = Boolean(d.instrumental);
  $('lyrics').disabled = $('instrumental').checked;
  $('keepVocals').checked = Boolean(d.keep);
  if (d.again) useSource(d.again, d.againName);
  return Boolean(d.prompt || d.lyrics || d.title);
}
function useSource(jobId, name) {
  again = jobId;
  file = null;
  $('dropTitle').textContent = name || 'Исходник из прошлой работы';
  $('dropHint').textContent = 'берём из прошлой работы · нажмите, чтобы выбрать другой файл';
  $('drop').classList.add('has');
}

function toast(html, ms = 6000) {
  $('toast').innerHTML = html;
  $('toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $('toast').hidden = true; }, ms);
}

function pickFile(chosen) {
  if (!chosen) return;
  file = chosen;
  again = null;
  $('dropTitle').textContent = chosen.name;
  $('dropHint').textContent = `${(chosen.size / 1048576).toFixed(1)} МБ · нажмите, чтобы заменить`;
  $('drop').classList.add('has');
  updateStart();
}

function lyricsCount() {
  const lines = $('lyrics').value.split('\n').filter((l) => l.trim()).length;
  $('lyricsCount').textContent = `строк: ${lines} · ${$('lyrics').value.length} из 5000`;
}

// На весь экран окно текста выносится прямо в <body>: внутри «липкой»
// панели оно оставалось в её слое, и список треков рисовался поверх.
let lyricsHome = null;
function lyricsFull(open) {
  const wrap = $('lyricsWrap');
  if (open && !lyricsHome) {
    lyricsHome = [wrap.parentNode, wrap.nextSibling];
    document.body.appendChild(wrap);
  } else if (!open && lyricsHome) {
    lyricsHome[0].insertBefore(wrap, lyricsHome[1]);
    lyricsHome = null;
  }
  wrap.classList.toggle('full', open);
  document.body.style.overflow = open ? 'hidden' : '';
  if (open) $('lyrics').focus();
}

// ------------------------------------------------------------ правая часть

function statusLine(j) {
  if (j.status === 'error') {
    return `<span class="bad">${pick(OOPS, seedOf(j.id))}</span><span class="muted">${esc(j.error)}</span>`;
  }
  if (j.expired) return '<span class="muted">файлы удалены по сроку хранения</span>';
  if (j.status !== 'done') {
    const tick = Math.floor(Date.now() / 7000) + seedOf(j.id);
    return `<span class="muted">${esc(j.stage || 'В очереди')}</span>
      <span class="st-fun">${pick(WAIT, tick)}</span>
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

function renderVoices() {
  const learning = info.jobs.filter((j) => j.mode === 'voice' && ['queued', 'running'].includes(j.status));
  $('voices').innerHTML = Object.entries(info.voices || {}).map(([key, title]) =>
    `<button type="button" class="preset${key === voice ? ' selected' : ''}" data-voice="${key}">${esc(title)}</button>`).join('')
    + (info.myVoices || []).map((v) =>
      `<button type="button" class="preset mine${`my:${v.id}` === voice ? ' selected' : ''}" data-voice="my:${v.id}">🎙 ${esc(v.name)}</button>`).join('')
    + learning.map((j) => `<button type="button" class="preset mine" disabled>🎙 ${esc(j.name)} · учим…</button>`).join('')
    + (info.voiceCloneOpen ? '<button type="button" class="preset add" data-voice="add">＋ Мой голос</button>' : '');
}

function renderList(jobs) {
  const top = jobs.filter((j) => !j.from && j.mode !== 'voice');
  $('count').textContent = top.length ? `${top.length}` : '';
  if (!top.length) {
    $('jobs').innerHTML = '<p class="muted">Здесь появятся ваши песни.</p>';
    return;
  }
  $('jobs').innerHTML = top.map((j) => {
    const first = j.status === 'done' && j.files[0];
    return `<div class="st-row" data-open="${j.id}">
      ${cover(j)}<span class="st-mode">${MODE_ICON[j.mode] || ''}</span>
      <div class="st-row-main"><b>${esc(j.name)}</b>${statusLine(j)}</div>
      ${first ? playButton(first, j.name) : ''}
    </div>`;
  }).join('');
}

function renderDetail(jobs) {
  const j = jobs.find((x) => x.id === openJob);
  if (!j) { closeDetail(); return; }
  const children = jobs.filter((x) => x.from === j.id);
  const meta = [MODE[j.mode] ? MODE[j.mode].title : j.title, j.keepVocals ? 'с вашим голосом' : '',
    j.bpm ? `${j.bpm} BPM` : '', j.key || ''].filter(Boolean).join(' · ');
  const versions = j.status === 'done' ? j.files.map((f) => `
    <div class="st-version">
      ${playButton(f, j.name)}
      <div class="st-row-main"><b>${esc(f.label)}</b>
        <span class="muted">${time(f.seconds)}</span></div>
      <button type="button" class="icon-btn" data-menu="${j.id}" data-file="${esc(f.name)}"
        data-url="${f.url}" data-split-ok="${j.mode !== 'stems' ? 1 : ''}" data-extend="${f.mid ? 1 : ''}"
        title="Что сделать">${ICON.more}</button>
    </div>`).join('') : `<div class="st-version"><div class="st-row-main">${statusLine(j)}</div></div>`;
  const parts = children.map((c) => `
    <div class="st-sub"><div class="st-label">${esc(c.title)}</div>
      ${c.status === 'done' ? c.files.map((f) => `
        <div class="st-version small">${playButton(f, `${j.name}: ${f.label}`)}
          <div class="st-row-main"><b>${esc(f.label)}</b></div>
          <button type="button" class="icon-btn" data-menu="${c.id}" data-file="${esc(f.name)}"
            data-url="${f.url}" title="Что сделать">${ICON.more}</button>
        </div>`).join('') : `<div class="st-version"><div class="st-row-main">${statusLine(c)}</div></div>`}
    </div>`).join('');
  const midi = (j.midi || []).length ? `<div class="st-sub"><div class="st-label">MIDI партий</div>
    ${j.midi.map((m) => `<div class="st-version small">
      <div class="st-row-main"><b>${esc(m.label)}</b></div>
      <button type="button" class="icon-btn" data-act="tabs" data-job="${j.id}" data-file="${esc(m.name)}"
        title="Табы и аккорды из этого MIDI">${ICON.tabs}</button>
      <a class="icon-btn" href="${m.url}" download title="Скачать MIDI">${ICON.download}</a>
    </div>`).join('')}
    <div class="st-archives">${(j.archives || []).map((a) => `<a href="${a.url}" download>${ICON.download}
      ${a.name === 'midi.zip' ? 'Все MIDI одним архивом' : 'Все партии в WAV одним архивом'}</a>`).join('')}</div>
  </div>` : '';
  const lyrics = j.lyrics ? `<details class="st-lyrics"><summary>Текст песни</summary>
    <pre>${esc(j.lyrics)}</pre></details>` : '';
  $('detailView').innerHTML = `
    <div class="st-dhead">
      <button type="button" class="icon-btn" id="back" title="Назад">${ICON.back}</button>
      ${cover(j, true)}
      <div class="st-row-main"><h2>${esc(j.name)}</h2><span class="muted">${esc(meta)}</span></div>
      ${['done', 'error'].includes(j.status)
    ? `<button type="button" class="icon-btn" data-menu="${j.id}" data-track="1" title="Что сделать">${ICON.more}</button>` : ''}
    </div>
    <div class="st-label">${j.mode === 'stems' ? 'Партии' : 'Версии'}</div>
    ${versions}${midi}${parts}${lyrics}`;
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
    + (info.studioCredits > 0 ? ` · ${info.studioCredits} ${plural(info.studioCredits, 'кредит', 'кредита', 'кредитов')}` : '');
  $('balance').className = info.unlimited || info.balance > 0 ? 'badge pro' : 'badge';
  if (!$('voices').children.length) {
    const restored = restoreDraft();
    renderVoices();
    $('track').innerHTML = Object.entries(info.tracks).map(([key, title]) =>
      `<option value="${key}">${esc(title)}</option>`).join('');
    lyricsCount();
    showMode();
    afterPayment(restored);
  }
  renderVoices();
  renderList(info.jobs);
  if (openJob) renderDetail(info.jobs);
  updateStart();
  clearTimeout(polling);
  if (info.jobs.some((j) => j.status === 'queued' || j.status === 'running')) {
    polling = setTimeout(load, 5000);
  } else if (info.jobs.some((j) => j.status === 'done' && !j.cover
      && ['create', 'restyle'].includes(j.mode) && Date.now() / 1000 - j.at < 900)) {
    polling = setTimeout(load, 15000);  // обложка ещё рисуется
  }
}

// ------------------------------------------------------------------ плеер

// Очередь -- версии (или партии) того трека, чью кнопку нажали: «дальше»
// и «назад» листают их, как треки альбома.
let queue = [];
let qi = -1;

function coverStyle(j) {
  if (j && j.cover) return `url("${j.cover}")`;
  let h = 0;
  for (const ch of (j ? j.id : 'x')) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${h} 70% 45%), hsl(${(h + 60) % 360} 70% 30%))`;
}

function queueFor(url) {
  for (const j of (info ? info.jobs : [])) {
    const index = (j.files || []).findIndex((f) => f.url === url);
    if (index < 0) continue;
    const parent = j.from ? info.jobs.find((x) => x.id === j.from) : null;
    const owner = parent || j;
    return {
      index,
      items: j.files.map((f) => ({
        url: f.url, title: owner.name, sub: f.label, art: coverStyle(owner), lyrics: owner.lyrics || '',
      })),
    };
  }
  return null;
}

function play(url, title, sub) {
  const audio = $('audio');
  if (playing === url) {
    if (audio.paused) audio.play(); else audio.pause();
    return;
  }
  const found = queueFor(url);
  queue = found ? found.items : [{ url, title, sub, art: coverStyle(null), lyrics: '' }];
  qi = found ? found.index : 0;
  startTrack();
}

function startTrack() {
  const item = queue[qi];
  if (!item) return;
  const audio = $('audio');
  playing = item.url;
  audio.src = item.url;
  if (Number($('fVolume').value) !== 100 || gainNode) setVolume(Number($('fVolume').value));
  audio.play();
  $('playerTitle').textContent = item.title;
  $('playerSub').textContent = item.sub;
  $('fTitle').textContent = item.title;
  $('fSub').textContent = item.sub;
  ['pCover', 'fCover'].forEach((id) => { $(id).style.backgroundImage = item.art; });
  $('fBg').style.backgroundImage = item.art;
  $('fDownload').href = item.url;
  $('fLyrics').textContent = item.lyrics;
  $('fLyricsBtn').hidden = !item.lyrics;
  const many = queue.length > 1;
  ['pPrev', 'pNext', 'fPrev', 'fNext'].forEach((id) => { $(id).disabled = !many; });
  $('player').hidden = false;
  syncButtons();
}

function step(delta) {
  if (queue.length < 2) return;
  qi = (qi + delta + queue.length) % queue.length;
  startTrack();
}

function fullPlayer(open) {
  $('full').hidden = !open;
  document.body.style.overflow = open ? 'hidden' : '';
}

function syncButtons() {
  const audio = $('audio');
  document.querySelectorAll('[data-play]').forEach((b) => {
    const on = b.dataset.play === playing && !audio.paused;
    b.classList.toggle('on', on);
    b.innerHTML = on ? ICON.pause : ICON.play;
  });
  const icon = audio.paused ? ICON.play : ICON.pause;
  $('playerToggle').innerHTML = icon;
  $('fToggle').innerHTML = icon;
}

function syncTime() {
  const audio = $('audio');
  const dur = audio.duration || 0;
  const cur = audio.currentTime || 0;
  $('pBar').style.width = dur ? `${(cur / dur) * 100}%` : '0';
  $('pTime').textContent = `${time(cur) || '0:00'} / ${time(dur) || '0:00'}`;
  $('fCur').textContent = time(cur) || '0:00';
  $('fDur').textContent = time(dur) || '0:00';
  if (!$('fSeek').matches(':active')) $('fSeek').value = dur ? Math.round((cur / dur) * 1000) : 0;
}

// ---------------------------------------------------------------- события

$('modes').addEventListener('click', (e) => {
  const b = e.target.closest('[data-mode]');
  if (!b) return;
  mode = b.dataset.mode;
  showMode();
});
$('refToggle').addEventListener('click', () => {
  useReference = !useReference;
  if (!useReference && mode === 'create') { file = null; $('drop').classList.remove('has'); }
  showMode();
});
$('styleIdea').addEventListener('click', () => {
  const current = $('prompt').value;
  let idea = current;
  while (idea === current) idea = STYLE_IDEAS[Math.floor(Math.random() * STYLE_IDEAS.length)];
  $('prompt').value = idea;
  saveDraft();
  updateStart();
});
['title', 'prompt', 'aiPrompt'].forEach((id) => $(id).addEventListener('input', () => { saveDraft(); updateStart(); }));
$('voices').addEventListener('click', (e) => {
  const b = e.target.closest('[data-voice]');
  if (!b) return;
  if (b.dataset.voice === 'add') { askVoice(); return; }
  voice = b.dataset.voice;
  saveDraft();
  document.querySelectorAll('#voices .preset').forEach((x) => x.classList.toggle('selected', x === b));
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
$('lyrics').addEventListener('input', () => { lyricsCount(); saveDraft(); updateStart(); });
$('keepVocals').addEventListener('change', showMode);
$('stemsPro').addEventListener('change', showMode);
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
  saveDraft();
  updateStart();
});

$('start').addEventListener('click', async () => {
  saveDraft();
  if (needPay) {
    const price = info.services[mode].price;
    const pay = `/pricing?next=/studio&need=${Math.ceil(price - (info.balance || 0))}`;
    location.href = info.registered ? pay : `/account?next=${encodeURIComponent(pay)}`;
    return;
  }
  const withFile = mode !== 'create' || useReference;
  if (withFile) {
    const key = file ? `${file.name}:${file.size}` : again;
    if (rightsFor !== key) {
      rights = await askRights();
      if (!rights) return;
      rightsFor = key;
    }
  }
  const form = new FormData();
  form.append('rights', withFile ? rights : '');
  if (file && withFile) form.append('file', file);
  form.append('reference', mode === 'create' && useReference);
  if (!file && again && mode !== 'create') form.append('again', again);
  form.append('mode', mode);
  form.append('preset', '');
  form.append('prompt', $('prompt').value);
  form.append('title', $('title').value);
  form.append('lyrics', $('instrumental').checked && mode === 'create' ? '' : $('lyrics').value);
  form.append('audio_influence', $('audioKnob').value / 100);
  form.append('style_influence', $('styleKnob').value / 100);
  form.append('weirdness', $('weirdKnob').value / 100);
  form.append('melody', $('melodyKnob').value / 100);
  form.append('track', $('track').value);
  form.append('voice', voice);
  form.append('keep_vocals', mode === 'restyle' && $('keepVocals').checked);
  form.append('pro', mode === 'stems' && $('stemsPro').checked);
  $('start').disabled = true;
  $('msg').textContent = mode === 'create' ? 'Отправляем…' : 'Загружаем трек…';
  const response = await fetch('/api/studio', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    $('msg').innerHTML = `<span class="bad">${pick(OOPS, Date.now())}</span> ${data.detail || 'Не получилось запустить.'}`;
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
  const menuBtn = e.target.closest('[data-menu]');
  if (menuBtn) { openMenu(menuBtn); return; }
  const action = e.target.closest('[data-act]');
  if (action) { await act(action.dataset.act, action.dataset.job, action.dataset.file); return; }
  if (!e.target.closest('#menu')) $('menu').hidden = true;
});

function openMenu(button) {
  const menu = $('menu');
  const job = button.dataset.menu;
  const f = button.dataset.file || '';
  const item = (what, icon, text, extra = '') =>
    `<button type="button" data-act="${what}" data-job="${job}" data-file="${esc(f)}" ${extra}>${icon}<span>${text}</span></button>`;
  menu.innerHTML = button.dataset.track
    ? item('again', ICON.again, 'Повторить с этими настройками') + '<hr>'
      + item('delete', ICON.trash, 'Удалить трек', 'class="danger"')
    : `<a href="${button.dataset.url}" download>${ICON.download}<span>Скачать mp3</span></a>`
      + item('tabs', ICON.tabs, 'Табы, аккорды и MIDI')
      + (button.dataset.splitOk ? item('split', ICON.split, 'Разделить на партии') : '')
      + (button.dataset.splitOk && info.restyleEngine === 'mureka'
        ? item('splitpro', ICON.split, 'Глубокое разделение + MIDI') : '')
      + item('shift', ICON.tempo, 'Темп и тональность')
      + (button.dataset.extend ? item('extend', ICON.again, 'Продлить песню') : '')
      + '<hr>' + item('again', ICON.again, 'Повторить с этими настройками');
  const box = button.getBoundingClientRect();
  menu.hidden = false;
  const left = Math.min(window.innerWidth - menu.offsetWidth - 12, box.right - menu.offsetWidth);
  menu.style.left = `${Math.max(12, left) + window.scrollX}px`;
  menu.style.top = `${box.bottom + window.scrollY + 6}px`;
}

// «Повторить»: настройки трека -- обратно в левую панель, можно поправить
// и сгенерировать заново. Для переделки исходник берётся с сервера.
function repeat(jobId) {
  const j = info.jobs.find((x) => x.id === jobId);
  if (!j) return;
  mode = j.mode in MODE ? j.mode : 'create';
  $('title').value = mode === 'create' ? j.name : '';
  $('prompt').value = j.style || '';
  $('lyrics').value = j.lyrics || '';
  $('instrumental').checked = mode === 'create' && !j.lyrics;
  $('lyrics').disabled = $('instrumental').checked;
  $('keepVocals').checked = Boolean(j.keepVocals);
  voice = j.voice || '';
  document.querySelectorAll('#voices .preset').forEach((x) => x.classList.toggle('selected', x.dataset.voice === voice));
  if (mode !== 'create' && j.hasSource) useSource(j.id, j.name);
  lyricsCount();
  showMode();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  toast(`Настройки в панели слева — поправьте что хотите и жмите «${MODE[mode].button}» 🎛`);
}

async function act(what, jobId, fileName) {
  $('menu').hidden = true;
  if (what === 'again') { repeat(jobId); return; }
  if (what === 'delete') {
    if (!confirm('Удалить трек вместе с файлами?')) return;
    await fetch(`/api/studio/${jobId}`, { method: 'DELETE' });
    closeDetail();
    load();
    return;
  }
  const form = new FormData();
  form.append('file', fileName || '');
  if (what === 'extend') {
    const lyrics = await askExtend(jobId);
    if (!lyrics) return;
    form.append('lyrics', lyrics);
    const response = await fetch(`/api/studio/${jobId}/extend`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) toast(`${pick(OOPS, Date.now())} ${data.detail || ''}`);
    else toast('Дописываем продолжение — появится под треком 🎶');
    load();
    return;
  }
  if (what === 'shift') {
    const choice = await askShift();
    if (!choice) return;
    form.append('semitones', choice.semitones);
    form.append('tempo', choice.tempo);
    const response = await fetch(`/api/studio/${jobId}/shift`, { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      toast(`${pick(OOPS, Date.now())} ${error.detail || ''}`);
    } else {
      toast('Подкручиваем колки и метроном — новая версия появится под треком 🎚');
    }
    load();
    return;
  }
  if (what === 'split' || what === 'splitpro') {
    const pro = what === 'splitpro';
    const service = pro ? info.services.stems_pro : info.services.stems;
    const price = info.unlimited ? '' : ` за ${service.pack} кредитов или ${rub(service.price)}`;
    if (!confirm(pro ? `До 12 партий и MIDI каждой${price}?` : `Разделить эту версию на партии${price}?`)) return;
    form.append('pro', pro);
    const response = await fetch(`/api/studio/${jobId}/stems`, { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      toast(`${pick(OOPS, Date.now())} ${error.detail || ''}`);
    }
    load();
    return;
  }
  if (what === 'tabs') {
    if (!confirm('Разобрать в табы, аккорды и MIDI? Это обычный разбор NASLUX — по вашему тарифу.')) return;
    const response = await fetch(`/api/studio/${jobId}/tabs`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (response.ok) location.href = `/player/${data.jobId}`;
    else toast(`${pick(OOPS, Date.now())} ${data.detail || ''}`);
  }
}

function askExtend(jobId) {
  return new Promise((resolve) => {
    const j = info.jobs.find((x) => x.id === jobId) || {};
    const modal = $('extendModal');
    $('extendLyrics').value = '';
    $('extendOk').textContent = `Продлить · ${info.unlimited ? '' : `${info.services.extend.pack} кредитов или ${rub(info.services.extend.price)}`}`;
    modal.hidden = false;
    $('extendAi').onclick = async () => {
      $('extendAi').disabled = true;
      const form = new FormData();
      form.append('lyrics', j.lyrics || $('extendLyrics').value || '[Verse]');
      const response = await fetch('/api/studio/lyrics/extend', { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      $('extendAi').disabled = false;
      if (response.ok) $('extendLyrics').value = data.lyrics || '';
      else toast(data.detail || 'Не получилось сочинить');
    };
    $('extendOk').onclick = () => {
      const text = $('extendLyrics').value.trim();
      if (!text) { $('extendLyrics').focus(); return; }
      modal.hidden = true;
      resolve(text);
    };
    $('extendCancel').onclick = () => { modal.hidden = true; resolve(''); };
  });
}

function askVoice() {
  const modal = $('voiceModal');
  $('voicePrice').textContent = info.unlimited ? '' : `Стоит ${info.services.voice.pack} кредитов или ${rub(info.services.voice.price)}. Голос хранится у вас, использовать можно сколько угодно.`;
  const sync = () => {
    $('voiceOk').disabled = !$('voiceFile').files[0] || !$('voiceConsent').checked;
  };
  modal.oninput = sync;
  modal.onchange = sync;
  sync();
  modal.hidden = false;
  $('voiceCancel').onclick = () => { modal.hidden = true; };
  $('voiceOk').onclick = async () => {
    const form = new FormData();
    form.append('file', $('voiceFile').files[0]);
    form.append('name', $('voiceName').value.trim() || 'Мой голос');
    form.append('consent', $('voiceConsent').checked);
    $('voiceOk').disabled = true;
    const response = await fetch('/api/studio/voice', { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    modal.hidden = true;
    if (!response.ok) toast(`${pick(OOPS, Date.now())} ${data.detail || ''}`);
    else toast('Слушаем ваш голос — через пару минут он появится в списке голосов 🎙');
    load();
  };
}

function askShift() {
  return new Promise((resolve) => {
    const modal = $('shiftModal');
    const sync = () => {
      const tone = Number($('shiftTone').value);
      $('shiftToneVal').textContent = tone > 0 ? `+${tone}` : String(tone);
      $('shiftTempoVal').textContent = `${$('shiftTempo').value}%`;
      $('shiftOk').disabled = tone === 0 && Number($('shiftTempo').value) === 100;
    };
    $('shiftTone').value = 0;
    $('shiftTempo').value = 100;
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

// Вернулись с оплаты: деньги могут дойти через пару секунд -- ждём их и
// напоминаем, что черновик на месте.
function afterPayment(restored) {
  const paid = new URLSearchParams(location.search).get('paid');
  if (!paid) return;
  history.replaceState(null, '', location.pathname);
  if (paid === '0') { toast('Оплата не прошла — черновик на месте, можно попробовать ещё раз.'); return; }
  toast(`Оплата прошла 🎸 ${restored ? `Всё заполненное на месте — жмите «${MODE[mode].button}».` : ''}`, 9000);
  let tries = 0;
  const wait = async () => {
    const before = info.balance;
    await load();
    if (info.balance === before && ++tries < 6) setTimeout(wait, 3000);
  };
  setTimeout(wait, 2000);
}

['playerToggle', 'fToggle'].forEach((id) => $(id).addEventListener('click', () => {
  const audio = $('audio');
  if (audio.paused) audio.play(); else audio.pause();
}));
['play', 'pause'].forEach((ev) => $('audio').addEventListener(ev, syncButtons));
$('audio').addEventListener('ended', () => { if (qi < queue.length - 1) step(1); else syncButtons(); });
['timeupdate', 'loadedmetadata'].forEach((ev) => $('audio').addEventListener(ev, syncTime));
$('fSeek').addEventListener('input', () => {
  const audio = $('audio');
  if (audio.duration) audio.currentTime = ($('fSeek').value / 1000) * audio.duration;
});
$('pPrev').addEventListener('click', () => step(-1));
$('pNext').addEventListener('click', () => step(1));
$('fPrev').addEventListener('click', () => step(-1));
$('fNext').addEventListener('click', () => step(1));
['pOpen', 'pExpand'].forEach((id) => $(id).addEventListener('click', () => fullPlayer(true)));
$('fClose').addEventListener('click', () => fullPlayer(false));
$('fLyricsBtn').addEventListener('click', () => { $('fLyrics').hidden = !$('fLyrics').hidden; });

// Громкость через Web Audio: на iPhone громкость <audio> из скрипта не меняется.
let gainNode = null;
function setVolume(percent) {
  const level = Math.max(0, Math.min(1, percent / 100));
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!gainNode && Context) {
    try {
      const context = new Context();
      const source = context.createMediaElementSource($('audio'));
      gainNode = context.createGain();
      source.connect(gainNode).connect(context.destination);
      $('audio').addEventListener('play', () => { if (context.state === 'suspended') context.resume(); });
      if (context.state === 'suspended') context.resume();
    } catch (error) { gainNode = null; }
  }
  if (gainNode) gainNode.gain.value = level; else $('audio').volume = level;
  try { localStorage.setItem('naslux.volume', percent); } catch (error) { /* инкогнито */ }
}
$('fVolume').addEventListener('input', () => setVolume(Number($('fVolume').value)));
try { $('fVolume').value = parseInt(localStorage.getItem('naslux.volume'), 10) || 100; } catch (error) { /* */ }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('full').hidden) fullPlayer(false); });
$('pPrev').innerHTML = ICON.prev; $('fPrev').innerHTML = ICON.prev;
$('pNext').innerHTML = ICON.next; $('fNext').innerHTML = ICON.next;
$('pExpand').innerHTML = ICON.expand; $('fClose').innerHTML = ICON.down;
$('fDownload').innerHTML = ICON.download;

knobText();
lyricsCount();
load();
