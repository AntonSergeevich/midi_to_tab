// Студия: слева -- что сделать, справа -- мои треки; клик по
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
  // плеер: свои значки NASLUX -- тонкая линия, скруглённые концы
  repeat: '<svg viewBox="0 0 24 24"><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H19"/><path d="m16 3 3 3-3 3"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H5"/><path d="m8 21-3-3 3-3"/></svg>',
  repeatOne: '<svg viewBox="0 0 24 24"><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H19"/><path d="m16 3 3 3-3 3"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H5"/><path d="m8 21-3-3 3-3"/><path d="M11 10.5 12.5 9.5V15"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24"><path d="M3 7h3.2c1.3 0 2.5.7 3.2 1.8l5.2 7.4c.7 1.1 1.9 1.8 3.2 1.8H21"/><path d="m18 15 3 3-3 3"/><path d="M3 17h3.2c1 0 1.9-.4 2.6-1.1"/><path d="M13.2 8.1A3.9 3.9 0 0 1 15.6 7H21"/><path d="m18 4 3 3-3 3"/></svg>',
  addList: '<svg viewBox="0 0 24 24"><path d="M4 6h12M4 11h12M4 16h7"/><path d="M18 13v7M14.5 16.5h7"/></svg>',
  share: '<svg viewBox="0 0 24 24"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M7 10H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-1"/></svg>',
  sliders: '<svg viewBox="0 0 24 24"><path d="M5 4v6M5 14v6M12 4v2M12 10v10M19 4v9M19 17v3"/><circle cx="5" cy="12" r="2"/><circle cx="12" cy="8" r="2"/><circle cx="19" cy="15" r="2"/></svg>',
  list: '<svg viewBox="0 0 24 24"><path d="M4 6h11M4 11h11M4 16h7M17 14v6m-3-3h6"/></svg>',
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
  pencil: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
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
let fScrub = null;        // ведут пальцем по перемотке полноэкранного плеера
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
  // Проба новой нейросети (GenAPI) -- у владельца; включена -- главнее YuE2
  $('hitBox').hidden = !(info && info.hitOpen && !info.hitPublic && ['create', 'restyle'].includes(mode));
  const hit = hitOn();
  $('hitOpts').hidden = !hit;
  $('contBox').hidden = !(hit && mode === 'restyle');
  const pub = Boolean(info && info.yue2Public) && ['create', 'restyle'].includes(mode) && !hit;
  const yue2Here = Boolean(info && info.yue2Open) && ['create', 'restyle'].includes(mode) && !hit;
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
  $('lyricsNote').textContent = mode === 'restyle' && hit && $('contRec').checked
    ? '— текст продолжения: следующий куплет или припев; пусто — инструментальное продолжение'
    : mode === 'restyle' && hit
    ? '— у своей записи нейросеть споёт этот текст, без текста выйдет инструментал; у чужой песни слова распознаем сами'
    : mode === 'restyle' && yue2 ? '— необязательно: распознаем сами'
    : mode === 'restyle' && mureka ? '— необязательно' : '';
  updateStart();
}

// «Дописать → Все партии»: на деле это переделка с вашим голосом (Mureka
// пишет новую аранжировку под записанный вокал) -- так и отправляем, и цена её
// Новая нейросеть: свои песни и каверы своих записей (чужие песни сервер сам отдаст YuE2)
function hitOn() {
  if (!['create', 'restyle'].includes(mode) || !info) return false;
  return Boolean(info.hitPublic) || (Boolean(info.hitOpen) && $('useHit').checked);
}
function yue2On() {
  if (!['create', 'restyle'].includes(mode) || hitOn()) return false;
  return Boolean(info && info.yue2Public) || (Boolean(info && info.yue2Open) && $('useYue2').checked);
}

function sendMode() {
  if (mode === 'enrich' && $('track').value === 'all') return { mode: 'restyle', keep: true, all: true };
  if (mode === 'restyle' && hitOn() && $('contRec').checked) return { mode: 'continue', keep: false, all: false };
  return { mode, keep: mode === 'restyle' && $('keepVocals').checked && !yue2On(), all: false };
}

function updateStart() {
  if (!info) return;
  styleAiState();
  const send = sendMode();
  const service = mode === 'stems' && $('stemsPro').checked ? info.services.stems_pro : info.services[send.mode];
  const packCost = (send.keep ? service.packKeep : service.pack) || 0;
  const byPack = packCost && info.studioCredits >= packCost;
  const rubCost = send.keep ? service.priceKeep : service.price;
  const enough = info.unlimited || byPack || info.balance >= rubCost;
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
  // Цена -- в кредитах (одна «валюта», 30.10); рубли -- только тем, кто
  // платит остатком прежнего рублёвого баланса
  const price = info.unlimited ? '' : !byPack && packCost && info.balance >= rubCost
    ? ` · ${rub(rubCost)}` : packCost ? ` · ${packCost} ${plural(packCost, 'кредит', 'кредита', 'кредитов')}` : ` · ${rub(rubCost)}`;
  const button = send.all ? 'Переписать все партии' : MODE[mode].button;
  $('start').textContent = needPay ? `Оплатить и ${button.toLowerCase()}${price}` : `${button}${price}`;
  $('trackHint').hidden = !send.all;
  $('msg').innerHTML = problem || (needPay
    ? (info.registered ? 'Докупите кредиты — всё заполненное дождётся вас здесь.'
      : 'Заведите аккаунт и докупите кредиты — всё заполненное дождётся вас здесь.')
    : ['create', 'restyle'].includes(send.mode) ? 'Две версии на выбор, обычно 1–3 минуты.' : 'Обычно пара минут.');
}

// «15 кредитов» -- а у кого кредитов мало, но есть прежний рублёвый баланс, -- «49 ₽»
function costText(pack, price) {
  if (pack && (info.studioCredits >= pack || info.balance < price)) return `${pack} ${plural(pack, 'кредит', 'кредита', 'кредитов')}`;
  return rub(price);
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
  if (j.expired) return '<span class="muted">файлы удалены: трек долго не открывали. Загрузите его заново и отметьте ★</span>';
  if (j.status !== 'done') {
    const tick = Math.floor(Date.now() / 7000) + seedOf(j.id);
    return `<span class="muted">${esc(j.stage || 'В очереди')}</span>
      <span class="st-fun">${pick(WAIT, tick)}</span>
      <div class="bar done"><i style="width:${Math.max(4, j.progress)}%"></i></div>
      <button type="button" class="st-linkbtn st-stop" data-stop="${j.id}">Остановить</button>`;
  }
  const when = new Date(j.at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
  const length = j.files[0] && j.files[0].seconds ? ` · ${time(j.files[0].seconds)}` : '';
  const key = j.key ? ` · <b class="st-key">${esc(j.key)}${j.bpm ? ` · ${j.bpm} BPM` : ''}</b>` : '';
  // Скоро уборка: за неделю предупреждаем -- ★ сохраняет трек навсегда
  const left = j.expiresAt && !j.fav ? Math.ceil((j.expiresAt * 1000 - Date.now()) / 86400000) : 99;
  const soon = left <= 7 ? `<span class="st-expire">удалится через ${Math.max(1, left)} ${plural(Math.max(1, left), 'день', 'дня', 'дней')} — ☆ сохранит навсегда</span>` : '';
  return `<span class="muted">${esc(MODE[j.mode] ? MODE[j.mode].title : j.title)}${key}${length} · ${when}</span>${soon}`;
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
const list = { query: '', page: 0, jobs: [], fav: false, lists: false };
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
  list.fav = Boolean(saved.fav);
  list.lists = Boolean(saved.lists);
  listScroll = Number(saved.scroll) || 0;
  if (list.query) $('search').value = list.query;
} catch (error) { /* нет хранилища -- с первой страницы */ }
function saveList() {
  try {
    sessionStorage.setItem(LIST_KEY, JSON.stringify({
      page: list.page, query: list.query, fav: list.fav, lists: list.lists, scroll: openJob ? listScroll : window.scrollY }));
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
  const favs = all.filter((j) => j.fav);
  const tab = list.lists ? 'lists' : list.fav ? 'fav' : 'all';
  document.querySelectorAll('[data-favtab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.favtab === tab)));
  if (list.lists) { renderPlaylists(); return; }
  const pool = list.fav ? favs : all;
  const top = words.length ? pool.filter((j) => matches(j, words)) : pool;
  $('count').textContent = pool.length ? (words.length ? `${top.length} из ${pool.length}` : `${pool.length}`) : '';
  $('searchBox').hidden = pool.length <= PER_PAGE && !list.query;
  const pages = Math.max(1, Math.ceil(top.length / PER_PAGE));
  list.page = Math.min(list.page, pages - 1);
  renderPages(pages);
  if (!top.length && list.fav && !words.length) {
    $('jobs').innerHTML = '<p class="muted">В избранном пока пусто. Нажмите ☆ у трека — он появится здесь.</p>';
    return;
  }
  if (!top.length) {
    $('jobs').innerHTML = pool.length
      ? `<p class="muted">Ничего не нашлось по «${esc(list.query)}».</p>`
      : '<p class="muted">Здесь появятся ваши песни.</p>';
    return;
  }
  const shown = top.slice(list.page * PER_PAGE, (list.page + 1) * PER_PAGE);
  $('jobs').innerHTML = shown.map((j) => {
    if (j.mode === 'tabs') return tabsRow(j);
    const first = j.status === 'done' && j.files[0];
    return `<div class="st-swipe" data-swipe="${j.id}"><span class="st-swipe-bg" aria-hidden="true">
      <i class="st-swipe-fav">${j.fav ? '☆ Убрать из избранного' : '★ В избранное'}</i><i class="st-swipe-del">Удалить</i></span>
      <div class="st-row${j.id === fresh ? ' st-new' : ''}" data-open="${j.id}">
      ${cover(j)}<span class="st-mode">${MODE_ICON[j.mode] || ''}</span>
      <div class="st-row-main"><span class="st-titleline"><b data-noi18n data-rename="${j.id}">${esc(j.name)}</b>${renameButton(j)}</span>${statusLine(j)}</div>
      ${favButton(j)}${first ? playButton(first, j.name) : ''}
    </div></div>`;
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

function rerender() {
  renderList(info.jobs);
  if (openJob) renderDetail(info.jobs);
}

// Название трека -- своё: карандаш у названия (а в карточке -- и само название)
function renameButton(j) {
  return `<button type="button" class="st-rename" data-renamebtn="${j.id}" title="Переименовать"
    aria-label="Переименовать">${ICON.pencil}</button>`;
}

function startRename(id) {
  const title = document.querySelector(`#detailView:not([hidden]) [data-rename="${id}"]`)
    || document.querySelector(`#jobs [data-rename="${id}"]`);
  if (!title || title.isContentEditable) return;
  const items = [...((info && info.jobs) || []), ...((info && info.analyses) || [])].filter((x) => x.id === id);
  if (!items.length) return;
  const before = items[0].name;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'st-rename-input';
  input.value = before;
  input.maxLength = 120;
  input.setAttribute('aria-label', 'Новое название');
  input.dataset.noi18n = '';
  const line = title.closest('.st-titleline');
  line.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (keep) => {
    if (done) return;
    done = true;
    const name = input.value.trim().replace(/\s+/g, ' ');
    if (!keep || !name || name === before) { rerender(); return; }
    items.forEach((x) => { x.name = name; });
    rerender();
    const form = new FormData();
    form.append('name', name);
    const answer = await fetch(`/api/job/${id}/rename`, { method: 'POST', body: form }).catch(() => null);
    if (!answer || !answer.ok) {
      items.forEach((x) => { x.name = before; });
      rerender();
      toast('Не получилось переименовать — проверьте интернет');
    }
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') finish(false);
  });
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('click', (e) => e.stopPropagation());
}

// Звёздочка: в избранное и обратно
function favButton(j) {
  return `<button type="button" class="icon-btn st-fav${j.fav ? ' on' : ''}" data-fav="${j.id}"
    aria-pressed="${j.fav ? 'true' : 'false'}" title="${j.fav ? 'Убрать из избранного' : 'В избранное'}"
    aria-label="${j.fav ? 'Убрать из избранного' : 'В избранное'}">${ICON.star}</button>`;
}

async function toggleFav(id) {
  const items = [...((info && info.jobs) || []), ...((info && info.analyses) || [])].filter((x) => x.id === id);
  if (!items.length) return;
  const on = !items[0].fav;
  window.haptic(on ? 'success' : 'light');
  items.forEach((x) => { x.fav = on; });           // сразу на экране, не дожидаясь сервера
  // перерисовка -- сразу после нажатия, а не внутри него: на iPhone «тик»
  // играет невидимый переключатель внутри нажатой кнопки, ей нельзя исчезнуть раньше
  setTimeout(rerender, 0);
  const form = new FormData();
  form.append('on', on);
  const answer = await fetch(`/api/job/${id}/favorite`, { method: 'POST', body: form }).catch(() => null);
  if (!answer || !answer.ok) {
    items.forEach((x) => { x.fav = !on; });
    rerender();
    toast('Не получилось — проверьте интернет');
    return;
  }
  toast(on ? 'Добавлено в избранное ★' : 'Убрано из избранного');
}

function tabsRow(a, label) {
  return `<div class="st-row${label ? ' st-tabsub' : ''}" data-tabs="${a.id}" title="Открыть аккорды и табы">
    ${label ? '' : cover(a)}<span class="st-mode">♫</span>
    <div class="st-row-main">${label ? `<b>${esc(label)}</b>` : `<span class="st-titleline"><b data-noi18n data-rename="${a.id}">${esc(a.name)}</b>${renameButton(a)}</span>`}${tabsLine(a)}</div>
    ${label ? '' : favButton(a)}<button type="button" class="icon-btn" data-menu="${a.id}" data-analysis="1" title="Что сделать">${ICON.more}</button>
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

// Раскрытый текст песни остаётся раскрытым: экран трека перерисовывается
// при каждом обновлении списка (статусы, разборы), и без памяти текст
// сворачивался сам посреди прослушивания
const lyricsOpen = new Set();
document.addEventListener('toggle', (e) => {
  const box = e.target;
  if (!box.dataset || !box.dataset.lyrics) return;
  if (box.open) lyricsOpen.add(box.dataset.lyrics);
  else lyricsOpen.delete(box.dataset.lyrics);
}, true);

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
        data-url="${f.url}" data-split-ok="${j.mode !== 'stems' ? 1 : ''}" data-extend="${f.mid || f.hit ? 1 : ''}"
        data-hit="${f.hit ? 1 : ''}" data-wav="${f.wav || ''}" data-karaoke="${f.karaoke ? 1 : ''}"
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
  $('detailView').innerHTML = `
    <div class="st-dhead">
      <button type="button" class="icon-btn" id="back" title="Назад">${ICON.back}</button>
      ${cover(j, true)}
      <div class="st-row-main"><span class="st-titleline"><h2 data-noi18n data-rename="${j.id}" title="Нажмите, чтобы переименовать">${esc(j.name)}</h2>${renameButton(j)}</span><span class="muted">${esc(meta)}</span></div>
      ${favButton(j)}${['done', 'error'].includes(j.status)
    ? `<button type="button" class="icon-btn" data-menu="${j.id}" data-track="1" data-upload="${j.mode === 'upload' ? 1 : ''}" data-done="${j.status === 'done' && j.files.length && j.mode !== 'stems' ? 1 : ''}" title="Что сделать">${ICON.more}</button>` : ''}
    </div>
    <div class="st-dgrid"><div class="st-dmain">
    <div class="st-label">${j.mode === 'stems' ? 'Партии' : 'Версии'}</div>
    ${versions}${sheetBlock(j)}${j.mode === 'stems' ? midiList(j) : ''}${midi}${parts}${analyses}</div>
    ${trackInfo(j)}</div>`;
  markInfoVersion();
  paintArt(j);
}

// Цвет обложки -- акцент страницы трека: сияние сверху, отметка версии,
// тень обложки. Берём самый сочный цвет картинки, а не средний бурый.
const artColors = {};
function paintArt(j) {
  const view = $('detailView');
  view.classList.toggle('st-arty', Boolean(j.cover));
  if (!j.cover) { view.style.removeProperty('--art'); view.style.removeProperty('--art-img'); return; }
  view.style.setProperty('--art-img', `url("${j.cover}")`);
  const apply = (color) => { if (openJob === j.id && color) view.style.setProperty('--art', color); };
  if (j.cover in artColors) { apply(artColors[j.cover]); return; }
  const img = new Image();
  img.onload = () => {
    try {
      const side = 32;
      const canvas = document.createElement('canvas');
      canvas.width = side; canvas.height = side;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, side, side);
      const d = ctx.getImageData(0, 0, side, side).data;
      const px = [];
      for (let i = 0; i < d.length; i += 4) {
        const max = Math.max(d[i], d[i + 1], d[i + 2]);
        const min = Math.min(d[i], d[i + 1], d[i + 2]);
        px.push([max ? ((max - min) / max) * max : 0, d[i], d[i + 1], d[i + 2]]);
      }
      px.sort((a, b) => b[0] - a[0]);
      const top = px.slice(0, Math.max(8, px.length >> 3));
      const avg = [1, 2, 3].map((k) => Math.round(top.reduce((sum, p) => sum + p[k], 0) / top.length));
      artColors[j.cover] = `rgb(${avg.join(',')})`;
    } catch (error) { artColors[j.cover] = ''; }
    apply(artColors[j.cover]);
  };
  img.src = j.cover;
}

// «О треке» -- справа от версий (на телефоне -- под ними): обложка,
// название, стиль и текст с «Копировать» и «В форму», голос, темп и
// тональность выбранной версии. Понравился стиль -- не писать заново.
let infoFile = '';
const META_FACTS = [['lyricist', 'Слова'], ['composer', 'Музыка'], ['album', 'Альбом'], ['year', 'Год'], ['genre', 'Жанр']];
function trackInfo(j) {
  const files = j.files || [];
  if (!files.some((f) => f.name === infoFile)) infoFile = files.length ? files[0].name : '';
  const f = files.find((x) => x.name === infoFile) || {};
  const k = (j.keys || {})[infoFile] || {};
  const voice = j.voice && info.voices ? info.voices[j.voice] : '';
  const facts = [
    ['Что это', MODE[j.mode] ? MODE[j.mode].title : j.title],
    ['Версия', f.label], ['Длина', time(f.seconds)],
    ['Тональность', k.key || j.key], ['Темп', (k.bpm || j.bpm) ? `${k.bpm || j.bpm} BPM` : ''],
    ['Голос', voice], ...META_FACTS.map(([key, title]) => [title, (j.meta || {})[key]]),
    ['Создан', j.at ? new Date(j.at * 1000).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : ''],
  ].filter(([, v]) => v);
  const copyBox = (kind, title, text) => (text ? `<div class="st-info-block">
      <div class="st-info-head"><b>${title}</b>
        <button type="button" class="st-linkbtn" data-copy="${kind}">Копировать</button>
        <button type="button" class="st-linkbtn" data-useinfo="${kind}">В форму</button></div>
      <${kind === 'lyrics' ? 'pre' : 'p'} class="st-info-text" data-noi18n>${esc(text)}</${kind === 'lyrics' ? 'pre' : 'p'}></div>` : '');
  const done = j.status === 'done' && j.mode !== 'stems';
  const drawing = coverWaiting(j);
  const coverActs = done ? `<div class="st-info-coveracts">
      ${j.cover ? `<a class="st-linkbtn" href="${j.cover}" download="${esc(j.name)}.jpg">Скачать</a>` : ''}
      ${j.coverLeft || drawing ? `<button type="button" class="st-linkbtn" data-act="drawcover" data-job="${j.id}"${drawing
    ? ' disabled>Рисуем…' : `>🎨 ${j.cover ? 'Перерисовать' : 'Нарисовать'}`}</button>` : ''}
      <button type="button" class="st-linkbtn" data-coverup="${j.id}">🖼 Своя картинка</button></div>`
    : j.cover ? `<a class="st-linkbtn" href="${j.cover}" download="${esc(j.name)}.jpg">Скачать обложку</a>` : '';
  const lyricsBox = j.lyrics ? copyBox('lyrics', 'Текст песни', j.lyrics) : done ? `<div class="st-info-block">
      <div class="st-info-head"><b>Текст песни</b></div>
      ${j.lyricsBusy ? '<p class="muted st-info-busy">Распознаём слова… обычно 2–5 минут, страницу можно закрыть</p>'
    : `<button type="button" class="st-info-read" data-readlyrics="${j.id}">📝 Распознать текст с записи</button>
      <p class="muted st-info-note">${j.lyricsError ? `Не вышло: ${esc(j.lyricsError)}` : 'Бесплатно. Слова появятся здесь и в суфлёре плеера'}</p>`}</div>` : '';
  return `<aside class="st-info" data-info="${j.id}" aria-label="О треке">
    ${j.cover ? `<img class="st-info-cover" src="${j.cover}" alt="Обложка">` : cover(j, true)}
    ${coverActs}
    <div class="st-info-head"><b class="st-info-title" data-noi18n>${esc(j.name)}</b>
      <button type="button" class="st-linkbtn" data-copy="title">Копировать</button>
      ${j.status === 'done' ? `<button type="button" class="st-linkbtn" data-editmeta="${j.id}">✎ Свойства</button>` : ''}</div>
    ${(j.meta || {}).artist ? `<p class="st-info-artist" data-noi18n>${esc(j.meta.artist)}</p>` : ''}
    <dl class="st-info-facts">${facts.map(([t, v]) => `<dt>${t}</dt><dd>${esc(String(v))}</dd>`).join('')}</dl>
    ${copyBox('style', 'Стиль', j.style)}${lyricsBox}
    ${['done', 'error'].includes(j.status) && j.mode !== 'stems' ? `<button type="button" class="st-info-again" data-act="again" data-job="${j.id}">↻ Повторить с этими настройками</button>` : ''}
  </aside>`;
}
function markInfoVersion() {
  document.querySelectorAll('#detailView .st-version').forEach((row) => {
    const menu = row.querySelector('[data-menu][data-file]');
    row.classList.toggle('st-picked', Boolean(menu && menu.dataset.file === infoFile && openJob === menu.dataset.menu));
  });
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (error) { /* старый браузер -- ниже */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.cssText = 'position:fixed;opacity:0';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  area.remove();
  return ok;
}
document.addEventListener('click', async (e) => {
  const j = info && info.jobs && info.jobs.find((x) => x.id === openJob);
  if (!j || !e.target.closest('#detailView')) return;
  const copy = e.target.closest('[data-copy]');
  const use = e.target.closest('[data-useinfo]');
  const meta = e.target.closest('[data-editmeta]');
  if (meta) { editMeta(meta.dataset.editmeta); return; }
  const coverUp = e.target.closest('[data-coverup]');
  if (coverUp) { uploadCover(coverUp.dataset.coverup); return; }
  const read = e.target.closest('[data-readlyrics]');
  if (read) { read.disabled = true; readLyrics(read.dataset.readlyrics); return; }
  if (copy) {
    const text = { title: j.name, style: j.style, lyrics: j.lyrics }[copy.dataset.copy] || '';
    const what = { title: 'Название', style: 'Стиль', lyrics: 'Текст' }[copy.dataset.copy];
    toast(await copyText(text) ? `${what} скопирован${copy.dataset.copy === 'title' ? 'о' : ''}` : 'Не получилось скопировать');
    if (window.haptic) window.haptic('light');
    return;
  }
  if (use) {
    // в левую панель «Создать»: поле стиля или текста, остальное -- как было
    const kind = use.dataset.useinfo;
    if (mode !== 'create' && mode !== 'restyle') document.querySelector('[data-mode="create"]').click();
    const field = kind === 'style' ? $('prompt') : $('lyrics');
    field.value = (kind === 'style' ? j.style : j.lyrics).slice(0, Number(field.maxLength) > 0 ? Number(field.maxLength) : 5000);
    field.dispatchEvent(new Event('input', { bubbles: true }));
    saveDraft();
    updateStart();
    toast(kind === 'style' ? 'Стиль — в поле «Стиль»' : 'Текст — в поле «Текст песни»');
    field.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  // Нажали на версию -- в «О треке» её темп, тональность и длина
  const row = e.target.closest('#detailView .st-version');
  const menu = row && row.querySelector('[data-menu][data-file]');
  if (menu && !e.target.closest('button, a, input') && menu.dataset.menu === j.id) {
    infoFile = menu.dataset.file;
    const aside = document.querySelector('#detailView .st-info');
    if (aside) aside.outerHTML = trackInfo(j);
    markInfoVersion();
  }
});

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

// MIDI всех партий одной кнопкой (кроме барабанов: их ноты так не услышать)
// и все готовые MIDI одним архивом
const DRUM_PART = /drum|барабан|ударн/i;
function midiList(c) {
  if (c.mode !== 'stems') return '';
  const parts = (c.files || []).filter((f) => !DRUM_PART.test(f.name) && !DRUM_PART.test(f.label || ''));
  const ready = parts.filter((f) => (c.midi || []).some((m) => m.name === `${f.name.replace(/\.[^.]+$/, '')}.mid`));
  const busy = (c.midiBusy || []).length;
  const button = !parts.length || ready.length === parts.length ? ''
    : busy ? `<button type="button" class="st-info-read" disabled>🎼 Переводим в ноты… готово ${ready.length} из ${parts.length}</button>`
      : `<button type="button" class="st-info-read" data-midiall="${c.id}">🎼 MIDI всех партий${ready.length ? ` (осталось ${parts.length - ready.length})` : ''} — бесплатно</button>`;
  const zip = (c.midi || []).length ? `<a class="st-linkbtn" href="/api/studio/${c.id}/midi.zip" download>⬇ Все MIDI одним архивом</a>` : '';
  const hint = c.pro ? '' : '<p class="muted st-midi-hint">Барабаны в MIDI — через «Глубокое разделение + MIDI» в ⋯ у версии трека.</p>';
  return `<div class="st-midiall">${button}${zip}</div>${hint}`;
}
document.addEventListener('click', async (e) => {
  const all = e.target.closest('[data-midiall]');
  if (!all) return;
  all.disabled = true;
  const r = await fetch(`/api/studio/${all.dataset.midiall}/midi/all`, { method: 'POST' }).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  toast(r && r.ok ? 'Переводим партии в ноты по одной — минута-две на каждую 🎼' : data.detail || 'Не получилось начать');
  load();
});

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
  if (!document.querySelector('.st-rename-input')) {    // пишут новое название -- не перерисовывать под рукой
    renderList(info.jobs);
    if (openJob) renderDetail(info.jobs);
  }
  if (!listRestored) {                 // вернулись на страницу «назад»
    listRestored = true;
    const back = history.state && history.state.detail;
    if (back && info.jobs.some((j) => j.id === back)) openDetail(back, false);
    else if (listScroll && !location.search) window.scrollTo({ top: listScroll, behavior: 'auto' });
  }
  updateStart();
  const analyzing = await ensureKeys().catch(() => false);
  const working = (s) => s === 'queued' || s === 'running';
  hitDone();
  if (((info && info.hitBusy) || []).some((b) => working(b.status))) return 4000;
  if (info.jobs.some((j) => working(j.status) || (j.midiBusy || []).length)
      || (info.analyses || []).some((a) => working(a.status) || a.made.some((m) => working(m.status)))) {
    return 5000;
  }
  if (analyzing) return 6000;            // тональность и темп вот-вот будут
  if (info.jobs.some((j) => j.lyricsBusy)) return 6000;   // распознаём слова
  if (info.jobs.some((j) => j.status === 'done' && (coverWaiting(j)
      || (!j.cover && ['create', 'restyle', 'continue'].includes(j.mode) && Date.now() / 1000 - j.at < 900)))) {
    return 15000;                        // обложка ещё рисуется
  }
  if (info.jobs.some((j) => j.status === 'error')) return 30000;   // «не получилось» само исчезнет
  return null;
}

// Тональность и темп: разбор на сервере по запросу, не больше двух треков
// за раз (открытый трек -- первым). true -- что-то ещё считается.
const keyAsked = {};
// Ждём обложку: { until, was } -- пока ссылка не сменилась (перерисовка) или не появилась
const coverWait = {};
function coverWaiting(j) {
  const w = coverWait[j.id];
  return Boolean(w && w.until > Date.now() && j.cover === w.was);
}
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
let playingList = null;           // имя плейлиста, который играет (или null -- версии трека)
let repeatMode = 'off';           // off | all | one
let shuffleOn = false;            // перемешано -- baseQueue хранит исходный порядок
let baseQueue = [];
try { shuffleOn = localStorage.getItem('naslux.shuffle') === '1'; } catch (error) { /* */ }
try { repeatMode = localStorage.getItem('naslux.repeat') || 'off'; } catch (error) { /* */ }

// Скачивание mp3 из Студии -- со свойствами трека и обложкой внутри файла
function withTags(url) {
  return /^\/api\/studio\/file\/[^?]+\.mp3$/.test(url || '') ? `${url}?dl=1` : url;
}

function coverStyle(j) {
  if (j && j.cover) return `url("${j.cover}")`;
  let h = 0;
  for (const ch of (j ? j.id : 'x')) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${h} 70% 45%), hsl(${(h + 60) % 360} 70% 30%))`;
}

// Строка очереди плеера: версия (или партия) трека -- с названием,
// обложкой, текстом и суфлёром «родителя»
function queueItem(j, f) {
  const parent = j.from ? info.jobs.find((x) => x.id === j.from) : null;
  const owner = parent || j;
  return {
    url: f.url, title: owner.name, sub: f.label, art: coverStyle(owner), cover: owner.cover || '',
    lyrics: owner.lyrics || '', kar: f.karaoke ? { job: j.id, file: f.name } : null, job: j.id, file: f.name,
  };
}

function queueFor(url) {
  for (const j of (info ? info.jobs : [])) {
    const index = (j.files || []).findIndex((f) => f.url === url);
    if (index < 0) continue;
    return { index, items: j.files.map((f) => queueItem(j, f)) };
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
  setQueue(found ? found.items : [{ url, title, sub, art: coverStyle(null), lyrics: '' }], found ? found.index : 0, null);
  startTrack();
}

// Новая очередь: с какого трека и как называется; перемешивание включено --
// этот трек первым, остальные вразброс (исходный порядок -- в baseQueue)
function setQueue(items, start, name) {
  baseQueue = items;
  playingList = name;
  if (shuffleOn && items.length > 1) {
    const first = items[start];
    queue = [first, ...shuffled(items.filter((x) => x !== first))];
    qi = 0;
  } else {
    queue = items;
    qi = start;
  }
}

function startTrack() {
  const item = queue[qi];
  if (!item) return;
  const audio = $('audio');
  playing = item.url;
  audio.src = item.url;
  if (Number($('fVolume').value) !== 100) setVolume(Number($('fVolume').value));
  audio.play();
  $('playerTitle').textContent = item.title;
  $('playerSub').textContent = item.sub;
  $('fTitle').textContent = item.title;
  $('fSub').textContent = item.sub;
  ['pCover', 'fCover'].forEach((id) => { $(id).style.backgroundImage = item.art; });
  $('fBg').style.backgroundImage = item.art;
  $('fDownload').href = withTags(item.url);
  $('fLyrics').textContent = item.lyrics;
  $('fLyrics').classList.remove('kk');
  $('fLyrics')._kk = null;
  if (!item.kar) $('full').classList.remove('kk-on');
  $('fLyricsBtn').hidden = !item.lyrics && !item.kar;
  $('fLyricsBtn').textContent = item.kar ? 'Суфлёр' : 'Текст песни';
  if (item.kar) {
    window.Karaoke.load(item.kar.job, item.kar.file).then((words) => {
      if (queue[qi] !== item || !words.length) return;
      window.Karaoke.render($('fLyrics'), words);
      window.Karaoke.update($('fLyrics'), $('audio').currentTime);
    });
  }
  const many = queue.length > 1;
  ['pPrev', 'pNext', 'fPrev', 'fNext'].forEach((id) => { $(id).disabled = !many; });
  $('fQueue').textContent = playingList ? `${playingList} · ${qi + 1} из ${queue.length}` : '';
  lockScreen(item);
  if (list.lists && !$('listView').hidden) renderPlaylists();    // подсветить, что играет
  $('player').hidden = false;
  playerSpace();
  syncButtons();
}

// Полоска плеера внизу не должна закрывать кнопку «Создать / Переделать»:
// кнопка (липкая к низу экрана) встаёт над плеером
function playerSpace() {
  const on = !$('player').hidden;
  document.body.classList.toggle('has-player', on);
  if (on) document.documentElement.style.setProperty('--player-h', `${$('player').offsetHeight}px`);
}
window.addEventListener('resize', () => { if (!$('player').hidden) playerSpace(); });

// Экран блокировки и наушники: название, обложка, «дальше/назад» --
// слушать плейлист на улице, не доставая телефон
function lockScreen(item) {
  if (!('mediaSession' in navigator)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: item.title, artist: item.sub || 'NASLUX', album: playingList || 'NASLUX',
      artwork: item.cover ? [{ src: new URL(item.cover, location.href).href, sizes: '1024x1024', type: 'image/jpeg' }]
        : [{ src: '/static/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
    });
  } catch (error) { /* старый браузер */ }
}
if ('mediaSession' in navigator) {
  const on = (action, handler) => { try { navigator.mediaSession.setActionHandler(action, handler); } catch (error) { /* */ } };
  on('play', () => $('audio').play());
  on('pause', () => $('audio').pause());
  on('previoustrack', () => { if ($('audio').currentTime > 4 || queue.length < 2) $('audio').currentTime = 0; else step(-1); });
  on('nexttrack', () => step(1));
  on('seekbackward', () => { $('audio').currentTime = Math.max(0, $('audio').currentTime - 10); });
  on('seekforward', () => { $('audio').currentTime += 10; });
  on('seekto', (e) => { if (e.seekTime != null) $('audio').currentTime = e.seekTime; });
}

function step(delta) {
  if (queue.length < 2) return;
  qi = (qi + delta + queue.length) % queue.length;
  startTrack();
}

// Плеер во весь экран выезжает снизу и уезжает вниз (владелец, 30.10:
// «открывается очень быстро, не чувствуется»), как шторка в плеерах телефона
let fullAnim = null;
const CALM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function fullPlayer(open) {
  const full = $('full');
  clearTimeout(fullAnim);
  if (open) {
    if (!full.hidden && !full.classList.contains('closing')) return;
    full.classList.remove('closing');
    full.hidden = false;
    document.body.style.overflow = 'hidden';
    if (CALM) return;
    if (!full.style.transform) {
      full.style.transition = 'none';
      full.style.transform = 'translateY(100%)';
      void full.offsetHeight;                     // зафиксировать начало, потом -- ехать
    }
    // кривая шторок iPhone: плавный разгон и мягкая остановка, 0,55 с
    full.style.transition = 'transform .55s cubic-bezier(.32,.72,0,1), border-radius .55s';
    full.style.borderRadius = full.style.borderRadius || '22px 22px 0 0';
    requestAnimationFrame(() => { full.style.transform = 'translateY(0)'; full.style.borderRadius = ''; });
    fullAnim = setTimeout(() => { full.style.transition = ''; full.style.transform = ''; }, 580);
    return;
  }
  if (full.hidden) return;
  document.body.style.overflow = '';
  if (CALM) { full.hidden = true; return; }
  full.classList.add('closing');
  full.style.transition = 'transform .4s cubic-bezier(.32,0,.67,0), border-radius .4s';
  full.style.borderRadius = '22px 22px 0 0';
  full.style.transform = 'translateY(100%)';
  fullAnim = setTimeout(() => {
    full.hidden = true;
    full.classList.remove('closing');
    full.style.transition = ''; full.style.transform = ''; full.style.borderRadius = '';
  }, 410);
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
  if (!fScrub) $('fCur').textContent = time(cur) || '0:00';
  $('fDur').textContent = time(dur) || '0:00';
  if (!$('fSeek').matches(':active') && !fScrub) $('fSeek').value = dur ? Math.round((cur / dur) * 1000) : 0;
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
// «Дописать с ИИ»: из слов человека -- подробный стиль (ИИ-шлюз Beget).
// Пустое поле -- кнопка неактивна: дописывать нечего.
let styleBefore = null;
function styleAiState() {
  const btn = $('styleAi');
  btn.hidden = !(info && info.styleAi);
  btn.disabled = !$('prompt').value.trim() || $('prompt').disabled || btn.classList.contains('busy');
  btn.title = btn.disabled && !btn.classList.contains('busy')
    ? 'Сначала опишите стиль своими словами — ИИ сделает из этого подробное описание'
    : 'ИИ допишет жанр, темп, голос, инструменты и звук — по вашему описанию';
}
$('prompt').addEventListener('input', () => {
  styleAiState();
  if (styleBefore !== null) { styleBefore = null; $('styleUndo').hidden = true; }
});
$('styleAi').addEventListener('click', async () => {
  const style = $('prompt').value.trim();
  if (!style) return;
  const btn = $('styleAi');
  btn.classList.add('busy');
  btn.disabled = true;
  btn.querySelector('span:last-child').textContent = 'Дописываю…';
  const form = new FormData();
  form.append('style', style);
  form.append('lyrics', $('lyrics').value || '');
  const response = await fetch('/api/studio/style', { method: 'POST', body: form }).catch(() => null);
  const data = response ? await response.json().catch(() => ({})) : {};
  btn.classList.remove('busy');
  btn.querySelector('span:last-child').textContent = 'Дописать с ИИ';
  if (!response || !response.ok || !data.style) {
    toast(data.detail || `Не получилось дописать стиль (${response ? `ответ ${response.status}` : 'нет связи'}) — попробуйте ещё раз`);
    styleAiState();
    return;
  }
  styleBefore = $('prompt').value;
  $('prompt').value = data.style.slice(0, Number($('prompt').maxLength) || 500);
  $('styleUndo').hidden = false;
  if (window.haptic) window.haptic('success');
  styleAiState();
  saveDraft();
  updateStart();
});
$('styleUndo').addEventListener('click', () => {
  if (styleBefore === null) return;
  $('prompt').value = styleBefore;
  styleBefore = null;
  $('styleUndo').hidden = true;
  styleAiState();
  saveDraft();
  updateStart();
});

$('styleIdea').addEventListener('click', () => {
  const current = $('prompt').value;
  let idea = current;
  // По-английски -- и идеи по-английски (нейросеть понимает оба)
  while (idea === current) idea = window.__t(STYLE_IDEAS[Math.floor(Math.random() * STYLE_IDEAS.length)]);
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
$('useHit').addEventListener('change', showMode);
$('contRec').addEventListener('change', () => { showMode(); updateStart(); });
// Заливка полосы до бегунка (--fill в app.css)
const knobFill = (el) => el.style.setProperty('--fill', `${((el.value - el.min) / (el.max - el.min)) * 100}%`);
$('yCfg').addEventListener('input', () => { $('yCfgVal').textContent = Number($('yCfg').value).toFixed(1); knobFill($('yCfg')); });
$('yCreative').addEventListener('input', () => { $('yCreativeVal').textContent = `${$('yCreative').value}%`; knobFill($('yCreative')); });
knobFill($('yCfg'));
knobFill($('yCreative'));
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
    toast('Поставьте курсор на строку (или выделите несколько) — пометим, кто её поёт');
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
// 🗑 у «Стиля» и «Текста песни»: очистить одним нажатием (выделять и стирать
// длинный текст на телефоне неудобно), с «Вернуть» на случай промаха
document.querySelectorAll('[data-clear]').forEach((button) => button.addEventListener('click', () => {
  const area = $(button.dataset.clear);
  if (!area.value) { area.focus(); return; }
  const before = area.value;
  area.value = '';
  area.dispatchEvent(new Event('input', { bubbles: true }));
  area.focus();
  toast(`${area.id === 'lyrics' ? 'Текст очищен' : 'Стиль очищен'}
    <button type="button" class="st-linkbtn" id="undoClear">Вернуть</button>`, 8000);
  $('undoClear').onclick = () => {
    area.value = before;
    area.dispatchEvent(new Event('input', { bubbles: true }));
    $('toast').hidden = true;
  };
}));
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
  form.append('engine', hitOn() ? 'hit' : yue2On() ? 'yue2' : '');
  if (hitOn()) {
    form.append('negative', $('negative').value.trim());
    form.append('wild', $('wild').checked);
  }
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
  if (Date.now() - swiped < 450 && e.target.closest('.st-swipe, #player, #full')) return;   // это был свайп, а не нажатие
  const renameBtn = e.target.closest('[data-renamebtn]');
  if (renameBtn) { e.stopPropagation(); startRename(renameBtn.dataset.renamebtn); return; }
  const titleClick = e.target.closest('#detailView [data-rename]');
  if (titleClick) { startRename(titleClick.dataset.rename); return; }
  if (e.target.closest('.st-rename-input')) return;
  const fav = e.target.closest('[data-fav]');
  if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); return; }
  const favTab = e.target.closest('[data-favtab]');
  if (favTab) {
    list.fav = favTab.dataset.favtab === 'fav';
    list.lists = favTab.dataset.favtab === 'lists';
    list.page = 0;
    saveList();
    renderList(list.jobs);
    return;
  }
  const playBtn = e.target.closest('[data-play]');
  if (playBtn) {
    e.stopPropagation();
    play(playBtn.dataset.play, playBtn.dataset.title, playBtn.dataset.sub);
    return;
  }
  // «Остановить» идущую работу -- раньше перехода по строке трека
  const stop = e.target.closest('[data-stop]');
  if (stop) {
    if (!confirm('Остановить эту работу? Деньги или генерация вернутся.')) return;
    stop.disabled = true;
    const r = await fetch(`/api/studio/${stop.dataset.stop}/cancel`, { method: 'POST' });
    toast(r.ok ? 'Остановили — деньги вернулись' : 'Не получилось остановить');
    load();
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
  const ungift = e.target.closest('[data-ungift]');
  if (ungift) {
    const r = await fetch(`/api/gift/${encodeURIComponent(ungift.dataset.ungift)}`, { method: 'DELETE' });
    toast(r.ok ? 'Ссылка закрыта — по ней больше не получить трек. Те, кто уже получил, его сохранят'
      : 'Не получилось закрыть ссылку');
    return;
  }
  const unshare = e.target.closest('[data-unshare]');
  if (unshare) {
    const r = await fetch(`/api/share/${encodeURIComponent(unshare.dataset.unshare)}`, { method: 'DELETE' });
    toast(r.ok ? 'Доступ по ссылке закрыт — она больше не открывается' : 'Не получилось закрыть доступ');
    return;
  }
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
    : `<a href="${withTags(button.dataset.url)}" download>${ICON.download}<span>Скачать mp3</span></a>`
      + `<a href="/studio/mix/${job}?file=${encodeURIComponent(f)}">${ICON.mix}<span>Открыть в мультитреке</span></a>`
      + item('tabs', ICON.tabs, 'Табы, аккорды и MIDI')
      + (button.dataset.splitOk ? item('minus', ICON.split, button.dataset.hit
        ? `Минус и голос${hitPrice('hitminus')}` : 'Минус без голоса') : '')
      + (button.dataset.splitOk ? item('split', ICON.split, button.dataset.hit
        ? `Разделить на 12 дорожек${hitPrice('stems12')}` : 'Разделить на партии') : '')
      + (button.dataset.splitOk && info.restyleEngine === 'mureka' && !button.dataset.hit
        ? item('splitpro', ICON.split, 'Глубокое разделение + MIDI') : '')
      + (button.dataset.hit ? hitItems(item, job, f, button.dataset) : '')
      + (button.dataset.stem ? (button.dataset.midi
        ? `<a href="${button.dataset.midi}" download>${ICON.download}<span>Скачать MIDI партии</span></a>`
        : item('midi', ICON.tabs, button.dataset.busy ? 'MIDI уже считается…' : 'MIDI этой дорожки',
          button.dataset.busy ? 'disabled' : '')) : '')
      + (info.yue2Open && !button.dataset.stem ? item('notes', ICON.tabs, '🧪 Ноты, аккорды и MIDI (проба)') : '')
      + item('toplaylist', ICON.list, 'Добавить в плейлист')
      + item('shift', ICON.tempo, 'Темп и тональность')
      + (button.dataset.stem ? '' : item('share', ICON.mix, 'Поделиться ссылкой: послушать без входа'))
      + item('gift', ICON.split, 'Передать музыканту — со всеми дорожками')
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

// Версия новой нейросети: 12 дорожек, WAV для релиза, суфлёр (текст в такт)
function hitPrice(kind) {
  const s = info && info.services && info.services[kind];
  return !s || info.unlimited ? '' : ` · ${s.pack} кр.`;
}
function hitBusyFor(job, file, kind) {
  return ((info && info.hitBusy) || []).find((b) => b.of === job && b.file === file && b.kind === kind
    && ['queued', 'running'].includes(b.status));
}
function hitItems(item, job, f, data) {
  const wavBusy = hitBusyFor(job, f, 'wav');
  const kBusy = hitBusyFor(job, f, 'karaoke');
  return (data.wav ? `<a href="${data.wav}" download>${ICON.download}<span>Скачать WAV для релиза</span></a>`
      : item('wav', ICON.download, wavBusy ? `Готовим WAV… ${Math.round(wavBusy.progress || 0)}%` : `WAV для релиза${hitPrice('wav')}`,
        wavBusy ? 'disabled' : ''))
    + (data.karaoke ? item('karaokeplay', ICON.tabs, 'Суфлёр: текст в такт')
      : item('karaoke', ICON.tabs, kBusy ? 'Суфлёр готовится…' : `Суфлёр: текст в такт${hitPrice('karaoke')}`,
        kBusy ? 'disabled' : ''));
}
async function hitAction(kind, jobId, fileName) {
  const form = new FormData();
  form.append('file', fileName || '');
  const response = await fetch(`/api/studio/${jobId}/hit/${kind}`, { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(response.status === 402 ? `${esc(data.detail || '')} <a href="/pricing?next=/studio">Пополнить</a>`
      : `${pick(OOPS, Date.now())} ${esc(data.detail || '')}`, 9000);
    return;
  }
  if (window.haptic) window.haptic('light');
  if (data.ready && kind === 'karaoke') { karaokePlay(jobId, fileName); return; }
  toast({ stems12: 'Раскладываем на 12 дорожек — появятся под треком через 3–4 минуты 🎛',
    wav: 'Готовим WAV — через минуту он появится в меню «⋯» этой версии 💿',
    karaoke: 'Расставляем слова по времени — через несколько секунд суфлёр будет готов 🎤' }[kind]);
  load();
}
// Суфлёр: играть версию и открыть плеер с текстом, который идёт в такт
function karaokePlay(jobId, fileName) {
  const j = info.jobs.find((x) => x.id === jobId);
  const f = j && j.files.find((x) => x.name === fileName);
  if (!f) return;
  if (playing !== f.url) play(f.url, j.name, f.label);
  fullPlayer(true);
  $('fLyrics').hidden = false;
  $('full').classList.add('kk-on');      // обложка поменьше -- текст на виду
}
const hitSeen = new Set();
function hitDone() {
  // служебная задача закончилась -- сказать, где результат
  const now = new Set(((info && info.hitBusy) || []).filter((b) => ['queued', 'running'].includes(b.status))
    .map((b) => `${b.of}/${b.file}/${b.kind}`));
  hitSeen.forEach((key) => {
    if (now.has(key)) return;
    hitSeen.delete(key);
    const [job, file, kind] = key.split('/');
    const failed = ((info && info.hitBusy) || []).find((b) => `${b.of}/${b.file}/${b.kind}` === key && b.status === 'error');
    if (failed) toast(`Не получилось: ${esc(failed.error || '')}. Деньги вернулись.`, 9000);
    else if (kind === 'wav') toast('WAV для релиза готов — «⋯» у версии → «Скачать WAV» 💿', 9000);
    else if (kind === 'karaoke') toast(`Суфлёр готов — <button type="button" class="st-linkbtn" data-kplay="${esc(job)}" data-kfile="${esc(file)}">открыть</button> 🎤`, 12000);
  });
  now.forEach((key) => hitSeen.add(key));
}
document.addEventListener('click', (e) => {
  const k = e.target.closest('[data-kplay]');
  if (k) karaokePlay(k.dataset.kplay, k.dataset.kfile);
});

// Обложка по кнопке: рисуется минуту-две, пока ждём (coverWait) -- опрос и «Рисуем…»
async function drawCover(jobId) {
  if (window.haptic) window.haptic('light');
  const r = await fetch(`/api/studio/${jobId}/cover`, { method: 'POST' });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { toast(data.detail || 'Не получилось нарисовать обложку'); return; }
  if (!data.ready) {
    const j = info.jobs.find((x) => x.id === jobId);
    coverWait[jobId] = { until: Date.now() + 4 * 60 * 1000, was: j ? j.cover : null };
    toast('Рисуем обложку по смыслу песни — появится через минуту-две');
  }
  load();
}

// Своя обложка: картинка с телефона или компьютера
function uploadCover(jobId) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png,image/webp,image/gif,image/bmp';
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) { toast('Картинка больше 15 МБ'); return; }
    toast('Ставим обложку…');
    const form = new FormData();
    form.append('file', file);
    const r = await fetch(`/api/studio/${jobId}/cover/upload`, { method: 'POST', body: form }).catch(() => null);
    const data = r ? await r.json().catch(() => ({})) : {};
    toast(r && r.ok ? 'Обложка обновлена' : data.detail || 'Не получилось поставить обложку');
    if (r && r.ok && window.haptic) window.haptic('success');
    load();
  });
  input.click();
}

// Слова с записи: Whisper на сервере, пару минут -- опрос сам покажет текст
async function readLyrics(jobId) {
  const form = new FormData();
  form.append('file', infoFile);
  const r = await fetch(`/api/studio/${jobId}/lyrics/read`, { method: 'POST', body: form }).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  if (!r || !r.ok) { toast(data.detail || 'Не получилось начать распознавание'); return; }
  toast('Распознаём слова — обычно 2–5 минут');
  load();
}

// Свойства трека: название, исполнитель, авторы, альбом, год, жанр, текст --
// видны в «О треке» и попадают внутрь скачанного mp3
function editMeta(jobId) {
  const j = info.jobs.find((x) => x.id === jobId);
  if (!j) return;
  const m = j.meta || {};
  $('metaName').value = j.name || '';
  ['artist', 'lyricist', 'composer', 'album', 'year', 'genre'].forEach((k) => { $(`meta_${k}`).value = m[k] || ''; });
  $('metaLyrics').value = j.lyrics || '';
  $('metaModal').dataset.job = jobId;
  $('metaModal').hidden = false;
  $('metaName').focus();
}
$('metaCancel').addEventListener('click', () => { $('metaModal').hidden = true; });
$('metaModal').addEventListener('click', (e) => { if (e.target === $('metaModal')) $('metaModal').hidden = true; });
$('metaForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const jobId = $('metaModal').dataset.job;
  const form = new FormData();
  form.append('name', $('metaName').value);
  ['artist', 'lyricist', 'composer', 'album', 'year', 'genre'].forEach((k) => form.append(k, $(`meta_${k}`).value));
  form.append('lyrics', $('metaLyrics').value);
  const r = await fetch(`/api/studio/${jobId}/meta`, { method: 'POST', body: form }).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  if (!r || !r.ok) { toast(data.detail || 'Не получилось сохранить'); return; }
  $('metaModal').hidden = true;
  toast('Свойства сохранены — они будут и в скачанном mp3');
  load();
});
async function act(what, jobId, fileName) {
  $('menu').hidden = true;
  if (what === 'again') { repeat(jobId); return; }
  if (what === 'cover') { coverOf(jobId, fileName); return; }
  if (what === 'drawcover') { drawCover(jobId); return; }
  if (what === 'toplaylist') { addToPlaylist(jobId, fileName); return; }
  if (['stems12', 'wav', 'karaoke'].includes(what)) { hitAction(what, jobId, fileName); return; }
  if (what === 'karaokeplay') { karaokePlay(jobId, fileName); return; }
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
    window.haptic('warn');
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
  if (what === 'gift') {
    // Передать трек музыканту: по ссылке он войдёт -- и трек со всеми
    // дорожками (и раскладкой мультитрека с этого устройства) уже у него
    const j = info.jobs.find((x) => x.id === jobId) || {};
    const root = j.from && info.jobs.some((x) => x.id === j.from) ? j.from : jobId;
    const layout = {};
    try {
      const prefix = `naslux.mix.${root}.`;
      for (let n = 0; n < localStorage.length; n++) {
        const key = localStorage.key(n);
        if (key && key.startsWith(prefix)) layout[key.slice(prefix.length)] = JSON.parse(localStorage.getItem(key) || '{}');
      }
    } catch (error) { /* без раскладки -- дорожки всё равно передадутся */ }
    const gift = new FormData();
    gift.append('layout', JSON.stringify(layout).slice(0, 200000));
    const response = await fetch(`/api/studio/${jobId}/gift`, { method: 'POST', body: gift });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { toast(esc(data.detail || 'Не получилось сделать ссылку'), 12000); return; }
    const url = `${location.origin}${data.path}`;
    let how = 'Ссылка скопирована';
    try {
      if (navigator.share && matchMedia('(pointer: coarse)').matches) {
        await navigator.share({ title: 'Трек с дорожками в NASLUX', url });
        how = 'Ссылка готова';
      } else await navigator.clipboard.writeText(url);
    } catch (error) { how = 'Ссылка готова'; }
    toast(`${how}: <a href="${esc(data.path)}" target="_blank" rel="noopener">${esc(url)}</a>`
      + ' — музыкант войдёт, и трек со всеми дорожками появится в его «Моих треках»'
      + `${data.taken ? ` · уже получили: ${data.taken}` : ''}`
      + ` · <button type="button" class="st-linkbtn" data-ungift="${esc(data.token)}">закрыть доступ</button>`, 20000);
    return;
  }
  if (what === 'share') {
    // Ссылка «послушать»: музыкантам, друзьям, для продвижения -- без входа
    const response = await fetch(`/api/studio/${jobId}/share`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { toast(esc(data.detail || 'Не получилось сделать ссылку'), 9000); return; }
    const url = `${location.origin}${data.path}`;
    let how = 'Ссылка скопирована';
    try {
      if (navigator.share && matchMedia('(pointer: coarse)').matches) { await navigator.share({ url }); how = 'Ссылка готова'; }
      else await navigator.clipboard.writeText(url);
    } catch (error) { how = 'Ссылка готова'; }
    toast(`${how}: <a href="${esc(data.path)}" target="_blank" rel="noopener">${esc(url)}</a>`
      + `${data.plays ? ` · прослушиваний: ${data.plays}` : ''} · <button type="button" class="st-linkbtn" data-unshare="${esc(data.token)}">закрыть доступ</button>`, 15000);
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
  if (what === 'minus') {
    // Минус и голос отдельно -- в один клик (раньше: разделить на 6 партий и глушить вокал)
    const service = info.services.stems;
    const price = info.unlimited ? '' : ` за ${costText(service.pack, service.price)}`;
    if (!confirm(`Сделать минус — дорожку без голоса (и голос отдельно)${price}?`)) return;
    form.append('minus', true);
    const response = await fetch(`/api/studio/${jobId}/stems`, { method: 'POST', body: form });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      toast(`${pick(OOPS, Date.now())} ${error.detail || ''}`);
    } else toast('Делаем минус — появится под треком за минуту 🎤');
    load();
    return;
  }
  if (what === 'split' || what === 'splitpro') {
    const pro = what === 'splitpro';
    const service = pro ? info.services.stems_pro : info.services.stems;
    const price = info.unlimited ? '' : ` за ${costText(service.pack, service.price)}`;
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
    $('extendOk').textContent = `Продлить · ${info.unlimited ? '' : costText(info.services.extend.pack, info.services.extend.price)}`;
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
  $('voicePrice').textContent = info.unlimited ? '' : `Стоит ${costText(info.services.voice.pack, info.services.voice.price)}. Голос хранится у вас, использовать можно сколько угодно.`;
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
// Конец трека: «повтор одного» -- заново (заучивать), дальше по очереди,
// «повтор всех» -- с начала очереди по кругу
$('audio').addEventListener('ended', () => {
  if (repeatMode === 'one') { $('audio').currentTime = 0; $('audio').play(); return; }
  if (qi < queue.length - 1) step(1);
  else if (repeatMode === 'all' && queue.length) { qi = 0; startTrack(); } else syncButtons();
});
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

// ---------------------------------------------------------------- жесты
// Как в музыкальных приложениях на телефоне. Направления -- как просил
// владелец (17.10): вправо -- следующий трек, влево -- предыдущий.
//   плеер во весь экран: вниз -- свернуть, вправо/влево -- трек;
//   полоска плеера: вверх -- во весь экран, вправо/влево -- трек;
//   строка трека в списке: вправо -- в избранное, влево -- удалить.
// Порог пройден -- лёгкий «щелчок» (haptic), отпустили -- действие.
let swiped = 0;

function swipe(el, { canStart = () => true, onMove, onEnd }) {
  let g = null;
  el.addEventListener('touchstart', (e) => {
    g = null;
    if (e.touches.length !== 1 || !canStart(e)) return;
    const t = e.touches[0];
    g = { x: t.clientX, y: t.clientY, dx: 0, dy: 0, mode: '', target: e.target };
  }, { passive: true });
  el.addEventListener('touchmove', (e) => {
    if (!g) return;
    const t = e.touches[0];
    g.dx = t.clientX - g.x;
    g.dy = t.clientY - g.y;
    if (!g.mode) {
      if (Math.abs(g.dx) < 10 && Math.abs(g.dy) < 10) return;
      g.mode = Math.abs(g.dx) > Math.abs(g.dy) * 1.2 ? 'h' : 'v';
    }
    if (onMove(g, e) === false) { g = null; return; }
  }, { passive: false });
  const end = () => {
    if (!g) return;
    const done = g;
    g = null;
    if (done.mode) swiped = Date.now();
    onEnd(done);
  };
  el.addEventListener('touchend', end);
  el.addEventListener('touchcancel', () => { if (g) { g.cancelled = true; end(); } });
}

const springBack = (el) => {
  el.style.transition = 'transform .22s cubic-bezier(.2,.8,.2,1), opacity .22s';
  el.style.transform = '';
  el.style.opacity = '';
  setTimeout(() => { el.style.transition = ''; }, 240);
};

// Обложка улетает в сторону жеста, новая въезжает с другой
function slideTrack(cover, dir) {
  cover.style.transition = 'transform .18s ease-in, opacity .18s';
  cover.style.transform = `translateX(${dir * 110}%) rotate(${dir * 6}deg)`;
  cover.style.opacity = '0';
  setTimeout(() => {
    step(dir);
    cover.style.transition = 'none';
    cover.style.transform = `translateX(${-dir * 60}%)`;
    requestAnimationFrame(() => requestAnimationFrame(() => springBack(cover)));
  }, 180);
}

const PULL = 110;     // свернуть
const FLIP = 70;      // сменить трек

swipe($('full'), {
  canStart: (e) => !e.target.closest('input[type="range"], .st-full-seek, .st-full-lyrics, a, .st-full-volume, .st-fx'),
  onMove: (g, e) => {
    // трек листают только по обложке (как в плеерах телефона): иначе
    // перемотку назад «перехватывало» переключение трека (владелец, 30.10)
    if (g.mode === 'h' && !g.target.closest('#fCover')) return false;
    if (g.mode === 'v') {
      if (g.dy < 0 || $('full').scrollTop > 0) return false;      // листают текст -- не мешаем
      e.preventDefault();
      // тянут вниз -- едет вся шторка вместе с фоном: сверху видна Студия
      const full = $('full');
      full.style.transition = 'none';
      full.style.transform = `translateY(${g.dy}px)`;
      full.style.borderRadius = `${Math.min(22, g.dy / 4)}px ${Math.min(22, g.dy / 4)}px 0 0`;
    } else {
      if (queue.length < 2) return false;
      e.preventDefault();
      const cover = $('fCover');
      cover.style.transition = 'none';
      cover.style.transform = `translateX(${g.dx}px) rotate(${g.dx / 40}deg)`;
    }
    const armed = g.mode === 'v' ? g.dy > PULL : Math.abs(g.dx) > FLIP;
    if (armed !== Boolean(g.armed)) { g.armed = armed; if (armed) window.haptic('tick'); }
    return true;
  },
  onEnd: (g) => {
    if (g.mode === 'v') {
      if (g.armed && !g.cancelled) {
        window.haptic('light');
        fullPlayer(false);                // доезжает вниз с того места, где отпустили
      } else {
        const full = $('full');
        full.style.transition = 'transform .28s cubic-bezier(.22,1,.36,1), border-radius .28s';
        full.style.transform = 'translateY(0)';
        full.style.borderRadius = '';
        setTimeout(() => { full.style.transition = ''; full.style.transform = ''; }, 300);
      }
    } else if (g.mode === 'h') {
      const cover = $('fCover');
      if (g.armed && !g.cancelled) { window.haptic('light'); slideTrack(cover, g.dx > 0 ? 1 : -1); } else springBack(cover);
    }
  },
});

swipe($('player'), {
  canStart: (e) => !e.target.closest('.st-pprogress, .play-btn'),
  onMove: (g, e) => {
    const info = $('pOpen');
    if (g.mode === 'v') {
      if (g.dy > 0) return false;
      e.preventDefault();
      g.armed = g.dy < -40;
      return true;
    }
    if (queue.length < 2) return false;
    e.preventDefault();
    info.style.transition = 'none';
    info.style.transform = `translateX(${g.dx}px)`;
    info.style.opacity = String(Math.max(0.3, 1 - Math.abs(g.dx) / 220));
    const armed = Math.abs(g.dx) > FLIP;
    if (armed !== Boolean(g.armed)) { g.armed = armed; if (armed) window.haptic('tick'); }
    return true;
  },
  onEnd: (g) => {
    const info = $('pOpen');
    if (g.mode === 'v') { if (g.armed && !g.cancelled) { window.haptic('light'); fullPlayer(true); } return; }
    springBack(info);
    if (g.mode === 'h' && g.armed && !g.cancelled) { window.haptic('light'); step(g.dx > 0 ? 1 : -1); }
  },
});

// Строки «Моих треков»: вправо -- в избранное, влево -- удалить
const ROW_ACT = 84;
function rowSwipes(root) {
  swipe(root, {
    // с кнопок ★ и ▶ тоже: свайп влево пальцем как раз оттуда и начинают
    canStart: (e) => Boolean(e.target.closest('.st-swipe .st-row')) && !e.target.closest('input'),
    onMove: (g, e) => {
      if (g.mode !== 'h') return false;
      e.preventDefault();
      const wrap = g.target.closest('.st-swipe');
      const row = wrap.querySelector('.st-row');
      wrap.classList.add('moving');
      wrap.classList.toggle('to-fav', g.dx > 0);
      wrap.classList.toggle('to-del', g.dx < 0);
      row.style.transition = 'none';
      row.style.transform = `translateX(${g.dx}px)`;
      const armed = Math.abs(g.dx) > ROW_ACT;
      wrap.classList.toggle('armed', armed);
      if (armed !== Boolean(g.armed)) { g.armed = armed; if (armed) window.haptic('tick'); }
      return true;
    },
    onEnd: (g) => {
      const wrap = g.target.closest('.st-swipe');
      if (!wrap) return;
      const row = wrap.querySelector('.st-row');
      springBack(row);
      setTimeout(() => wrap.classList.remove('moving', 'to-fav', 'to-del', 'armed'), 230);
      if (g.mode !== 'h' || !g.armed || g.cancelled) return;
      const id = wrap.dataset.swipe;
      if (g.dx > 0) toggleFav(id);
      else setTimeout(() => act('delete', id), 240);
    },
  });
}
rowSwipes($('jobs'));

$('pPrev').addEventListener('click', () => step(-1));
$('pNext').addEventListener('click', () => step(1));
$('fPrev').addEventListener('click', () => step(-1));
$('fNext').addEventListener('click', () => step(1));
['pOpen', 'pExpand'].forEach((id) => $(id).addEventListener('click', () => fullPlayer(true)));
$('fClose').addEventListener('click', () => fullPlayer(false));
$('fLyricsBtn').addEventListener('click', () => {
  $('fLyrics').hidden = !$('fLyrics').hidden;
  $('full').classList.toggle('kk-on', !$('fLyrics').hidden && Boolean($('fLyrics')._kk));
});

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
function setVolume(percent) {
  const level = Math.max(0, Math.min(1, percent / 100));
  // звук идёт через эффекты (или iPhone) -- громкость там; иначе -- у <audio>
  if (IOS_VOLUME) window.SoundFX.ensure($('audio'));
  if (!window.SoundFX.setVolume(level) && !IOS) $('audio').volume = level;
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

// /studio#tracks (старые ссылки на «Мои треки», кнопка в кабинете): из карточки трека -- к списку
window.addEventListener('hashchange', () => {
  if (location.hash !== '#tracks') return;
  if (openJob) closeDetail(true);
  $('listView').scrollIntoView({ block: 'start' });
});

// Суфлёр в плеере: слово подсвечивается в такт (каждый кадр, пока открыт плеер)
(function karaokeFrame() {
  requestAnimationFrame(karaokeFrame);
  if ($('fLyrics')._kk && !$('full').hidden && !$('fLyrics').hidden) window.Karaoke.update($('fLyrics'), $('audio').currentTime);
}());

// ------------------------------------------------------------- плейлисты
// Свои подборки треков (владелец, 30.10: «иду по улице, заучиваю свои
// треки -- и всё время переоткрываю другой плеер»): играют подряд, по
// кругу или один трек, перемешанно; управление -- и с экрана блокировки.
// Хранятся в профиле (/api/me/playlists) -- те же на телефоне и компьютере.
let playlists = [];
let plLoaded = false;
let plOpen = '';
let plTarget = null;               // { job, file } -- что добавляем

async function loadPlaylists() {
  const r = await fetch('/api/me/playlists').catch(() => null);
  if (r && r.ok) playlists = (await r.json()).playlists || [];
  plLoaded = true;
}
async function savePlaylists() {
  const form = new FormData();
  form.append('data', JSON.stringify(playlists));
  const r = await fetch('/api/me/playlists', { method: 'POST', body: form }).catch(() => null);
  if (r && r.ok) playlists = (await r.json()).playlists;
  else toast('Не получилось сохранить плейлист');
}
loadPlaylists();

function plItems(p) {
  const out = [];
  for (const it of p.items) {
    const j = info && info.jobs.find((x) => x.id === it.job);
    const f = j && (j.files || []).find((x) => x.name === it.file);
    if (f) out.push(queueItem(j, f));
  }
  return out;
}
function favItems() {
  return ((info && info.jobs) || []).filter((j) => j.fav && !j.from && j.status === 'done' && j.files[0])
    .map((j) => queueItem(j, j.files[0]));
}
function shuffled(items) {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i -= 1) {
    const k = Math.floor(Math.random() * (i + 1));
    [a[i], a[k]] = [a[k], a[i]];
  }
  return a;
}
function playList(items, name) {
  if (!items.length) { toast('В плейлисте пока нет треков'); return; }
  setQueue(items, shuffleOn ? Math.floor(Math.random() * items.length) : 0, name);
  startTrack();
  if (window.haptic) window.haptic('light');
}

function renderPlaylists() {
  $('count').textContent = playlists.length ? String(playlists.length) : '';
  $('searchBox').hidden = true;
  $('pages').hidden = true;
  const favs = favItems();
  const card = (id, name, items, own) => {
    const minutes = Math.round(items.reduce((s, x) => s + (((info.jobs.find((j) => j.id === x.job) || {}).files || [])
      .find((f) => f.name === x.file) || {}).seconds || 0, 0) / 60);
    const art = (items[0] ? items[0].art : coverStyle(null)).replace(/"/g, "'");   // внутри style="…"
    const open = plOpen === id;
    return `<div class="st-pl${open ? ' open' : ''}">
      <div class="st-pl-head" data-pltoggle="${id}" role="button" aria-expanded="${open}">
        <span class="st-pl-art" style="background:${art.startsWith('url') ? `${art} center/cover` : art}"></span>
        <div class="st-row-main"><b data-noi18n>${esc(name)}</b>
          <span class="muted">${items.length} ${plural(items.length, 'трек', 'трека', 'треков')}${minutes ? ` · ${minutes} мин` : ''}</span></div>
        <span class="st-pl-chev" aria-hidden="true">${open ? '▴' : '▾'}</span>
        <button type="button" class="st-mode-btn${shuffleOn ? ' on' : ''}" data-plshuffle="${id}"
          aria-pressed="${shuffleOn}" aria-label="${shuffleOn ? 'Перемешать: вкл' : 'Перемешать: выкл'}">${ICON.shuffle}</button>
        <button type="button" class="play-btn" data-plplay="${id}" title="Слушать подряд">${ICON.play}</button>
      </div>
      ${open ? `<div class="st-pl-items">${items.length ? items.map((x, i) => `
        <div class="st-pl-item${playingList === name && queue[qi] && queue[qi].url === x.url ? ' now' : ''}">
          <button type="button" class="st-pl-track" data-plat="${id}:${i}" title="Слушать с этого трека">
            <span class="st-pl-n">${i + 1}</span>
            <span class="st-row-main"><b data-noi18n>${esc(x.title)}</b><span class="muted">${esc(x.sub || '')}</span></span></button>
          ${own ? `<button type="button" class="icon-btn" data-plup="${i}" title="Выше"${i ? '' : ' disabled'}>↑</button>
          <button type="button" class="icon-btn" data-pldown="${i}" title="Ниже"${i < items.length - 1 ? '' : ' disabled'}>↓</button>
          <button type="button" class="icon-btn" data-pldel="${i}" title="Убрать из плейлиста">✕</button>` : ''}</div>`).join('')
    : '<p class="muted">Пусто. Добавить трек: ⋯ у версии → «Добавить в плейлист».</p>'}
        ${own ? `<div class="st-pl-foot"><button type="button" class="st-linkbtn" data-plrename="${id}">Переименовать</button>
          <button type="button" class="st-linkbtn danger" data-plremove="${id}">Удалить плейлист</button></div>` : ''}</div>` : ''}
    </div>`;
  };
  $('jobs').innerHTML = `<div class="st-pls">
    <button type="button" class="st-pl-new" data-plnew="1">＋ Новый плейлист</button>
    ${favs.length ? card('fav', '★ Избранное подряд', favs, false) : ''}
    ${playlists.map((p) => card(p.id, p.name, plItems(p), true)).join('')}
    ${!playlists.length ? `<p class="muted">Соберите треки, которые хотите слушать подряд — например, заучить к концерту:
      ⋯ у версии → «Добавить в плейлист». Плейлист играет и с выключенным экраном, «дальше» — с наушников.</p>` : ''}
  </div>`;
}

function itemsOf(id) {
  if (id === 'fav') return { items: favItems(), name: 'Избранное' };
  const p = playlists.find((x) => x.id === id);
  return p ? { items: plItems(p), name: p.name, p } : { items: [], name: '' };
}
// перестановка/удаление -- по видимому порядку (пропавшие треки уже не видны)
function visibleIndex(p, i) {
  const shown = p.items.filter((it) => {
    const j = info.jobs.find((x) => x.id === it.job);
    return j && (j.files || []).some((f) => f.name === it.file);
  });
  return p.items.indexOf(shown[i]);
}

document.addEventListener('click', async (e) => {
  const t = (attr) => e.target.closest(`[data-${attr}]`);
  let el;
  if ((el = t('plplay'))) {
    const { items, name } = itemsOf(el.dataset.plplay);
    playList(items, name);
    return;
  }
  // «перемешать» -- переключатель, как в плеере: горит -- включено (для всех
  // плейлистов); играет этот плейлист -- очередь перестраивается сразу
  if ((el = t('plshuffle'))) {
    setShuffle(!shuffleOn);
    renderPlaylists();
    return;
  }
  if ((el = t('plat'))) {                 // нажали на трек -- слушать плейлист с него
    const [id, at] = el.dataset.plat.split(':');
    const { items, name } = itemsOf(id);
    setQueue(items, Math.min(Number(at), items.length - 1), name);
    startTrack();
    renderPlaylists();
    return;
  }
  // нажали на карточку (не на кнопку) -- раскрыть/свернуть треки
  if ((el = t('pltoggle')) && !e.target.closest('button')) {
    plOpen = plOpen === el.dataset.pltoggle ? '' : el.dataset.pltoggle;
    renderPlaylists();
    return;
  }
  if (t('plnew')) {
    const name = prompt('Название плейлиста', 'Заучить к концерту');
    if (!name || !name.trim()) return;
    playlists.push({ id: Math.random().toString(36).slice(2, 10), name: name.trim(), items: [] });
    await savePlaylists();
    plOpen = playlists[playlists.length - 1].id;
    renderPlaylists();
    return;
  }
  const p = playlists.find((x) => x.id === plOpen);
  if (p && ((el = t('plup')) || (el = t('pldown')) || (el = t('pldel')))) {
    const at = visibleIndex(p, Number(el.dataset.plup || el.dataset.pldown || el.dataset.pldel));
    if (el.dataset.pldel != null) p.items.splice(at, 1);
    else {
      const to = visibleIndex(p, Number(el.dataset.plup || el.dataset.pldown) + (el.dataset.plup != null ? -1 : 1));
      [p.items[at], p.items[to]] = [p.items[to], p.items[at]];
    }
    renderPlaylists();
    await savePlaylists();
    return;
  }
  if ((el = t('plrename'))) {
    const pl = playlists.find((x) => x.id === el.dataset.plrename);
    const name = pl && prompt('Новое название', pl.name);
    if (!name || !name.trim()) return;
    pl.name = name.trim();
    await savePlaylists();
    renderPlaylists();
    return;
  }
  if ((el = t('plremove'))) {
    const pl = playlists.find((x) => x.id === el.dataset.plremove);
    if (!pl || !confirm(`Удалить плейлист «${pl.name}»? Сами треки останутся.`)) return;
    playlists = playlists.filter((x) => x !== pl);
    plOpen = '';
    await savePlaylists();
    renderPlaylists();
    return;
  }
  if ((el = t('pladd'))) {
    const pl = playlists.find((x) => x.id === el.dataset.pladd);
    if (pl && plTarget) await addTo(pl);
  }
});

async function addTo(pl) {
  if (!pl.items.some((x) => x.job === plTarget.job && x.file === plTarget.file)) pl.items.push(plTarget);
  $('plModal').hidden = true;
  await savePlaylists();
  toast(`Добавлено в «${pl.name}» 🎧`);
  if (list.lists) renderPlaylists();
}

async function addToPlaylist(job, file) {
  if (!plLoaded) await loadPlaylists();
  const j = info.jobs.find((x) => x.id === job);
  const f = j && (j.files || []).find((x) => x.name === file);
  if (!f) return;
  plTarget = { job, file };
  $('plWhat').textContent = `${(queueItem(j, f)).title} — ${f.label}`;
  $('plPick').innerHTML = playlists.map((p) => `<button type="button" class="st-pl-choice" data-pladd="${p.id}">
    <b data-noi18n>${esc(p.name)}</b><small>${p.items.length} ${plural(p.items.length, 'трек', 'трека', 'треков')}</small></button>`).join('')
    || '<p class="muted">Плейлистов пока нет — создайте первый.</p>';
  $('plNew').value = '';
  $('plModal').hidden = false;
}
$('plCancel').addEventListener('click', () => { $('plModal').hidden = true; });
$('plModal').addEventListener('click', (e) => { if (e.target === $('plModal')) $('plModal').hidden = true; });
$('plCreate').addEventListener('click', async () => {
  const name = $('plNew').value.trim() || 'Мой плейлист';
  const pl = { id: Math.random().toString(36).slice(2, 10), name, items: [] };
  playlists.push(pl);
  await addTo(playlists[playlists.length - 1]);
});
$('plNew').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('plCreate').click(); });

// Повтор, перемешать, в плейлист, звук -- значками в полноэкранном плеере;
// что включилось -- мягкой подсказкой посередине (как в плеерах телефона)
let hudTimer = null;
function hud(icon, title, text) {
  $('hudIcon').innerHTML = icon;
  $('hudTitle').textContent = title;
  $('hudText').textContent = text;
  $('hud').classList.add('on');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('hud').classList.remove('on'), 1800);
  if (window.haptic) window.haptic('light');
}
const REPEAT_HUD = {
  off: ['Повтор: выкл', 'Воспроизведение остановится, когда очередь закончится.'],
  all: ['Повтор: все', 'Очередь будет играть по кругу.'],
  one: ['Повтор: один', 'Текущий трек будет повторяться — удобно заучивать.'],
};
function modeButtons() {
  $('fRepeat').innerHTML = repeatMode === 'one' ? ICON.repeatOne : ICON.repeat;
  $('fRepeat').classList.toggle('on', repeatMode !== 'off');
  $('fRepeat').setAttribute('aria-label', (REPEAT_HUD[repeatMode] || REPEAT_HUD.off)[0]);
  $('fShuffle').innerHTML = ICON.shuffle;
  $('fShuffle').classList.toggle('on', shuffleOn);
  $('fShuffle').setAttribute('aria-pressed', String(shuffleOn));
  $('fAddPl').innerHTML = ICON.addList;
  $('fFx').innerHTML = ICON.sliders;
  $('fShare').innerHTML = ICON.share;
  const item = queue[qi];
  $('fAddPl').hidden = !(item && item.job);
  $('fShare').hidden = !(item && item.job);
  $('fFx').classList.toggle('on', window.SoundFX.on);
}
function setShuffle(on, announce = true) {
  shuffleOn = on;
  try { localStorage.setItem('naslux.shuffle', on ? '1' : '0'); } catch (error) { /* */ }
  const now = queue[qi];
  if (now && baseQueue.length > 1) {
    if (on) { queue = [now, ...shuffled(baseQueue.filter((x) => x !== now))]; qi = 0; } else {
      queue = baseQueue;
      qi = Math.max(0, queue.indexOf(now));
    }
    $('fQueue').textContent = playingList ? `${playingList} · ${qi + 1} из ${queue.length}` : '';
  }
  modeButtons();
  if (announce) {
    hud(ICON.shuffle, on ? 'Перемешать: вкл' : 'Перемешать: выкл',
      on ? 'Треки играют в случайном порядке.' : 'Треки играют по порядку очереди.');
  }
}
$('fRepeat').addEventListener('click', () => {
  repeatMode = { off: 'all', all: 'one', one: 'off' }[repeatMode] || 'all';
  try { localStorage.setItem('naslux.repeat', repeatMode); } catch (error) { /* */ }
  modeButtons();
  hud(repeatMode === 'one' ? ICON.repeatOne : ICON.repeat, ...REPEAT_HUD[repeatMode]);
});
$('fShuffle').addEventListener('click', () => setShuffle(!shuffleOn));
$('fAddPl').addEventListener('click', () => { const item = queue[qi]; if (item && item.job) addToPlaylist(item.job, item.file); });
window.SoundFX.attach($('audio'));
window.SoundFX.bind($('fxPanel'));
$('fFx').addEventListener('click', () => {
  $('fxPanel').hidden = !$('fxPanel').hidden;
  if (!$('fxPanel').hidden) $('fxPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
});
$('fxPanel').addEventListener('click', () => setTimeout(modeButtons));
$('audio').addEventListener('play', modeButtons);
modeButtons();

// Перемотка пальцем в полноэкранном плеере (владелец, 30.10: «назад очень
// сложно -- переключается трек»): вся полоса с подписями времени -- одна
// широкая зона. Коснулись где угодно -- бегунок сразу там, ведёте --
// перематывается; переключение трека сюда не дотягивается.
function scrubTo(x) {
  const box = $('fSeek').getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (x - box.left) / box.width));
  $('fSeek').value = Math.round(frac * 1000);
  const audio = $('audio');
  if (audio.duration) $('fCur').textContent = time(frac * audio.duration) || '0:00';
  return frac;
}
$('fSeekZone').addEventListener('touchstart', (e) => {
  if (e.touches.length !== 1) return;
  e.preventDefault();                       // не прокручивать и не тянуть плеер
  fScrub = { frac: scrubTo(e.touches[0].clientX) };
  $('fSeekZone').classList.add('scrubbing');
  if (window.haptic) window.haptic('tick');
}, { passive: false });
$('fSeekZone').addEventListener('touchmove', (e) => {
  if (!fScrub) return;
  e.preventDefault();
  fScrub.frac = scrubTo(e.touches[0].clientX);
}, { passive: false });
const scrubEnd = () => {
  if (!fScrub) return;
  const audio = $('audio');
  if (audio.duration) audio.currentTime = fScrub.frac * audio.duration;
  fScrub = null;
  $('fSeekZone').classList.remove('scrubbing');
};
$('fSeekZone').addEventListener('touchend', scrubEnd);
$('fSeekZone').addEventListener('touchcancel', scrubEnd);

// Поделиться треком из плеера (владелец, 30.10: «в плеере не могу
// поделиться»): ссылка «послушать без входа» -- в мессенджер или
// скопировать; или сам mp3 (со свойствами и обложкой). Ссылка и файл
// готовятся сразу при открытии окна, а отправка -- по нажатию: так
// системное меню телефона открывается наверняка.
let shareJob = null;
function closeShare() {
  shareJob = null;
  $('shareSheet').classList.remove('on');
  setTimeout(() => { if (!shareJob) $('shareSheet').hidden = true; }, 220);
}
async function openShare() {
  const item = queue[qi];
  if (!item || !item.job) return;
  const mine = { item, url: '', file: null };
  shareJob = mine;
  $('shIcon').innerHTML = ICON.share;
  $('shName').textContent = `${item.title}${item.sub ? ` — ${item.sub}` : ''}`;
  $('shInfo').textContent = 'Готовим ссылку…';
  ['shSend', 'shCopy'].forEach((id) => { $(id).disabled = true; });
  $('shFile').hidden = true;
  $('shareSheet').hidden = false;
  requestAnimationFrame(() => $('shareSheet').classList.add('on'));
  const form = new FormData();
  form.append('file', item.file);
  const r = await fetch(`/api/studio/${item.job}/share`, { method: 'POST', body: form }).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  if (shareJob !== mine) return;
  if (!r || !r.ok) { $('shInfo').textContent = data.detail || 'Не получилось сделать ссылку'; return; }
  mine.url = `${location.origin}${data.path}`;
  mine.token = data.token;
  $('shInfo').innerHTML = `Послушать можно без входа${data.plays ? ` · прослушиваний: ${data.plays}` : ''}
    · <button type="button" class="st-linkbtn" id="shStop">закрыть доступ</button>`;
  ['shSend', 'shCopy'].forEach((id) => { $(id).disabled = false; });
  // сам файл -- в мессенджер, если телефон умеет делиться файлами
  if (navigator.canShare) {
    const f = await fetch(withTags(item.url)).then((x) => (x.ok ? x.blob() : null)).catch(() => null);
    if (shareJob !== mine || !f) return;
    const name = `${item.title}${item.sub ? ` — ${item.sub}` : ''}.mp3`.replace(/[\\/:*?"<>|]/g, '_');
    const file = new File([f], name, { type: 'audio/mpeg' });
    if (navigator.canShare({ files: [file] })) {
      mine.file = file;
      $('shFile').textContent = `Отправить файл mp3 · ${(f.size / 1048576).toFixed(1).replace('.', ',')} МБ`;
      $('shFile').hidden = false;
    }
  }
}
$('fShare').addEventListener('click', openShare);
$('shBack').addEventListener('click', closeShare);
$('shareSheet').addEventListener('click', async (e) => {
  if (e.target === $('shareSheet')) { closeShare(); return; }
  if (e.target.id === 'shStop' && shareJob && shareJob.token) {
    const r = await fetch(`/api/share/${encodeURIComponent(shareJob.token)}`, { method: 'DELETE' }).catch(() => null);
    toast(r && r.ok ? 'Доступ по ссылке закрыт' : 'Не получилось закрыть доступ');
    closeShare();
  }
});
$('shSend').addEventListener('click', async () => {
  const job = shareJob;
  if (!job || !job.url) return;
  try {
    if (navigator.share) {
      await navigator.share({ title: job.item.title, text: `${job.item.title} — послушать в NASLUX`, url: job.url });
      closeShare();
      return;
    }
  } catch (error) {
    if (error && error.name === 'AbortError') return;        // закрыли меню -- окно остаётся
  }
  $('shCopy').click();
});
$('shCopy').addEventListener('click', async () => {
  const job = shareJob;
  if (!job || !job.url) return;
  toast(await copyText(job.url) ? 'Ссылка скопирована — вставьте её в сообщение' : 'Не получилось скопировать');
  closeShare();
});
$('shFile').addEventListener('click', async () => {
  const job = shareJob;
  if (!job || !job.file) return;
  try {
    await navigator.share({ files: [job.file], title: job.file.name });
    closeShare();
  } catch (error) {
    if (!error || error.name !== 'AbortError') toast('Не получилось отправить файл');
  }
});

// ------------------------------------------------ разделы сайта под музыку
// Владелец, 30.10: «переключаю меню -- плеер пропадает и музыка обрывается,
// а я хотел полазить по сайту под музыку». Каждый раздел -- отдельная
// страница, переход перезагружает её вместе с плеером. Поэтому, пока играет
// музыка, раздел открывается поверх Студии (тот же сайт во фрейме), а плеер
// внизу продолжает играть. «Студия» в меню раздела -- обратно сюда
// (nav.js во фрейме сообщает об этом). Мультитрек, плеер табов, вход через
// Яндекс/VK и оплата -- обычным переходом: у них свой звук или чужой сайт.
window.NASLUX_HOST = true;
const BROWSE_SKIP = /^\/(studio\/mix|player\/|auth|oauth|api\/|media\/|static\/|logout|listen\/|take\/)/;
function musicOn() { return !$('player').hidden && !$('audio').paused; }
function openBrowse(href) {
  $('browseFrame').src = href;
  $('browse').hidden = false;
  document.body.classList.add('browsing');
  playerSpace();
}
function closeBrowse() {
  if ($('browse').hidden) return;
  $('browse').hidden = true;
  $('browseFrame').src = 'about:blank';
  document.body.classList.remove('browsing');
}
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest('a[href]');
  if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || url.pathname === '/studio' || BROWSE_SKIP.test(url.pathname)) return;
  if (!musicOn()) return;
  e.preventDefault();
  openBrowse(url.pathname + url.search + url.hash);
});
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || !e.data || e.data.naslux !== 'studio') return;
  const url = new URL(e.data.href, location.href);
  if (url.pathname !== '/studio') { location.href = url.href; return; }      // мультитрек и т.п.
  closeBrowse();
  if (url.hash && url.hash !== location.hash) location.hash = url.hash;
  load();                       // во фрейме могли войти, оплатить, пополнить
});
