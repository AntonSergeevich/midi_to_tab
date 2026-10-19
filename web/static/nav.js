// Шапка на любой странице: кто вошёл и что на счету. Одна надпись для
// всех страниц -- раньше каждая страница писала своё («Владелец · безлимит
// · 50 ₽» на главной, «Безлимит» в Студии), и меню прыгало при переходах.
// Последняя надпись хранится в браузере: следующая страница рисует её
// сразу, ещё до ответа сервера, -- без «загрузка…» и сдвигов.
// Тактильный отклик -- как на iPhone: взял дорожку, сработал свайп,
// удалил -- палец чувствует. Android и Chrome -- navigator.vibrate. На
// iPhone (Safari 18+) вибрации из страницы нет, но системный переключатель
// <input switch> «щёлкает» Taptic Engine -- им и пользуемся. Отключить:
// localStorage 'naslux.haptics' = 'off'.
window.haptic = (() => {
  const PATTERN = { tick: 6, light: 10, pick: 18, drop: 12, medium: 22, success: [12, 40, 18], warn: [30, 50, 30] };
  const ios = /iP(hone|ad|od)/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let label = null;
  let last = 0;
  const off = () => { try { return localStorage.getItem('naslux.haptics') === 'off'; } catch (error) { return false; } };
  return (kind = 'light') => {
    if (off()) return;
    const now = Date.now();
    if (kind === 'tick' && now - last < 40) return;     // частые «тики» не сливаются в гул
    last = now;
    try {
      if (navigator.vibrate && !ios) { navigator.vibrate(PATTERN[kind] || 10); return; }
      if (!ios) return;
      if (!label) {
        label = document.createElement('label');
        label.setAttribute('aria-hidden', 'true');
        label.style.cssText = 'position:fixed;left:-200px;top:0;width:1px;height:1px;opacity:0;pointer-events:none';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.setAttribute('switch', '');
        input.tabIndex = -1;
        label.appendChild(input);
        // щелчок по скрытому переключателю -- не «щелчок по странице»: меню не закрываются
        label.addEventListener('click', (e) => e.stopPropagation());
        document.body.appendChild(label);
      }
      label.click();
    } catch (error) { /* без отклика -- не страшно */ }
  };
})();

// Ползунки «щёлкают» на делениях -- как колёсико выбора на iPhone: на
// каждом шаге (крупный ползунок -- на каждой двадцатой части) лёгкий тик,
// на краях -- чуть сильнее. Перемотка трека (data-nohaptic) -- без тиков,
// иначе телефон дрожал бы без остановки.
(() => {
  const slot = (el) => {
    const min = Number(el.min || 0);
    const max = Number(el.max || 100);
    const span = max - min;
    if (!(span > 0)) return null;
    const step = el.step === 'any' ? 0 : Number(el.step || 1);
    const detent = Math.max(step || 0, span / 20);
    return Math.round((Number(el.value) - min) / detent);
  };
  const ranged = (e) => e.target && e.target.type === 'range' && !('nohaptic' in e.target.dataset);
  const remember = (e) => { if (ranged(e)) e.target.hapticSlot = slot(e.target); };
  document.addEventListener('pointerdown', remember, true);
  document.addEventListener('focusin', remember, true);
  document.addEventListener('input', (e) => {
    if (!ranged(e)) return;
    const el = e.target;
    const now = slot(el);
    if (now === null || now === el.hapticSlot) return;
    el.hapticSlot = now;
    const v = Number(el.value);
    window.haptic(v <= Number(el.min || 0) || v >= Number(el.max || 100) ? 'light' : 'tick');
  }, true);
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
