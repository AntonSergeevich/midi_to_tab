// Студия в духе Suno: слева -- что сделать, справа -- мои треки; клик по
// треку открывает его страницу: версии, партии, текст. Музыка играет в
// общем плеере внизу -- обновление списка её не обрывает.

const $ = (id) => document.getElementById(id);
let info = null;
let mode = 'create';
let voice = '';
let file = null;
let again = null;
let againFile = '';        // какая версия прошлой работы -- исходник (кавер на свой трек)
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
  mix: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-4 4m4-4 4 4M5 20h14"/></svg>',
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
const MODE_ICON = { create: '✦', restyle: '↻', stems: '≡', enrich: '+', upload: '⤒' };
let fresh = null;
let seeking = false;      // тянут ползунок нижнего плеера -- время не перерисовываем          // только что запущенный трек -- подсветить в списке

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
  // Проба YuE2 -- у владельца (безлимит), в «С нуля» и «Переделать». Пока
  // Mureka без денег, YuE2 -- движок для всех (yue2Public), без галочки
  const pub = Boolean(info && info.yue2Public) && ['create', 'restyle'].includes(mode);
  const yue2Here = Boolean(info && info.yue2Open) && ['create', 'restyle'].includes(mode);
  $('yue2Box').hidden = !yue2Here || pub;
  const yue2 = pub || (yue2Here && $('useYue2').checked);
  $('yue2Knobs').hidden = !yue2;
  $('yCloseBox').hidden = mode !== 'restyle';
  const mureka = info && info.restyleEngine === 'mureka' && !yue2;
  if (yue2) useReference = false;
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
  $('voiceBox').hidden = !['create', 'restyle'].includes(mode) || keep;
  $('lyricsBox').hidden = !['create', 'restyle'].includes(mode) || keep;
  $('instrumentalBox').hidden = mode !== 'create' || yue2;      // YuE2 поёт только по словам
  $('strengthBox').hidden = !(mode === 'restyle' && !mureka && !yue2);
  saveDraft();
  // «Инструментал» есть только у песни с нуля: в «Переделать» поле слов не гасим
  $('lyrics').disabled = mode === 'create' && $('instrumental').checked && !yue2;
  $('lyricsNote').textContent = mode === 'restyle' && yue2 ? '— необязательно: распознаем сами'
    : mode === 'restyle' && mureka ? '— необязательно' : '';
  updateStart();
}

// «Дописать → Все партии»: на деле это переделка с вашим голосом (Mureka
// пишет новую аранжировку под записанный вокал) -- так и отправляем, и цена её
function yue2On() {
  if (!['create', 'restyle'].includes(mode)) return false;
  return Boolean(info && info.yue2Public) || (Boolean(info && info.yue2Open) && $('useYue2').checked);
}

function sendMode() {
  if (mode === 'enrich' && $('track').value === 'all') return { mode: 'restyle', keep: true, all: true };
  return { mode, keep: mode === 'restyle' && $('keepVocals').checked && !yue2On(), all: false };
}

function updateStart() {
  if (!info) return;
  const send = sendMode();
  const service = mode === 'stems' && $('stemsPro').checked ? info.services.stems_pro : info.services[send.mode];
  const packCost = (send.keep ? service.packKeep : service.pack) || 0;
  const byPack = packCost && info.studioCredits >= packCost;
  const enough = info.unlimited || byPack || info.balance >= service.price;
  const lyrics = $('lyrics').value.trim();
  const mureka = info.restyleEngine === 'mureka';
  let problem = '';
  if (!info.ready) problem = info.why;
  else if (mode === 'create' && !info.createOpen && !yue2On()) problem = 'Песни с нуля скоро появятся.';
  else if (send.mode === 'restyle' && !info.restyleOpen) problem = 'Переделка скоро вернётся.';
  else if (mode !== 'create' && !file && !again) problem = 'Загрузите трек.';
  else if (mode === 'create' && useReference && !file) problem = 'Загрузите песню-образец (возьмём ~30 секунд).';
  else if (mode === 'create' && !lyrics && yue2On()) problem = 'Добавьте текст песни.';
  else if (mode === 'create' && !lyrics && !$('instrumental').checked) problem = 'Добавьте текст или отметьте «инструментал».';
  else if (mode !== 'create' && mode !== 'stems' && !$('prompt').value.trim()) problem = 'Опишите стиль.';
  // Не хватает денег или нет аккаунта -- кнопка не гаснет, а ведёт к оплате:
  // черновик сохранён, после регистрации и оплаты человек вернётся сюда же.
  needPay = !problem && !enough;
  $('start').disabled = Boolean(problem);
  const price = info.unlimited ? '' : byPack
    ? ` · ${packCost} ${plural(packCost, 'кредит', 'кредита', 'кредитов')}` : ` · ${rub(service.price)}`;
  const button = send.all ? 'Переписать все партии' : MODE[mode].button;
  $('start').textContent = needPay ? `Оплатить и ${button.toLowerCase()}${price}` : `${button}${price}`;
  $('trackHint').hidden = !send.all;
  $('msg').innerHTML = problem || (needPay
    ? (info.registered ? 'Пополните баланс — всё заполненное дождётся вас здесь.'
      : 'Заведите аккаунт и пополните баланс — всё заполненное дождётся вас здесь.')
    : ['create', 'restyle'].includes(send.mode) ? 'Две версии на выбор, обычно 1–3 минуты.' : 'Обычно пара минут.');
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

function askRights(chosen = file) {
  return new Promise(async (resolve) => {
    const artist = await guessArtist(chosen);
    const modal = $('rightsModal');
    const name = chosen ? chosen.name : $('dropTitle').textContent;
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
      againName: again ? $('dropTitle').textContent : '', againFile,
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
  if (d.again) useSource(d.again, d.againName, d.againFile);
  return Boolean(d.prompt || d.lyrics || d.title);
}
function useSource(jobId, name, version = '', hint = '') {
  again = jobId;
  againFile = version || '';
  file = null;
  $('dropTitle').textContent = name || 'Исходник из прошлой работы';
  $('dropHint').textContent = hint || 'берём из прошлой работы · нажмите, чтобы выбрать другой файл';
  $('drop').classList.add('has');
  $('dropClear').hidden = false;
}

// ✕ на выбранном треке: убрать его из формы (в «Моих треках» он остаётся)
function clearSource() {
  file = null;
  again = null;
  againFile = '';
  $('file').value = '';
  $('dropTitle').textContent = 'Загрузите трек';
  $('dropHint').textContent = 'mp3, wav, flac, ogg, m4a · до 6 минут · сразу появится в «Моих треках»';
  $('drop').classList.remove('has');
  $('dropClear').hidden = true;
  saveDraft();
  updateStart();
}

function toast(html, ms = 6000) {
  $('toast').innerHTML = html;
  $('toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $('toast').hidden = true; }, ms);
}

// Загрузка трека -- одна, здесь (10.10: была ещё кнопка у списка, путались).
// Выбранный файл сразу ложится в «Мои треки» (бесплатно: тональность, темп,
// мультитрек), а запуск берёт его оттуда -- второй раз файл не грузим.
async function pickFile(chosen) {
  if (!chosen) return;
  const kind = await askRights(chosen);
  if (!kind) return;
  $('dropTitle').textContent = chosen.name;
  $('dropHint').textContent = 'Загружаем в «Мои треки»…';
  $('drop').classList.add('has');
  const form = new FormData();
  form.append('file', chosen);
  form.append('rights', kind);
  const response = await fetch('/api/studio/upload', { method: 'POST', body: form }).catch(() => null);
  const data = response ? await response.json().catch(() => ({})) : {};
  rights = kind;
  if (response && response.ok) {
    useSource(data.jobId, chosen.name, '',
      `${(chosen.size / 1048576).toFixed(1)} МБ · уже в «Моих треках» · нажмите, чтобы заменить`);
    rightsFor = data.jobId;
    fresh = data.jobId;
    list.page = 0; list.query = ''; $('search').value = '';
    saveDraft();
    load();
  } else {
    // Не легло в «Мои треки» (лимит, связь) -- работаем с файлом как раньше
    file = chosen;
    again = null;
    againFile = '';
    rightsFor = `${chosen.name}:${chosen.size}`;
    $('dropHint').textContent = `${(chosen.size / 1048576).toFixed(1)} МБ · нажмите, чтобы заменить`;
    $('dropClear').hidden = false;
    if (response) toast(`${data.detail || 'В «Мои треки» не добавили'} — трек загрузится при запуске`);
  }
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
  const key = j.key ? ` · <b class="st-key">${esc(j.key)}${j.bpm ? ` · ${j.bpm} BPM` : ''}</b>` : '';
  return `<span class="muted">${esc(MODE[j.mode] ? MODE[j.mode].title : j.title)}${key}${length} · ${when}</span>`;
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
    // Свои голоса и «Мой голос» -- только у Mureka; на запасном движке их нет
    + (info.restyleEngine === 'mureka' ? info.myVoices || [] : []).map((v) =>
      `<button type="button" class="preset mine${`my:${v.id}` === voice ? ' selected' : ''}" data-voice="my:${v.id}">🎙 ${esc(v.name)}</button>`).join('')
    + learning.map((j) => `<button type="button" class="preset mine" disabled>🎙 ${esc(j.name)} · учим…</button>`).join('')
    + (info.voiceCloneOpen && info.restyleEngine === 'mureka' ? '<button type="button" class="preset add" data-voice="add">＋ Мой голос</button>' : '');
}

// Поиск и страницы: список перерисовывается при каждом опросе сервера,
// поэтому запрос и номер страницы живут отдельно и не сбрасываются
const PER_PAGE = 10;
const list = { query: '', page: 0, jobs: [] };
// Страница списка, поиск и прокрутка -- в памяти вкладки: зашёл в трек,
// ушёл в плеер или табы, вернулся «назад» -- список там же, где был
// (раньше всегда сбрасывался на первую страницу, 06.10)
const LIST_KEY = 'studioList';
let listScroll = 0;
let listRestored = false;
try {
  const saved = JSON.parse(sessionStorage.getItem(LIST_KEY) || '{}');
  list.page = Number(saved.page) || 0;
  list.query = String(saved.query || '');
  listScroll = Number(saved.scroll) || 0;
  if (list.query) $('search').value = list.query;
} catch (error) { /* нет хранилища -- с первой страницы */ }
function saveList() {
  try {
    sessionStorage.setItem(LIST_KEY, JSON.stringify({
      page: list.page, query: list.query, scroll: openJob ? listScroll : window.scrollY }));
  } catch (error) { /* не страшно */ }
}
window.addEventListener('pagehide', saveList);
const fold = (text) => String(text || '').toLowerCase().replace(/ё/g, 'е');

function matches(j, words) {
  const hay = fold([j.name, j.title, j.style, j.key, j.bpm && `${j.bpm} bpm`,
    MODE[j.mode] && MODE[j.mode].title].filter(Boolean).join(' '));
  return words.every((w) => hay.includes(w));
}

function renderList(jobs) {
  list.jobs = jobs;
  // Разборы (аккорды, табы, MIDI) -- тем же списком «Мои треки»: раньше они
  // жили на отдельной странице, и два раздела с треками путали (10.10).
  // Разбор из работы Студии -- внутри неё, а не отдельной строкой.
  const own = new Set(jobs.map((j) => j.id));
  const tabs = ((info && info.analyses) || []).filter((a) => !own.has(a.studio))
    .map((a) => ({ ...a, mode: 'tabs', files: [] }));
  const all = jobs.filter((j) => !j.from && j.mode !== 'voice').concat(tabs).sort((x, y) => y.at - x.at);
  const words = fold(list.query).split(/\s+/).filter(Boolean);
  const top = words.length ? all.filter((j) => matches(j, words)) : all;
  $('count').textContent = all.length ? (words.length ? `${top.length} из ${all.length}` : `${all.length}`) : '';
  $('searchBox').hidden = all.length <= PER_PAGE && !list.query;
  const pages = Math.max(1, Math.ceil(top.length / PER_PAGE));
  list.page = Math.min(list.page, pages - 1);
  renderPages(pages);
  if (!top.length) {
    $('jobs').innerHTML = all.length
      ? `<p class="muted">Ничего не нашлось по «${esc(list.query)}».</p>`
      : '<p class="muted">Здесь появятся ваши песни.</p>';
    return;
  }
  const shown = top.slice(list.page * PER_PAGE, (list.page + 1) * PER_PAGE);
  $('jobs').innerHTML = shown.map((j) => {
    if (j.mode === 'tabs') return tabsRow(j);
    const first = j.status === 'done' && j.files[0];
    return `<div class="st-row${j.id === fresh ? ' st-new' : ''}" data-open="${j.id}">
      ${cover(j)}<span class="st-mode">${MODE_ICON[j.mode] || ''}</span>
      <div class="st-row-main"><b>${esc(j.name)}</b>${statusLine(j)}</div>
      ${first ? playButton(first, j.name) : ''}
    </div>`;
  }).join('');
}

// ---------------------------------------------------- разборы в «Моих треках»

const TAB_FILES = { gp5: 'Guitar Pro', txt: 'Табы .txt', mid: 'MIDI' };

function tabsLine(a) {
  if (a.status === 'error') return `<span class="bad">${esc(a.stage || 'Не получилось')}</span>`;
  const busy = a.made.filter((m) => ['running', 'queued'].includes(m.status));
  if (a.status !== 'done') {
    return `<span class="muted">${esc(a.stage || 'В очереди')}</span>
      <div class="bar done"><i style="width:${Math.max(4, Math.round(a.progress || 0))}%"></i></div>`;
  }
  const when = new Date(a.at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
  const facts = ['Аккорды и табы', a.tempo ? `${Math.round(a.tempo)} BPM` : '', a.chords ? `аккордов ${a.chords}` : '',
    a.parts.length ? `партий ${a.parts.length}` : '', a.hasLyrics ? 'текст' : '',
    busy.length ? `в работе: ${busy.length}` : ''].filter(Boolean).join(' · ');
  return `<span class="muted">${esc(facts)} · ${when}</span>`;
}

function tabsRow(a, label) {
  return `<div class="st-row${label ? ' st-tabsub' : ''}" data-tabs="${a.id}" title="Открыть аккорды и табы">
    ${label ? '' : cover(a)}<span class="st-mode">♫</span>
    <div class="st-row-main"><b>${esc(label || a.name)}</b>${tabsLine(a)}</div>
    <button type="button" class="icon-btn" data-menu="${a.id}" data-analysis="1" title="Что сделать">${ICON.more}</button>
  </div>`;
}

function tabsMenu(a) {
  const files = a.made.filter((m) => m.status === 'done').flatMap((m) => m.files.map((f) =>
    `<a href="/api/file/${m.id}/${f}" download>${ICON.download}<span>${esc(m.stem ? `${m.stem}: ` : '')}${TAB_FILES[f] || f}</span></a>`));
  return `<a href="/player/${a.id}">${ICON.tabs}<span>Открыть: аккорды, табы, плеер</span></a>${files.join('')}<hr>`
    + `<button type="button" data-act="deltabs" data-job="${a.id}" class="danger">${ICON.trash}<span>Удалить разбор</span></button>`;
}

function renderPages(pages) {
  $('pages').hidden = pages < 2;
  if (pages < 2) { $('pages').innerHTML = ''; return; }
  const at = list.page;
  // 1 … 4 5 6 … 12 -- соседние страницы и края
  const nums = [...new Set([0, at - 1, at, at + 1, pages - 1])].filter((n) => n >= 0 && n < pages).sort((x, y) => x - y);
  let html = `<button type="button" data-page="${at - 1}" ${at === 0 ? 'disabled' : ''} aria-label="Назад">←</button>`;
  nums.forEach((n, k) => {
    if (k && n - nums[k - 1] > 1) html += '<span>…</span>';
    html += `<button type="button" data-page="${n}" ${n === at ? 'aria-current="page"' : ''}>${n + 1}</button>`;
  });
  html += `<button type="button" data-page="${at + 1}" ${at === pages - 1 ? 'disabled' : ''} aria-label="Дальше">→</button>`;
  $('pages').innerHTML = html;
}

$('pages').addEventListener('click', (e) => {
  const button = e.target.closest('[data-page]');
  if (!button || button.disabled) return;
  list.page = Number(button.dataset.page);
  renderList(list.jobs);
  saveList();
  $('listView').scrollIntoView({ block: 'start', behavior: 'smooth' });
});

$('search').addEventListener('input', () => {
  list.query = $('search').value;
  list.page = 0;
  renderList(list.jobs);
  saveList();
});

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
        <span class="muted">${time(f.seconds)}${keyOf(j, f.name)}</span></div>
      <a class="icon-btn st-mixbtn" href="/studio/mix/${j.id}?file=${encodeURIComponent(f.name)}"
        title="Открыть в мультитреке: дорожки, метроном, заглушить лишнее">${ICON.mix}<span>Мультитрек</span></a>
      <button type="button" class="icon-btn" data-menu="${j.id}" data-file="${esc(f.name)}"
        data-url="${f.url}" data-split-ok="${j.mode !== 'stems' ? 1 : ''}" data-extend="${f.mid ? 1 : ''}"
        ${j.mode === 'stems' ? stemData(j, f) : ''}
        title="Что сделать">${ICON.more}</button>
    </div>`).join('') : `<div class="st-version"><div class="st-row-main">${statusLine(j)}</div></div>`;
  const parts = children.map((c) => `
    <div class="st-sub"><div class="st-label">${esc(c.title)}</div>
      ${c.status === 'done' ? c.files.map((f) => stemRow(c, f, `${j.name}: ${f.label}`)).join('')
        + midiList(c) + (c.mode === 'notes' ? sheetBlock(c) + midiRows(c) : '') : `<div class="st-version"><div class="st-row-main">${statusLine(c)}</div></div>`}
    </div>`).join('');
  const midi = (j.midi || []).length ? `<div class="st-sub"><div class="st-label">MIDI партий</div>
    ${midiRows(j)}
    <div class="st-archives">${(j.archives || []).map((a) => `<a href="${a.url}" download>${ICON.download}
      ${a.name === 'midi.zip' ? 'Все MIDI одним архивом' : 'Все партии в WAV одним архивом'}</a>`).join('')}</div>
  </div>` : '';
  const label = (name) => ((j.files || []).concat(j.midi || []).find((f) => f.name === name) || {}).label || name;
  const made = ((info && info.analyses) || []).filter((a) => a.studio === j.id);
  const analyses = made.length ? `<div class="st-sub"><div class="st-label">Аккорды и табы</div>
    ${made.map((a) => tabsRow(a, label(a.studioFile))).join('')}</div>` : '';
  const lyrics = j.lyrics ? `<details class="st-lyrics"><summary>Текст песни</summary>
    <pre>${esc(j.lyrics)}</pre></details>` : '';
  $('detailView').innerHTML = `
    <div class="st-dhead">
      <button type="button" class="icon-btn" id="back" title="Назад">${ICON.back}</button>
      ${cover(j, true)}
      <div class="st-row-main"><h2>${esc(j.name)}</h2><span class="muted">${esc(meta)}</span></div>
      ${['done', 'error'].includes(j.status)
    ? `<button type="button" class="icon-btn" data-menu="${j.id}" data-track="1" data-upload="${j.mode === 'upload' ? 1 : ''}" data-done="${j.status === 'done' && j.files.length && j.mode !== 'stems' ? 1 : ''}" title="Что сделать">${ICON.more}</button>` : ''}
    </div>
    <div class="st-label">${j.mode === 'stems' ? 'Партии' : 'Версии'}</div>
    ${versions}${sheetBlock(j)}${j.mode === 'stems' ? midiList(j) : ''}${midi}${parts}${analyses}${lyrics}`;
}

// MIDI партий работы: табы нашим плеером и скачивание
function midiRows(j) {
  return (j.midi || []).map((m) => `<div class="st-version small">
      <div class="st-row-main"><b>${esc(m.label)}</b></div>
      <button type="button" class="icon-btn" data-act="tabs" data-job="${j.id}" data-file="${esc(m.name)}"
        title="Табы и аккорды из этого MIDI">${ICON.tabs}</button>
      <a class="icon-btn" href="${m.url}" download title="Скачать MIDI">${ICON.download}</a>
    </div>`).join('');
}

// Ноты и аккорды от YuE2/SheetSage2: нотный стан по кнопке, аккорды строкой
function sheetBlock(j) {
  const chords = j.chords ? `<p class="st-chords"><span class="muted">Аккорды:</span> ${esc(j.chords)}</p>` : '';
  const notes = j.hasAbc ? `<button type="button" class="st-linkbtn" data-notes="${j.id}">🎼 Ноты на нотном стане</button>` : '';
  return chords || notes ? `<div class="st-sheet">${notes}${chords}</div>` : '';
}

// Партия: MIDI -- по кнопке в ⋯ (наша расшифровка нот), статус -- прямо в строке
function stemRow(c, f, title) {
  const stem = f.name.replace(/\.[^.]+$/, '');
  const ready = (c.midi || []).find((m) => m.name === `${stem}.mid`);
  const busy = (c.midiBusy || []).includes(f.name);
  return `<div class="st-version small">${playButton(f, title)}
    <div class="st-row-main"><b>${esc(f.label)}</b>${busy ? '<span class="muted">переводим в ноты…</span>' : ''}</div>
    ${ready ? `<a class="icon-btn" href="${ready.url}" download title="Скачать MIDI этой партии">MIDI</a>` : ''}
    <button type="button" class="icon-btn" data-menu="${c.id}" data-file="${esc(f.name)}"
      data-url="${f.url}" data-stem="${c.mode === 'stems' ? 1 : ''}" data-midi="${ready ? ready.url : ''}"
      data-busy="${busy ? 1 : ''}" title="Что сделать">${ICON.more}</button>
  </div>`;
}

function stemData(c, f) {
  const ready = (c.midi || []).find((m) => m.name === `${f.name.replace(/\.[^.]+$/, '')}.mid`);
  return `data-stem="1" data-midi="${ready ? ready.url : ''}" data-busy="${(c.midiBusy || []).includes(f.name) ? 1 : ''}"`;
}

function midiList(c) {
  if (c.mode !== 'stems') return '';
  const hint = c.pro ? '' : `<p class="muted st-midi-hint">Нужен MIDI? В ⋯ у партии — «MIDI этой дорожки», бесплатно.
    Барабаны в MIDI — через «Глубокое разделение + MIDI» в ⋯ у версии трека.</p>`;
  return hint;
}

// Открытый трек -- запись в истории: жест «назад» на телефоне закрывает
// трек и возвращает к списку на ту же страницу и прокрутку, а не уводит с сайта
function openDetail(id, push) {
  if (!openJob) listScroll = window.scrollY;
  openJob = id;
  renderDetail(info.jobs);
  $('listView').hidden = true;
  $('detailView').hidden = false;
  if (push) history.pushState({ detail: id }, '');
  saveList();
  window.scrollTo({ top: $('detailView').offsetTop - 80, behavior: push ? 'smooth' : 'auto' });
}

function closeDetail(fromHistory) {
  const wasOpen = Boolean(openJob);
  openJob = null;
  $('detailView').hidden = true;
  $('listView').hidden = false;
  if (!fromHistory && history.state && history.state.detail) history.back();
  if (wasOpen && fromHistory) window.scrollTo({ top: listScroll, behavior: 'auto' });
  saveList();
}

// Опрос сервера. Раньше один сорвавшийся запрос (на телефоне по LTE --
// обычное дело) обрывал цепочку навсегда, и карточка замирала до
// перезагрузки (06.10). Теперь после ошибки -- повтор, а при возврате на
// вкладку -- сразу свежие данные.
async function load() {
  clearTimeout(polling);
  let next = null;
  try {
    next = await loadOnce();
  } catch (error) {
    next = 8000;
  }
  clearTimeout(polling);
  if (next) polling = setTimeout(load, next);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden && info) load(); });
window.addEventListener('pageshow', (e) => { if (e.persisted && info) load(); });

async function loadOnce() {
  const response = await fetch('/api/studio');
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  info = await response.json();
  // Шапка -- общая (nav.js); обновляем, только когда изменились деньги
  const money = `${info.unlimited}|${info.balance}|${info.studioCredits}|${info.email}`;
  if (money !== load.money && window.refreshNav) { load.money = money; window.refreshNav(); }
  if (!$('voices').children.length) {
    const restored = restoreDraft();
    // Ссылки со страниц возможностей: /?mode=stems, /?style=рок
    const ask = new URLSearchParams(location.search);
    if (ask.get('mode') in MODE) mode = ask.get('mode');
    if (ask.get('style') && !$('prompt').value) $('prompt').value = ask.get('style').slice(0, 300);
    renderVoices();
    $('track').innerHTML = (info.restyleEngine === 'mureka'
      ? '<option value="all">Все партии — голос остаётся ваш</option>' : '')
      + Object.entries(info.tracks).map(([key, title]) => `<option value="${key}">${esc(title)}</option>`).join('');
    lyricsCount();
    showMode();
    afterPayment(restored);
  }
  renderVoices();
  renderList(info.jobs);
  if (openJob) renderDetail(info.jobs);
  if (!listRestored) {                 // вернулись на страницу «назад»
    listRestored = true;
    const back = history.state && history.state.detail;
    if (back && info.jobs.some((j) => j.id === back)) openDetail(back, false);
    else if (listScroll && !location.search) window.scrollTo({ top: listScroll, behavior: 'auto' });
  }
  updateStart();
  const analyzing = await ensureKeys().catch(() => false);
  const working = (s) => s === 'queued' || s === 'running';
  if (info.jobs.some((j) => working(j.status) || (j.midiBusy || []).length)
      || (info.analyses || []).some((a) => working(a.status) || a.made.some((m) => working(m.status)))) {
    return 5000;
  }
  if (analyzing) return 6000;            // тональность и темп вот-вот будут
  if (info.jobs.some((j) => j.status === 'done' && !j.cover
      && ['create', 'restyle'].includes(j.mode) && Date.now() / 1000 - j.at < 900)) {
    return 15000;                        // обложка ещё рисуется
  }
  return null;
}

// Тональность и темп: разбор на сервере по запросу, не больше двух треков
// за раз (открытый трек -- первым). true -- что-то ещё считается.
const keyAsked = {};
async function ensureKeys() {
  const wanted = [];
  const open = info.jobs.find((j) => j.id === openJob);
  const consider = (j, files) => files.forEach((f) => {
    if (!(j.keys || {})[f.name] && (keyAsked[`${j.id}/${f.name}`] || 0) < 20) wanted.push([j, f]);
  });
  if (open && open.status === 'done') consider(open, open.files);
  info.jobs.filter((j) => !j.from && j.status === 'done' && j.files[0] && !j.key && j.mode !== 'voice')
    .forEach((j) => consider(j, [j.files[0]]));
  let pending = false;
  for (const [j, f] of wanted.slice(0, 2)) {
    keyAsked[`${j.id}/${f.name}`] = (keyAsked[`${j.id}/${f.name}`] || 0) + 1;
    const answer = await fetch(`/api/studio/${j.id}/analysis?file=${encodeURIComponent(f.name)}`)
      .then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
    if (answer.pending) pending = true;
    else if (answer.key || answer.error) {
      j.keys = { ...(j.keys || {}), [f.name]: { key: answer.key, bpm: answer.bpm } };
      pending = true;               // перерисуем со свежими данными
    }
  }
  return pending;
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
  if (!seeking) $('pBar').style.width = dur ? `${(cur / dur) * 100}%` : '0';
  $('pSeek').setAttribute('aria-valuenow', dur ? Math.round((cur / dur) * 100) : 0);
  $('pSeek').setAttribute('aria-valuetext', `${time(cur) || '0:00'} из ${time(dur) || '0:00'}`);
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
  if (!useReference && mode === 'create') clearSource();
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
$('drop').addEventListener('click', (e) => {
  if (e.target.closest('#dropClear')) { e.stopPropagation(); clearSource(); return; }
  $('file').click();
});
$('file').addEventListener('change', () => {
  const chosen = $('file').files[0];
  $('file').value = '';            // тот же файл ещё раз -- тоже событие
  pickFile(chosen);
});
$('drop').addEventListener('dragover', (e) => { e.preventDefault(); $('drop').classList.add('over'); });
$('drop').addEventListener('dragleave', () => $('drop').classList.remove('over'));
$('drop').addEventListener('drop', (e) => {
  e.preventDefault();
  $('drop').classList.remove('over');
  pickFile(e.dataTransfer.files[0]);
});
$('lyrics').addEventListener('input', () => { lyricsCount(); saveDraft(); updateStart(); });
$('keepVocals').addEventListener('change', showMode);
$('useYue2').addEventListener('change', showMode);
$('yCfg').addEventListener('input', () => { $('yCfgVal').textContent = Number($('yCfg').value).toFixed(1); });
$('yCreative').addEventListener('input', () => { $('yCreativeVal').textContent = `${$('yCreative').value}%`; });
$('track').addEventListener('change', () => { updateStart(); saveDraft(); });
$('stemsPro').addEventListener('change', showMode);
$('instrumental').addEventListener('change', () => {
  $('lyrics').disabled = $('instrumental').checked;
  updateStart();
});
// Метки частей песни: кнопка -- в место курсора, «Разметить» -- всё сразу
$('lyricsTags').addEventListener('click', (e) => {
  const tag = e.target.closest('[data-tag]');
  if (tag) window.insertLyricsTag($('lyrics'), tag.dataset.tag);
  const who = e.target.closest('[data-singer]');
  if (who && !window.setSinger($('lyrics'), who.dataset.singer)) {
    toast('Поставьте курсор в нужную часть песни — пометим, кто её поёт');
  }
});
$('lyricsAuto').addEventListener('click', () => {
  const area = $('lyrics');
  if (!area.value.trim()) { toast('Сначала вставьте текст песни — разметим его сами ✨'); return; }
  const before = area.value;
  area.value = window.structureLyrics(before, $('prompt').value, voice).slice(0, 5000);
  area.dispatchEvent(new Event('input', { bubbles: true }));
  toast('Разметили: припевы — по повторам, вставки — под ваш стиль. Поправьте, если нужно ✍️');
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
  if (!file && again && (mode !== 'create' || useReference)) { form.append('again', again); form.append('againFile', againFile); }
  const send = sendMode();
  form.append('mode', send.mode);
  form.append('preset', '');
  form.append('prompt', $('prompt').value);
  form.append('title', $('title').value);
  form.append('lyrics', $('instrumental').checked && mode === 'create' && !yue2On() ? '' : $('lyrics').value);
  form.append('audio_influence', $('audioKnob').value / 100);
  form.append('style_influence', $('styleKnob').value / 100);
  form.append('weirdness', $('weirdKnob').value / 100);
  form.append('melody', $('melodyKnob').value / 100);
  form.append('track', $('track').value);
  form.append('voice', voice);
  form.append('keep_vocals', send.keep);
  form.append('pro', mode === 'stems' && $('stemsPro').checked);
  form.append('engine', yue2On() ? 'yue2' : '');
  form.append('closeness', $('yClose').value);
  form.append('cfg_scale', $('yCfg').value);
  form.append('creativity', $('yCreative').value / 100);
  $('start').disabled = true;
  $('msg').textContent = mode === 'create' ? 'Отправляем…' : 'Загружаем трек…';
  const response = await fetch('/api/studio', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    $('msg').innerHTML = `<span class="bad">${pick(OOPS, Date.now())}</span> ${data.detail || 'Не получилось запустить.'}`;
    $('start').disabled = false;
    return;
  }
  // Новый трек -- сразу наверху «Моих треков», с подсветкой
  fresh = data.jobId;
  list.page = 0; list.query = ""; $("search").value = "";
  closeDetail();
  $('start').disabled = false;
  $('msg').textContent = '';
  await load();
  window.scrollTo({ top: $('listView').offsetTop - 80, behavior: 'smooth' });
});

document.addEventListener('click', async (e) => {
  const playBtn = e.target.closest('[data-play]');
  if (playBtn) {
    e.stopPropagation();
    play(playBtn.dataset.play, playBtn.dataset.title, playBtn.dataset.sub);
    return;
  }
  // ⋯ у строки разбора -- внутри самой строки: меню раньше, чем переход по строке
  const rowMenu = e.target.closest('[data-tabs] [data-menu]');
  if (rowMenu) { openMenu(rowMenu); return; }
  const tabs = e.target.closest('[data-tabs]');
  if (tabs) { location.href = `/player/${tabs.dataset.tabs}`; return; }
  const row = e.target.closest('[data-open]');
  if (row) { openDetail(row.dataset.open, true); return; }
  if (e.target.closest('#back')) {
    // «Назад» в карточке = «назад» браузера: та же страница и прокрутка
    if (history.state && history.state.detail) history.back(); else closeDetail(true);
    return;
  }
  const menuBtn = e.target.closest('[data-menu]');
  if (menuBtn) { openMenu(menuBtn); return; }
  const action = e.target.closest('[data-act]');
  if (action) { await act(action.dataset.act, action.dataset.job, action.dataset.file); return; }
  const notes = e.target.closest('[data-notes]');
  if (notes) { openNotes(notes.dataset.notes); return; }
  if (!e.target.closest('#menu')) $('menu').hidden = true;
});

function openMenu(button) {
  const menu = $('menu');
  const job = button.dataset.menu;
  const f = button.dataset.file || '';
  const item = (what, icon, text, extra = '') =>
    `<button type="button" data-act="${what}" data-job="${job}" data-file="${esc(f)}" ${extra}>${icon}<span>${text}</span></button>`;
  const analysis = button.dataset.analysis && ((info && info.analyses) || []).find((a) => a.id === job);
  menu.innerHTML = analysis ? tabsMenu(analysis) : button.dataset.track
    ? (button.dataset.done ? item('cover', ICON.again, 'Кавер на этот трек')
        + item('reference', ICON.tabs, 'Сверить аккорды с эталоном') : '')
      + (button.dataset.upload ? item('like', ICON.again, 'Похожая песня: стиль и слова из трека')
        : item('again', ICON.again, 'Повторить с этими настройками'))
      + '<hr>' + item('delete', ICON.trash, 'Удалить трек', 'class="danger"')
    : `<a href="${button.dataset.url}" download>${ICON.download}<span>Скачать mp3</span></a>`
      + `<a href="/studio/mix/${job}?file=${encodeURIComponent(f)}">${ICON.mix}<span>Открыть в мультитреке</span></a>`
      + item('tabs', ICON.tabs, 'Табы, аккорды и MIDI')
      + (button.dataset.splitOk ? item('split', ICON.split, 'Разделить на партии') : '')
      + (button.dataset.splitOk && info.restyleEngine === 'mureka'
        ? item('splitpro', ICON.split, 'Глубокое разделение + MIDI') : '')
      + (button.dataset.stem ? (button.dataset.midi
        ? `<a href="${button.dataset.midi}" download>${ICON.download}<span>Скачать MIDI партии</span></a>`
        : item('midi', ICON.tabs, button.dataset.busy ? 'MIDI уже считается…' : 'MIDI этой дорожки',
          button.dataset.busy ? 'disabled' : '')) : '')
      + (info.yue2Open && !button.dataset.stem ? item('notes', ICON.tabs, '🧪 Ноты, аккорды и MIDI (проба)') : '')
      + item('shift', ICON.tempo, 'Темп и тональность')
      + (button.dataset.extend ? item('extend', ICON.again, 'Продлить песню') : '')
      + (button.dataset.stem ? '' : item('reference', ICON.tabs, 'Сверить аккорды с эталоном'))
      + '<hr>' + (button.dataset.stem ? '' : item('cover', ICON.again, 'Кавер на эту версию'))
      + item('again', ICON.again, 'Повторить с этими настройками');
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
  // Стиль или слова не сохранились (например, «как в образце») -- услышим их в треке
  const missingLyrics = !j.lyrics && !(mode === 'create' && $('instrumental').checked && j.style);
  if ((!j.style || missingLyrics) && j.files && j.files.length) {
    fillFromTrack(j.id, j.files[0].name, { style: !j.style, lyrics: missingLyrics });
  }
}

// Стиль и слова готового трека (Mureka song/describe + recognize), с
// кэшем на сервере. null -- не вышло (сообщение уже показано).
async function describeTrack(jobId, fileName) {
  const url = `/api/studio/${jobId}/describe?file=${encodeURIComponent(fileName || '')}`;
  for (let tries = 0; tries < 90; tries++) {
    const answer = await fetch(url);
    const data = await answer.json().catch(() => ({}));
    if (!answer.ok || data.error) {
      toast(`Не получилось распознать стиль и слова: ${esc(data.detail || data.error || 'попробуйте позже')}`, 7000);
      return null;
    }
    if (!data.pending) return data;
    await new Promise((r) => setTimeout(r, 3000));
  }
  toast('Распознавание затянулось — попробуйте ещё раз через минуту');
  return null;
}

// Заполнить пустые поля тем, что услышали в треке; написанное не трогаем
async function fillFromTrack(jobId, fileName, { style = true, lyrics = true } = {}) {
  if (info.restyleEngine !== 'mureka') return;
  toast('Слушаем трек: распознаём стиль и слова… 🎧', 60000);
  const found = await describeTrack(jobId, fileName);
  if (!found) return;
  if (style && found.style && !$('prompt').value.trim()) $('prompt').value = found.style;
  if (!style && found.style) $('prompt').placeholder = `Новый стиль. В оригинале: ${found.style}`;
  if (lyrics && found.lyrics && !$('lyrics').value.trim()) $('lyrics').value = found.lyrics;
  if (lyrics && !found.lyrics && mode === 'create') {
    $('instrumental').checked = true;
    $('lyrics').disabled = true;
  }
  lyricsCount();
  saveDraft();
  updateStart();
  toast(found.lyrics || found.style
    ? 'Готово: стиль и слова — в панели слева, поправьте что хотите ✍️'
    : 'Слов в треке не нашли — похоже, это инструментал');
}

// ---------------------------------------------------------- сверка с эталоном
const MODEL_NAME = { main: 'На сайте', ext15: 'Прошлая (500 песен)', r2: 'Без sus/dim (61 класс)', v2: 'Самая первая', ens: 'Ансамбль (сайт + 500)', nokey: 'На сайте без подсказки тональности', tuned: 'На сайте с подстройкой строя' };
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const reference = { job: '', file: '' };

function openReference(jobId, fileName) {
  const j = info.jobs.find((x) => x.id === jobId);
  if (!j) return;
  reference.job = jobId;
  reference.file = fileName || (j.files[0] && j.files[0].name) || '';
  $('refTitle').textContent = `Сверить аккорды: ${j.name}`;
  $('refSheet').value = '';
  $('refResult').innerHTML = '';
  $('refModal').hidden = false;
  // уже сверяли -- сразу показать итог
  fetch(`/api/studio/${jobId}/reference?file=${encodeURIComponent(reference.file)}`)
    .then((r) => (r.ok ? r.json() : null)).then((data) => { if (data) showReference(data); }).catch(() => {});
}

function showReference(data) {
  if (data.pending) { $('refResult').innerHTML = '<p class="muted">Слушаем трек всеми моделями… около минуты ⏳</p>'; return; }
  if (data.error) { $('refResult').innerHTML = `<p class="bad">Не получилось: ${esc(data.error)}</p>`; return; }
  const rows = Object.entries(data.scores || {}).sort((a, b) => b[1].score - a[1].score);
  if (!rows.length) { $('refResult').innerHTML = ''; return; }
  const main = (data.scores || {}).main || rows[0][1];
  const shift = main.shift ? `<p class="muted">Похоже, эталон записан в другом строе: совпадение лучше со сдвигом на
    ${main.shift} полутон(а) — каподастр или другая тональность. Без сдвига — ${pct(main.unshifted)}.</p>` : '';
  $('refResult').innerHTML = `
    <p>Эталон: ${data.chords} аккордов.${data.key ? ` Тональность по звуку: <b>${esc(data.key)}</b>.` : ''}${data.tuning ? ` Строй записи: ${data.tuning > 0 ? '+' : ''}${Math.round(data.tuning * 100)} центов.` : ''} Модель на сайте нашла: <b>${esc(main.found.join(' ') || '—')}</b>
      ${main.missed.length ? `· не нашла: <b class="bad">${esc(main.missed.join(' '))}</b>` : '· все'}
      ${main.extra.length ? `· лишние: ${esc(main.extra.join(' '))}` : ''}</p>${shift}
    <table class="st-ref-table"><thead><tr><th>Модель</th><th>Итог</th><th title="доля звучания, где наш аккорд есть в эталоне">Покрытие</th>
      <th title="какую долю аккордов эталона нашли">Аккорды</th><th title="какую долю переходов эталона услышали">Переходы</th></tr></thead>
      <tbody>${rows.map(([m, s]) => `<tr${m === 'main' ? ' class="main"' : ''}><td>${esc(MODEL_NAME[m] || m)}</td>
        <td><b>${pct(s.score)}</b></td><td>${pct(s.coverage)}</td><td>${pct(s.vocab)}</td><td>${pct(s.transitions)}</td></tr>`).join('')}
      </tbody></table>`;
}

async function pollReference() {
  for (let tries = 0; tries < 120; tries++) {
    const data = await fetch(`/api/studio/${reference.job}/reference?file=${encodeURIComponent(reference.file)}`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (data) showReference(data);
    if (data && !data.pending) return;
    await new Promise((r) => setTimeout(r, 3000));
  }
}

$('refGo').addEventListener('click', async () => {
  const form = new FormData();
  form.append('file', reference.file);
  form.append('sheet', $('refSheet').value);
  $('refGo').disabled = true;
  const answer = await fetch(`/api/studio/${reference.job}/reference`, { method: 'POST', body: form });
  const data = await answer.json().catch(() => ({}));
  $('refGo').disabled = false;
  if (!answer.ok) { $('refResult').innerHTML = `<p class="bad">${esc(data.detail || 'Не получилось')}</p>`; return; }
  showReference({ pending: true });
  pollReference();
});
$('refClose').addEventListener('click', () => { $('refModal').hidden = true; });
$('refModal').addEventListener('click', (e) => { if (e.target === $('refModal')) $('refModal').hidden = true; });
let allTimer = 0;
async function showAll() {
  clearTimeout(allTimer);
  if ($('refModal').hidden) return;
  const data = await fetch('/api/studio/benchmark').then((r) => r.json()).catch(() => null);
  if (!data || !data.rows.length) { $('refResult').innerHTML = '<p class="muted">Эталонов пока нет.</p>'; return; }
  const models = Object.keys(data.average).sort((a, b) => data.average[b] - data.average[a]);
  const waiting = data.rows.filter((r) => r.pending).length;
  // Пока идёт пересверка -- таблица обновляется сама
  if (waiting) allTimer = setTimeout(showAll, 5000);
  // Кнопка видна всегда: уже идущие сверки сервер второй раз не ставит
  $('refResult').innerHTML = `<p><button type="button" id="refRerun">Пересверить все</button>
    ${waiting ? ` Пересверяем по очереди: осталось ${waiting} ⏳` : ''}<br>Все эталоны: ${data.rows.length}. В среднем — ${models.map((m) =>
    `${esc(MODEL_NAME[m] || m)}: <b>${pct(data.average[m])}</b>`).join(' · ')}</p>
    <div class="st-ref-scroll"><table class="st-ref-table st-ref-all"><thead><tr><th>Песня</th>${models.map((m) => `<th>${esc(MODEL_NAME[m] || m)}</th>`).join('')}</tr></thead>
    <tbody>${data.rows.map((r) => `<tr><td>${esc(r.name)}</td>${models.map((m) =>
      `<td>${r.pending ? '…' : r.scores[m] ? pct(r.scores[m].score) : '—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  const rerun = $('refRerun');
  if (rerun) {
    rerun.addEventListener('click', async () => {
      rerun.disabled = true;
      await fetch('/api/studio/benchmark/rerun', { method: 'POST' }).catch(() => null);
      showAll();
    });
  }
}
$('refAll').addEventListener('click', showAll);

// «Похожая песня» из загруженного трека: песня с нуля в его стиле и со
// словами из него -- полный перенос того, что слышно в записи
function likeOf(jobId, fileName) {
  const j = info.jobs.find((x) => x.id === jobId);
  if (!j) return;
  mode = 'create';
  $('title').value = j.name.replace(/\.[a-z0-9]{2,4}$/i, '');
  $('prompt').value = '';
  $('lyrics').value = '';
  $('instrumental').checked = false;
  $('lyrics').disabled = false;
  showMode();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  fillFromTrack(jobId, fileName || (j.files[0] && j.files[0].name));
}

// «Кавер на этот трек»: сам трек (выбранная версия) -- исходник для
// «Переделать»: мелодия и текст остаются, стиль -- новый
function coverOf(jobId, fileName) {
  const j = info.jobs.find((x) => x.id === jobId);
  if (!j || !j.files || !j.files.length) return;
  const version = j.files.find((f) => f.name === fileName) || j.files[0];
  mode = 'restyle';
  $('prompt').value = '';
  const label = j.files.length > 1 && version.label ? ` · ${version.label}` : '';
  useSource(j.id, `${j.name}${label}`, version.name);
  $('lyrics').value = j.lyrics || '';
  showMode();
  updateStart();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  setTimeout(() => $('prompt').focus(), 400);
  toast(`«${esc(j.name)}» — исходник для кавера. Опишите новый стиль и жмите «Переделать» 🎸`);
  // слова -- в поле, чтобы их можно было поправить; стиль оригинала -- подсказкой
  fillFromTrack(j.id, version.name, { style: false, lyrics: !j.lyrics });
}

async function act(what, jobId, fileName) {
  $('menu').hidden = true;
  if (what === 'again') { repeat(jobId); return; }
  if (what === 'cover') { coverOf(jobId, fileName); return; }
  if (what === 'like') { likeOf(jobId, fileName); return; }
  if (what === 'reference') { openReference(jobId, fileName); return; }
  if (what === 'deltabs') {
    const a = ((info && info.analyses) || []).find((x) => x.id === jobId);
    if (!confirm(`Удалить разбор «${a ? a.name : ''}» вместе с табами и MIDI?\n\nОтменить это нельзя.`)) return;
    const response = await fetch(`/api/job/${jobId}`, { method: 'DELETE' });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      toast(data.detail || 'Не удалось удалить');
    }
    load();
    return;
  }
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
    const j = info.jobs.find((x) => x.id === jobId) || {};
    const known = (j.keys || {})[fileName] || (j.files && j.files[0] && j.files[0].name === fileName ? j : {});
    const choice = await askShift(known.key, known.bpm);
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
  if (what === 'midi') {
    const response = await fetch(`/api/studio/${jobId}/midi`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) toast(`${pick(OOPS, Date.now())} ${data.detail || ''}`, 7000);
    else toast('Переводим партию в ноты — MIDI появится у партии через минуту 🎼');
    load();
    return;
  }
  if (what === 'notes') {
    const response = await fetch(`/api/studio/${jobId}/notes`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (response.ok) toast('Снимаем ноты, аккорды и MIDI — несколько минут 🎼');
    else toast(`${pick(OOPS, Date.now())} ${data.detail || ''}`);
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
    // Уже разбирали эту версию -- открываем готовое, а не платим ещё раз
    const done = ((info && info.analyses) || []).find((a) => a.studio === jobId && a.studioFile === fileName
      && a.status !== 'error');
    if (done) { location.href = `/player/${done.id}`; return; }
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

const SHARPS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const FLATS = { Db: 'C#', Eb: 'D#', Gb: 'F#', Ab: 'G#', Bb: 'A#' };

// «Am» + 2 полутона -> «Bm»
function transposeKey(key, semitones) {
  const m = /^([A-G][#b]?)(.*)$/.exec(key || '');
  if (!m) return '';
  const index = SHARPS.indexOf(FLATS[m[1]] || m[1]);
  return index < 0 ? '' : SHARPS[(index + Number(semitones) + 120) % 12] + m[2];
}

function keyOf(j, name) {
  const k = (j.keys || {})[name];
  return k && k.key ? ` · <b class="st-key">${esc(k.key)}${k.bpm ? ` · ${k.bpm} BPM` : ''}</b>` : '';
}

function askShift(key, bpm) {
  return new Promise((resolve) => {
    const modal = $('shiftModal');
    const sync = () => {
      const tone = Number($('shiftTone').value);
      const rate = Number($('shiftTempo').value);
      $('shiftToneVal').textContent = tone > 0 ? `+${tone}` : String(tone);
      $('shiftTempoVal').textContent = `${rate}%`;
      const now = [key, bpm ? `${bpm} BPM` : ''].filter(Boolean).join(' · ');
      const next = [key ? transposeKey(key, tone) : '', bpm ? `${Math.round(bpm * rate / 100)} BPM` : '']
        .filter(Boolean).join(' · ');
      $('shiftNow').innerHTML = now
        ? `Сейчас: <b>${esc(now)}</b>${tone || rate !== 100 ? ` → станет: <b class="st-key">${esc(next)}</b>` : ''}`
        : 'Тональность ещё определяем — откройте окно через пару секунд.';
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
// Полоса над нижним плеером -- ползунок: клик, перетаскивание (мышь и
// палец), стрелки с клавиатуры; над полосой -- время, куда перемотаем
function seekFrac(e) {
  const box = $('pSeek').getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
}
function showSeek(frac, e) {
  const dur = $('audio').duration || 0;
  $('pBar').style.width = `${frac * 100}%`;
  if (!dur || !e) return;
  $('pTip').hidden = false;
  $('pTip').textContent = time(frac * dur) || '0:00';
  const box = $('pSeek').getBoundingClientRect();
  $('pTip').style.left = `${Math.max(24, Math.min(box.width - 24, e.clientX - box.left))}px`;
}
$('pSeek').addEventListener('pointerdown', (e) => {
  if (!$('audio').duration) return;
  seeking = true;
  $('pSeek').classList.add('dragging');
  $('pSeek').setPointerCapture(e.pointerId);
  showSeek(seekFrac(e), e);
  e.preventDefault();
});
$('pSeek').addEventListener('pointermove', (e) => {
  if (seeking) showSeek(seekFrac(e), e);
  else if (e.pointerType === 'mouse' && $('audio').duration) {
    const dur = $('audio').duration;
    const box = $('pSeek').getBoundingClientRect();
    $('pTip').hidden = false;
    $('pTip').textContent = time(seekFrac(e) * dur) || '0:00';
    $('pTip').style.left = `${Math.max(24, Math.min(box.width - 24, e.clientX - box.left))}px`;
  }
});
function endSeek(e) {
  if (!seeking) return;
  seeking = false;
  $('pSeek').classList.remove('dragging');
  const audio = $('audio');
  if (audio.duration) audio.currentTime = seekFrac(e) * audio.duration;
  $('pTip').hidden = true;
  syncTime();
}
$('pSeek').addEventListener('pointerup', endSeek);
$('pSeek').addEventListener('pointercancel', (e) => { seeking = false; $('pSeek').classList.remove('dragging'); $('pTip').hidden = true; syncTime(); });
$('pSeek').addEventListener('pointerleave', () => { if (!seeking) $('pTip').hidden = true; });
$('pSeek').addEventListener('keydown', (e) => {
  const audio = $('audio');
  if (!audio.duration) return;
  const step = { ArrowLeft: -5, ArrowRight: 5, PageDown: -30, PageUp: 30 }[e.key];
  if (step === undefined && !['Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  audio.currentTime = e.key === 'Home' ? 0 : e.key === 'End' ? audio.duration - 0.5
    : Math.max(0, Math.min(audio.duration - 0.2, audio.currentTime + step));
  syncTime();
});

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

// Громкость. На iPhone громкость <audio> из скрипта не меняется, поэтому
// там звук идёт через Web Audio. Но Web Audio на iPhone молчит при
// включённом беззвучном режиме (а <audio> -- играет): у владельца звук
// пропал насовсем после первого касания ползунка (06.10). Поэтому:
//  - Web Audio -- только на iPhone/iPad, и в режиме «воспроизведение»
//    (navigator.audioSession, Safari 16.4+), как у музыкальных приложений;
//  - нет такого режима -- ползунок прячем, громкость -- кнопками телефона;
//  - на остальных устройствах -- обычная громкость <audio>.
const IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const IOS_VOLUME = IOS && 'audioSession' in navigator;
if (IOS && !IOS_VOLUME) $('fVolume').closest('.st-full-volume').style.display = 'none';
let gainNode = null;
let audioContext = null;
function setVolume(percent) {
  const level = Math.max(0, Math.min(1, percent / 100));
  if (!IOS) { $('audio').volume = level; }
  else if (IOS_VOLUME) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!gainNode && Context) {
      try {
        navigator.audioSession.type = 'playback';   // играть и в беззвучном режиме
        audioContext = new Context();
        const source = audioContext.createMediaElementSource($('audio'));
        gainNode = audioContext.createGain();
        source.connect(gainNode).connect(audioContext.destination);
        $('audio').addEventListener('play', () => { if (audioContext.state !== 'running') audioContext.resume(); });
      } catch (error) { gainNode = null; }
    }
    if (audioContext && audioContext.state !== 'running') audioContext.resume().catch(() => {});
    if (gainNode) gainNode.gain.value = level;
  }
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

// ---------------------------------------------------------- ноты (abcjs)
// Нотный стан из ABC, который пишет YuE2 или снимает SheetSage2. Библиотека
// abcjs (MIT) лежит у нас (/static/vendor) и грузится только по кнопке.
let abcjsLoading = null;
function loadAbcjs() {
  if (!abcjsLoading) {
    abcjsLoading = new Promise((ok, fail) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/abcjs-basic-min.js';
      script.onload = ok;
      script.onerror = () => { abcjsLoading = null; fail(new Error('abcjs')); };
      document.head.append(script);
    });
  }
  return abcjsLoading;
}

const KEY_NAMES = { major: 'мажор', minor: 'минор' };
async function openNotes(jobId) {
  const data = await fetch(`/api/studio/${jobId}/abc`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!data) { toast('Нот у этой работы нет'); return; }
  $('notesTitle').textContent = `Ноты: ${data.name}`;
  const [tonic, scale] = (data.key || '').split(':');
  $('notesInfo').textContent = tonic ? `Тональность: ${tonic} ${KEY_NAMES[scale] || scale || ''}`.trim() : '';
  $('notesAbc').href = URL.createObjectURL(new Blob([data.abc], { type: 'text/plain' }));
  $('notesAbc').download = `${data.name.replace(/\.[a-z0-9]{2,4}$/i, '')}.abc`;
  $('notesPaper').innerHTML = '<p class="muted">Рисуем ноты…</p>';
  $('notesModal').hidden = false;
  $('notesPaper').scrollTop = 0;
  history.pushState({ ...(history.state || {}), notes: true }, '');   // «назад» закрывает ноты, а не уходит со страницы
  try {
    await loadAbcjs();
    // Рисуем во вложенный блок: abcjs меняет стили своего контейнера, и
    // прокрутка нот внутри окна ломалась
    $('notesPaper').innerHTML = '<div id="notesSheet"></div>';
    // На телефоне -- по 2 такта в строке и в натуральную ширину: ужатые
    // под узкий экран 4 такта читать невозможно
    const narrow = $('notesPaper').clientWidth < 600;
    window.ABCJS.renderAbc('notesSheet', data.abc, narrow ? {
      add_classes: true, staffwidth: Math.max(260, $('notesPaper').clientWidth - 40),
      wrap: { minSpacing: 1.6, maxSpacing: 2.4, preferredMeasuresPerLine: 2 },
    } : {
      responsive: 'resize', add_classes: true,
      wrap: { minSpacing: 1.8, maxSpacing: 2.7, preferredMeasuresPerLine: 4 }, staffwidth: 860,
    });
  } catch (error) {
    $('notesPaper').innerHTML = '<p class="bad">Не получилось нарисовать ноты — скачайте ABC или MIDI.</p>';
  }
}
// Закрыть ноты: ✕, «Закрыть», тап мимо окна, Esc и жест «назад» на телефоне
function closeNotes(fromHistory) {
  if ($('notesModal').hidden) return;
  $('notesModal').hidden = true;
  if (!fromHistory && history.state && history.state.notes) history.back();
}
$('notesClose').addEventListener('click', () => closeNotes(false));
$('notesX').addEventListener('click', () => closeNotes(false));
$('notesModal').addEventListener('click', (e) => { if (e.target === $('notesModal')) closeNotes(false); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeNotes(false); });
window.addEventListener('popstate', () => {
  closeNotes(true);
  const id = history.state && history.state.detail;
  if (id && id !== openJob && info) openDetail(id, false);
  else if (!id && openJob) closeDetail(true);
});
$('notesPrint').addEventListener('click', () => {
  document.body.classList.add('print-notes');
  window.print();
  setTimeout(() => document.body.classList.remove('print-notes'), 500);
});

// «Мои треки» в меню ведёт сюда же (/studio#tracks): из карточки трека -- к списку
window.addEventListener('hashchange', () => {
  if (location.hash !== '#tracks') return;
  if (openJob) closeDetail(true);
  $('listView').scrollIntoView({ block: 'start' });
});
