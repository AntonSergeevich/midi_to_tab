// Шапка на любой странице: кто вошёл и что на счету. Одна надпись для
// всех страниц -- раньше каждая страница писала своё («Владелец · безлимит
// · 50 ₽» на главной, «Безлимит» в Студии), и меню прыгало при переходах.
// Последняя надпись хранится в браузере: следующая страница рисует её
// сразу, ещё до ответа сервера, -- без «загрузка…» и сдвигов.
// Тактильный отклик -- как на iPhone: взял дорожку, сработал свайп,
// удалил -- палец чувствует. Работает библиотека vibration (MIT,
// static/vibration.js): Android -- navigator.vibrate; iPhone -- «тик»
// системного переключателя. С iOS 26.5 он звучит только от настоящего
// касания: библиотека подкладывает переключатель под палец, поэтому на
// iPhone отклик -- в обработчике нажатия (click), а не из таймера.
// Отключить: localStorage 'naslux.haptics' = 'off'.
window.haptic = (() => {
  const V = window.NasluxVibration;
  const PRESET = { tick: 'selection', light: 'light', pick: 'medium', drop: 'light', medium: 'medium',
    success: 'success', warn: 'warning' };
  let last = 0;
  const off = () => { try { return localStorage.getItem('naslux.haptics') === 'off'; } catch (error) { return false; } };
  return (kind = 'light') => {
    if (off()) return;
    const now = Date.now();
    if (kind === 'tick' && now - last < 40) return;     // частые «тики» не сливаются в гул
    last = now;
    try {
      if (V && V.engine !== 'none') V.haptic(PRESET[kind] || 'light');
      else if (navigator.vibrate) navigator.vibrate(10);
    } catch (error) { /* без отклика -- не страшно */ }
  };
})();

// Ползунки «щёлкают» на делениях -- как колёсико выбора на iPhone: на
// каждом шаге (крупный ползунок -- на каждой двадцатой части) лёгкий тик,
// на краях -- чуть сильнее. Перемотка трека (data-nohaptic) -- без тиков,
// иначе телефон дрожал бы без остановки.
//
// На iPhone тик во время перетаскивания возможен, только если палец ведёт
// по невидимому переключателю (vibration.hapticDrag). Поэтому там ползунок
// оборачивается такой «поверхностью», а значение ставим сами -- по
// положению пальца (сам ползунок касаний под поверхностью не получает).
(() => {
  const V = window.NasluxVibration;
  const slot = (el) => {
    const min = Number(el.min || 0);
    const max = Number(el.max || 100);
    const span = max - min;
    if (!(span > 0)) return null;
    const step = el.step === 'any' ? 0 : Number(el.step || 1);
    const detent = Math.max(step || 0, span / 20);
    return Math.round((Number(el.value) - min) / detent);
  };
  const ranged = (el) => el && el.type === 'range' && !('nohaptic' in el.dataset);
  const remember = (e) => { if (ranged(e.target)) e.target.hapticSlot = slot(e.target); };
  document.addEventListener('pointerdown', remember, true);
  document.addEventListener('focusin', remember, true);
  const detent = (el) => {
    const now = slot(el);
    if (now === null || now === el.hapticSlot) return;
    el.hapticSlot = now;
    const v = Number(el.value);
    window.haptic(v <= Number(el.min || 0) || v >= Number(el.max || 100) ? 'light' : 'tick');
  };
  document.addEventListener('input', (e) => {
    if (ranged(e.target) && !e.target.hapticDriving) detent(e.target);
  }, true);

  const ios = V && (V.engine === 'ios-relay' || V.engine === 'ios-switch');
  if (!ios) return;
  const wrapped = new WeakSet();
  const valueAt = (el, x) => {
    const box = el.getBoundingClientRect();
    const min = Number(el.min || 0);
    const max = Number(el.max || 100);
    const step = el.step === 'any' ? 0 : Number(el.step || 1);
    const frac = Math.max(0, Math.min(1, (x - box.left) / (box.width || 1)));
    let v = min + frac * (max - min);
    if (step) v = Math.round((v - min) / step) * step + min;
    return Math.max(min, Math.min(max, Number(v.toFixed(6))));
  };
  const wrap = (el) => {
    if (wrapped.has(el) || !ranged(el) || !el.isConnected || el.disabled) return;
    wrapped.add(el);
    // раскладку ползунка переносим на обёртку. Ширину читаем у скрытого
    // элемента -- так видно заданное в CSS («100%», «auto»), а не пиксели
    const shown = getComputedStyle(el);
    const display = shown.display;
    const prev = el.style.display;
    el.style.display = 'none';
    const cs = getComputedStyle(el);
    const lay = { width: cs.width, minWidth: cs.minWidth, maxWidth: cs.maxWidth, flex: `${cs.flexGrow} ${cs.flexShrink} ${cs.flexBasis}`,
      alignSelf: cs.alignSelf, margin: `${cs.marginTop} ${cs.marginRight} ${cs.marginBottom} ${cs.marginLeft}`, order: cs.order };
    el.style.display = prev;
    const box = document.createElement('span');
    box.className = 'hv-wrap';
    box.style.cssText = `position:relative;display:${display === 'block' ? 'block' : 'inline-block'};vertical-align:middle;`
      + `flex:${lay.flex};align-self:${lay.alignSelf};order:${lay.order};margin:${lay.margin};`
      + `min-width:${lay.minWidth};max-width:${lay.maxWidth};` + (lay.width !== 'auto' ? `width:${lay.width};` : '');
    el.parentNode.insertBefore(box, el);
    box.appendChild(el);
    el.style.margin = '0';
    el.style.display = 'block';
    if (lay.width !== 'auto' || Number(cs.flexGrow) > 0 || display === 'block') el.style.width = '100%';
    el.style.minWidth = '0';
    V.hapticDrag(box);
    const set = (x) => {
      const v = valueAt(el, x);
      if (String(v) === el.value) return;
      el.value = String(v);
      el.hapticDriving = true;
      try { el.dispatchEvent(new Event('input', { bubbles: true })); } finally { el.hapticDriving = false; }
      // «тик» -- прямо в обработчике движения пальца: библиотека узнаёт
      // его по событию touchmove, из вложенного input он запоздал бы на шаг
      detent(el);
    };
    // поверхность запрещает браузеру прокрутку (touch-action:none), поэтому
    // сами различаем жест: вбок -- двигаем ползунок, вверх-вниз -- листаем
    // страницу, лёгкое касание -- ставим значение, куда ткнули
    let gesture = null;
    box.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      el.hapticSlot = slot(el);
      gesture = { x: t.clientX, y: t.clientY, lastY: t.clientY, mode: '' };
    }, { passive: true });
    box.addEventListener('touchmove', (e) => {
      if (!gesture) return;
      const t = e.touches[0];
      if (!gesture.mode) {
        const dx = Math.abs(t.clientX - gesture.x);
        const dy = Math.abs(t.clientY - gesture.y);
        if (dx < 6 && dy < 6) return;
        gesture.mode = dx >= dy ? 'slide' : 'scroll';
      }
      if (gesture.mode === 'slide') set(t.clientX);
      else window.scrollBy(0, gesture.lastY - t.clientY);
      gesture.lastY = t.clientY;
    }, { passive: true });
    const finish = (e) => {
      if (!gesture) return;
      const { mode, x } = gesture;
      gesture = null;
      if (e.type === 'touchcancel' || mode === 'scroll') return;
      if (!mode) set(x);
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    box.addEventListener('touchend', finish);
    box.addEventListener('touchcancel', finish);
  };
  const scan = (root) => {
    if (root.querySelectorAll) root.querySelectorAll('input[type="range"]').forEach(wrap);
  };
  const start = () => {
    scan(document);
    // ползунки, которые страница дорисовывает позже (дорожки мультитрека, окна)
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.nodeType !== 1) return;
      if (n.matches && n.matches('input[type="range"]')) wrap(n); else scan(n);
    }))).observe(document.documentElement, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

(() => {
  const CACHE = 'naslux.nav';
  const rub = (n) => `${Math.round(n || 0)} ₽`;

  // Ширина окна без полосы прокрутки -- по ней шапка одной ширины везде (app.css)
  const measure = () => document.documentElement.style.setProperty(
    '--page', `${document.documentElement.clientWidth}px`);
  measure();
  window.addEventListener('resize', measure);

  function badgeOf(me) {
    const money = me.balance > 0 ? ` · ${rub(me.balance)}` : '';
    if (me.unlimited) return { text: `Безлимит${money}`, pro: true };
    if (me.subscribed) return { text: `Подписка${money}`, pro: true };
    if (me.balance > 0) return { text: `Баланс: ${rub(me.balance)}`, pro: true };
    if (me.studioCredits > 0) return { text: `Кредитов: ${me.studioCredits}`, pro: true };
    if (me.credits > 0) return { text: `Оплачено треков: ${me.credits}`, pro: true };
    if (me.freeSongs) return { text: `Бесплатно: ${me.freeLeft ?? 0} из ${me.freeSongs}`, pro: false };
    return { text: 'Баланс: 0 ₽', pro: false };
  }

  function paint(state) {
    const account = document.getElementById('account');
    if (account && state.email) {
      account.textContent = state.email;
      account.title = state.email;
    } else if (account && state.email === '') {
      account.textContent = 'Вход';
    }
    document.querySelectorAll('header.top .badge').forEach((badge) => {
      badge.textContent = state.text;
      badge.classList.toggle('pro', Boolean(state.pro));
      badge.hidden = false;
    });
  }

  // Язык сайта: RU / EN (i18n.js)
  const nav = document.querySelector('header.top .topnav');
  if (nav && window.setLang && !nav.querySelector('.langsw')) {
    const en = window.NASLUX_LANG === 'en';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'langsw';
    button.dataset.noi18n = '';
    button.textContent = en ? 'RU' : 'EN';
    button.title = en ? 'Русский язык' : 'English';
    button.setAttribute('aria-label', button.title);
    button.addEventListener('click', () => window.setLang(en ? 'ru' : 'en'));
    nav.insertBefore(button, nav.querySelector('#account'));
  }

  try {
    const cached = JSON.parse(localStorage.getItem(CACHE) || 'null');
    if (cached) paint(cached);
  } catch (error) { /* приватный режим -- просто без памяти */ }

  // Страницы зовут после оплаты или списания: надпись обновляется везде одинаково
  window.refreshNav = async (me) => {
    try {
      const data = me || await (await fetch('/api/me')).json();
      const state = { ...badgeOf(data), email: data.registered ? data.email : '' };
      paint(state);
      if (data.whatsNew) whatsNew(data.whatsNew);
      try { localStorage.setItem(CACHE, JSON.stringify(state)); } catch (error) { /* без памяти */ }
    } catch (error) {
      // Шапка -- не повод ломать страницу
    }
  };
  // ---------------------------------------------- «Что нового» (как в «Твёрдом знаке»)
  // Один раз после обновления: что изменилось и где теперь, не больше пяти
  // пунктов. Закрыли любым способом -- сервер запоминает версию, и окно не
  // всплывёт ни здесь, ни на другом устройстве. Подробности -- /updates.
  let told = false;
  function whatsNew(info) {
    if (told || !info || !info.changes || !info.changes.length) return;
    if (/^\/(updates|admin|offer|privacy)/.test(location.pathname)) return;
    told = true;
    const account = document.getElementById('account');
    if (account && !account.querySelector('.nav-dot')) {
      const dot = document.createElement('span');
      dot.className = 'nav-dot';
      dot.title = 'Есть обновления';
      account.appendChild(dot);
    }
    const esc = (t) => String(t || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const dialog = document.createElement('dialog');
    dialog.className = 'whats-new';
    dialog.setAttribute('aria-labelledby', 'whatsNewTitle');
    dialog.innerHTML = `<p class="whats-new-kicker">${esc(info.label)}</p>
      <h2 class="whats-new-title" id="whatsNewTitle">${esc(info.title)}</h2>
      <ul class="whats-new-list">${info.changes.map((c) => `<li><b>${esc(c.title)}</b>`
        + `${c.where ? `<span><i>Где:</i> ${esc(c.where)}</span>` : ''}</li>`).join('')}</ul>
      ${info.more ? `<p class="whats-new-more"><span>И ещё</span> ${info.more} <span>— на странице обновлений</span></p>` : ''}
      <div class="whats-new-actions">
        <button type="button" class="primary" data-close autofocus>Понятно</button>
        <a href="/updates" data-more>Подробнее</a>
      </div>`;
    document.body.appendChild(dialog);
    let seen = false;
    const markSeen = () => {
      if (seen) return;
      seen = true;
      // sendBeacon долетает, даже если человек в ту же секунду ушёл со страницы
      if (!(navigator.sendBeacon && navigator.sendBeacon('/api/updates/seen'))) {
        fetch('/api/updates/seen', { method: 'POST', keepalive: true }).catch(() => {});
      }
      document.querySelectorAll('.nav-dot').forEach((d) => d.remove());
    };
    dialog.addEventListener('close', markSeen);
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog || e.target.closest('[data-close]')) dialog.close();
      if (e.target.closest('[data-more]')) markSeen();
    });
    if (typeof dialog.showModal !== 'function') return;
    // Не поверх только что открывшейся страницы, а через мгновение после
    setTimeout(() => { if (!dialog.open && !document.querySelector('dialog[open], .mx-sheet:not([hidden]), .mx-rec.on')) dialog.showModal(); }, 700);
  }

  window.refreshNav();

  // ------------------------------------------- приложение и без интернета
  // Сайт ставится как приложение (Android, Windows, Mac -- кнопка «Установить»;
  // iPhone -- «Поделиться → На экран „Домой“») и работает без интернета: то,
  // что открывали при интернете, хранится на устройстве (sw.js)
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
      const reg = await navigator.serviceWorker.register('/sw.js').catch(() => null);
      // Студию сохраняем сразу, с первого же захода: без интернета в ней
      // откроется список треков, а из него -- сохранённые треки
      if (!reg || !/^\/studio\/?$/.test(location.pathname) || !navigator.onLine) return;
      const ready = await navigator.serviceWorker.ready.catch(() => null);
      const worker = ready && ready.active;
      if (!worker) return;
      setTimeout(() => worker.postMessage({ type: 'keep', urls: [location.pathname, '/api/studio', '/api/me',
        ...performance.getEntriesByType('resource').map((r) => r.name)
          .filter((u) => u.startsWith(`${location.origin}/static/`))] }), 1500);
    });
  }
  let installEvent = null;
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installEvent = event;
    if (!nav || nav.querySelector('.appinstall')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'langsw appinstall';
    button.textContent = 'Установить';
    button.title = 'Установить NASLUX как приложение: значок на экране, работа без интернета';
    button.addEventListener('click', async () => {
      if (!installEvent) return;
      installEvent.prompt();
      await installEvent.userChoice.catch(() => null);
      installEvent = null;
      button.remove();
    });
    nav.insertBefore(button, nav.querySelector('.langsw') || nav.querySelector('#account'));
  });
  window.addEventListener('appinstalled', () => {
    const button = nav && nav.querySelector('.appinstall');
    if (button) button.remove();
  });

  // Пропал интернет -- сказать, что работает дальше
  function offlineNote() {
    let note = document.getElementById('offlineNote');
    if (navigator.onLine) { if (note) note.remove(); return; }
    if (note) return;
    note = document.createElement('div');
    note.id = 'offlineNote';
    note.className = 'offline-note';
    note.textContent = 'Нет интернета — сохранённые треки и мультитрек работают';
    document.body.appendChild(note);
  }
  window.addEventListener('online', offlineNote);
  window.addEventListener('offline', offlineNote);
  if (document.body) offlineNote(); else document.addEventListener('DOMContentLoaded', offlineNote);
})();

// Скачивание на iPhone (владелец, 30.10: «нажал скачать и не могу вернуться
// назад»). В приложении с экрана «Домой» у iPhone нет кнопки «назад», и
// ссылка на файл открывала его просмотр, из которого не выйти. Теперь файл
// готовится тут же, а «Сохранить» открывает системное меню («Сохранить в
// Файлы», другое приложение, AirDrop); «Назад» -- остаться на странице.
(() => {
  const IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (!IOS) { window.saveFile = null; return; }

  let sheet = null;
  let job = null;
  const text = (s) => (window.__t ? window.__t(s) : s);
  function ensureSheet() {
    if (sheet) return sheet;
    sheet = document.createElement('div');
    sheet.className = 'dl-sheet';
    sheet.hidden = true;
    sheet.innerHTML = `<div class="dl-box" role="dialog" aria-modal="true">
      <div class="dl-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/></svg></div>
      <b class="dl-name" data-noi18n></b><small class="dl-info"></small>
      <button type="button" class="dl-save" disabled></button>
      <button type="button" class="dl-back"></button></div>`;
    document.body.appendChild(sheet);
    sheet.querySelector('.dl-back').textContent = text('Назад');
    sheet.querySelector('.dl-back').addEventListener('click', close);
    sheet.addEventListener('click', (e) => { if (e.target === sheet) close(); });
    sheet.querySelector('.dl-save').addEventListener('click', save);
    return sheet;
  }
  function close() {
    if (job && job.abort) job.abort.abort();
    job = null;
    if (sheet) sheet.classList.remove('on');
    setTimeout(() => { if (sheet && !job) sheet.hidden = true; }, 220);
  }
  const mb = (n) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0).replace('.', ',')} МБ`;
  function ready(blob, name) {
    job.file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
    sheet.querySelector('.dl-info').textContent = mb(blob.size);
    const button = sheet.querySelector('.dl-save');
    button.disabled = false;
    button.textContent = text('Сохранить');
  }
  function open(name) {
    ensureSheet();
    job = { abort: null, file: null };
    sheet.querySelector('.dl-name').textContent = name || text('Файл');
    sheet.querySelector('.dl-info').textContent = text('Готовим файл…');
    const button = sheet.querySelector('.dl-save');
    button.disabled = true;
    button.textContent = text('Готовим файл…');
    sheet.hidden = false;
    requestAnimationFrame(() => sheet.classList.add('on'));
  }
  async function save() {
    if (!job || !job.file) return;
    const file = job.file;
    try {
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: file.name });
        close();
        return;
      }
    } catch (error) {
      if (error && error.name === 'AbortError') return;       // закрыли меню -- остаёмся в окне
    }
    // нет системного меню -- обычное скачивание файла из памяти: страница не уходит
    const a = document.createElement('a');
    a.href = URL.createObjectURL(file);
    a.download = file.name;
    a.dataset.direct = '1';                 // не перехватывать самим собой
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    close();
  }
  function nameFrom(response, fallback) {
    const header = response.headers.get('content-disposition') || '';
    const star = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) { try { return decodeURIComponent(star[1]); } catch (error) { /* */ } }
    const plain = header.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : fallback;
  }

  // готовый файл в памяти (сведённый микс, ноты) -- то же окно
  window.saveFile = (blob, name) => { open(name); ready(blob, name); };

  document.addEventListener('click', async (e) => {
    const link = e.target.closest('a[download]');
    if (!link || !link.href || e.defaultPrevented || link.dataset.direct) return;
    e.preventDefault();
    const fallback = link.getAttribute('download') || decodeURIComponent(link.pathname.split('/').pop() || 'file');
    open(link.getAttribute('download') || '');
    const mine = job;
    mine.abort = new AbortController();
    try {
      const response = await fetch(link.href, { signal: mine.abort.signal, credentials: 'same-origin' });
      if (!response.ok) throw new Error(String(response.status));
      const name = nameFrom(response, fallback);
      const blob = await response.blob();
      if (job !== mine) return;
      sheet.querySelector('.dl-name').textContent = name;
      ready(blob, name);
    } catch (error) {
      if (job !== mine || (error && error.name === 'AbortError')) return;
      sheet.querySelector('.dl-info').textContent = text('Не получилось скачать — проверьте интернет');
      sheet.querySelector('.dl-save').textContent = text('Не получилось');
    }
  });
})();
