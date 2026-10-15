// Язык сайта: русский (как написан) или английский -- словарём фраз
// (i18n_en.js) прямо на странице, в том числе в том, что дорисовывают
// скрипты и присылает сервер. Строки в коде не размечены ключами: тексты
// живут там же, где и были, а здесь заменяются целыми фразами.
//
// Язык -- выбранный переключателем (localStorage + cookie lang), иначе по
// браузеру: русскоязычным -- русский, остальным -- английский.
// Не трогаем то, что пишет человек: поля ввода, тексты песен (<pre>),
// названия треков (data-noi18n).
(function () {
  const RU_FAMILY = ['ru', 'uk', 'be', 'kk', 'uz', 'ky', 'tg', 'hy', 'az', 'ka'];
  let saved = '';
  try { saved = localStorage.getItem('naslux-lang') || ''; } catch (error) { /* приватный режим */ }
  if (!saved) saved = (document.cookie.match(/(?:^|; )lang=(ru|en)/) || [])[1] || '';
  const browser = ((navigator.languages && navigator.languages[0]) || navigator.language || 'ru')
    .toLowerCase().split('-')[0];
  const lang = saved || (RU_FAMILY.includes(browser) ? 'ru' : 'en');
  window.NASLUX_LANG = lang;
  window.setLang = (next) => {
    try { localStorage.setItem('naslux-lang', next); } catch (error) { /* не страшно */ }
    document.cookie = `lang=${next}; path=/; max-age=31536000; samesite=lax`;
    location.reload();
  };
  window.__t = (text) => text;
  if (lang !== 'en') return;

  document.documentElement.lang = 'en';
  // Без мигания русского: страница видна, когда переведена
  const hide = document.createElement('style');
  hide.textContent = 'body{visibility:hidden}';
  document.head.appendChild(hide);
  const me = document.currentScript && document.currentScript.src;
  const version = me && me.includes('?') ? me.slice(me.indexOf('?')) : '';
  document.write(`<script src="/static/i18n_en.js${version}"><\/script>`);

  const CYR = /[А-Яа-яЁё]/;
  const LETTER = /[\p{L}\p{N}]/u;
  let pattern = null;
  let dict = null;

  function prepare() {
    dict = window.NASLUX_EN || {};
    const keys = Object.keys(dict).sort((a, b) => b.length - a.length);
    const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    pattern = keys.length ? new RegExp(keys.map(escape).join('|'), 'gu') : null;
  }

  // Фраза целиком -- сразу; иначе -- известные куски внутри строки, только
  // целыми словами («рок» не трогает «срок»)
  function tr(text) {
    if (!text || !CYR.test(text)) return text;
    if (!dict) prepare();
    const trimmed = text.trim().replace(/\s+/g, ' ');
    if (dict[trimmed] !== undefined) return text.replace(text.trim(), dict[trimmed]);
    if (!pattern) return text;
    return text.replace(pattern, (found, offset, whole) => {
      const before = whole[offset - 1];
      const after = whole[offset + found.length];
      if ((before && LETTER.test(before) && LETTER.test(found[0]))
          || (after && LETTER.test(after) && LETTER.test(found[found.length - 1]))) return found;
      return dict[found];
    });
  }
  window.__t = tr;

  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'PRE', 'CODE', 'NOSCRIPT']);
  const ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-tip'];
  const skipped = (el) => !el || (el.closest && el.closest('[data-noi18n], [contenteditable="true"]'));

  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (!parent || SKIP_TAGS.has(parent.tagName) || skipped(parent)) return;
      const next = tr(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || skipped(node)) return;
    // Подсказки (placeholder) у полей ввода -- переводим; то, что человек вписал, -- нет
    for (const name of ATTRS) {
      const value = node.getAttribute(name);
      if (value && CYR.test(value)) {
        const next = tr(value);
        if (next !== value) node.setAttribute(name, next);
      }
    }
    if (node.tagName === 'INPUT' && ['button', 'submit'].includes(node.type) && CYR.test(node.value)) {
      node.value = tr(node.value);
    }
    if (node.tagName === 'META' && node.name === 'description') node.content = tr(node.content);
    if (SKIP_TAGS.has(node.tagName)) return;
    for (let child = node.firstChild; child; child = child.nextSibling) translateNode(child);
  }

  // Юридические тексты (оферта, политика) и SEO-страницы -- только по-русски:
  // переводим шапку, а над текстом -- пометка
  const RU_ONLY = /^\/(offer|privacy)$/.test(location.pathname) || location.pathname.startsWith('/admin');

  function markRussianOnly() {
    const main = document.querySelector('main.landing, .doc, main, article');
    if (!main || main.dataset.noi18n !== undefined) return;
    if (location.pathname.startsWith('/admin')) { document.body.dataset.noi18n = ''; return; }
    main.dataset.noi18n = '';
    const note = document.createElement('p');
    note.className = 'muted i18n-note';
    note.textContent = /^\/(offer|privacy)$/.test(location.pathname)
      ? 'This document is available in Russian. The Russian version is legally binding.'
      : 'This page is available in Russian only.';
    main.parentNode.insertBefore(note, main);
  }

  function run() {
    if (RU_ONLY || document.querySelector('main.landing')) markRussianOnly();
    document.title = tr(document.title);
    translateNode(document.body);
    hide.remove();
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') translateNode(record.target);
        else if (record.type === 'attributes') translateNode(record.target);
        else record.addedNodes.forEach(translateNode);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS });
    new MutationObserver(() => {
      const next = tr(document.title);
      if (next !== document.title) document.title = next;
    }).observe(document.querySelector('title') || document.head, { childList: true, characterData: true, subtree: true });
  }

  // Даты -- по-английски, где скрипты просят ru-RU
  ['toLocaleString', 'toLocaleDateString', 'toLocaleTimeString'].forEach((name) => {
    const original = Date.prototype[name];
    Date.prototype[name] = function (locale, options) {
      return original.call(this, locale === 'ru-RU' || locale === 'ru' ? 'en-GB' : locale, options);
    };
  });

  // Окна браузера (подтверждения, предупреждения) -- тоже по-английски
  ['alert', 'confirm', 'prompt'].forEach((name) => {
    const original = window[name].bind(window);
    window[name] = (message, ...rest) => original(tr(String(message ?? '')), ...rest);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
