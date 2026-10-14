// Шапка на любой странице: кто вошёл и что на счету. Одна надпись для
// всех страниц -- раньше каждая страница писала своё («Владелец · безлимит
// · 50 ₽» на главной, «Безлимит» в Студии), и меню прыгало при переходах.
// Последняя надпись хранится в браузере: следующая страница рисует её
// сразу, ещё до ответа сервера, -- без «загрузка…» и сдвигов.
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
      try { localStorage.setItem(CACHE, JSON.stringify(state)); } catch (error) { /* без памяти */ }
    } catch (error) {
      // Шапка -- не повод ломать страницу
    }
  };
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
