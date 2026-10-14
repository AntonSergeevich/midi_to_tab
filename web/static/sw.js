// NASLUX без интернета. Репетиция, интернет пропал -- мультитрек и треки,
// которые открывали при интернете, продолжают работать: страницы, скрипты,
// данные Студии и звук дорожек берутся из памяти устройства.
//
// Страницы и данные (/api/...) -- сначала из сети (свежее), а если сеть не
// ответила за несколько секунд -- из памяти. Скрипты и стили -- из памяти:
// их адреса меняются с каждой версией (?v=...). Звук дорожек -- из памяти,
// его кладёт туда сам мультитрек, когда загружает трек.
const VERSION = 'v1';
const PAGES = `naslux-pages-${VERSION}`;
const STATIC = `naslux-static-${VERSION}`;
const DATA = `naslux-data-${VERSION}`;
const AUDIO = 'naslux-audio-v1';          // общий с мультитреком (mix.js)
const KEEP = [PAGES, STATIC, DATA, AUDIO];
const WAIT = 4000;                        // столько ждём сеть, потом -- память

// Чужое и личное не храним: вход, оплата, админка, служебное
const NEVER = /^\/(api\/(auth|admin|pay|subscribe|topup|payments|health|webhook)|admin|account|oauth|sw\.js)/;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith('naslux-') && !KEEP.includes(name)) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

// Сменился человек (вход, выход, регистрация) -- чужие треки в памяти ни к чему
async function forgetUser() {
  await Promise.all([PAGES, DATA, AUDIO].map((name) => caches.delete(name)));
}

function timeout(ms) {
  return new Promise((resolve) => { setTimeout(() => resolve(null), ms); });
}

// Мультитрек и плеер -- одна страница на все треки: без интернета подойдёт
// сохранённая от любого трека, а что этот трек не сохранён, скажет она сама
function pageTemplate(path) {
  const m = /^\/(studio\/mix|player)\/[^/]+$/.exec(path);
  return m ? `/${m[1]}/*` : '';
}

async function fromNetworkFirst(request, cacheName, key = request) {
  const cache = await caches.open(cacheName);
  const template = cacheName === PAGES ? pageTemplate(key) : '';
  const network = fetch(request).then(async (response) => {
    // Из /api/ -- только данные (JSON): скачиваемые архивы и табы память не забивают
    const json = (response.headers.get('Content-Type') || '').includes('json');
    if (response.ok && response.status === 200 && (cacheName !== DATA || json)) {
      await cache.put(key, response.clone());
      if (template) await cache.put(template, response.clone());
    }
    return response;
  }).catch(() => null);
  const quick = await Promise.race([network, timeout(WAIT)]);
  if (quick) return quick;
  const saved = await cache.match(key, { ignoreSearch: cacheName === PAGES })
    || (template && await cache.match(template));
  if (saved) return saved;
  const late = await network;            // памяти нет -- ждём сеть до конца
  if (late) return late;
  return null;
}

async function fromCacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  if (response.ok && response.status === 200) await cache.put(request, response.clone());
  return response;
}

// <audio> просит звук кусками (Range); в памяти он целиком -- отдаём кусок
async function rangeFromCache(request) {
  const saved = await caches.match(request.url, { cacheName: AUDIO });
  if (!saved) return fetch(request);
  const blob = await saved.blob();
  const match = /bytes=(\d*)-(\d*)/.exec(request.headers.get('range') || '');
  const start = match && match[1] ? Number(match[1]) : 0;
  const end = match && match[2] ? Math.min(Number(match[2]), blob.size - 1) : blob.size - 1;
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      'Content-Type': saved.headers.get('Content-Type') || 'audio/mpeg',
      'Content-Range': `bytes ${start}-${end}/${blob.size}`,
      'Content-Length': String(end - start + 1),
      'Accept-Ranges': 'bytes',
    },
  });
}

const OFFLINE_PAGE = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NASLUX — нет интернета</title>
<style>body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#15161c;color:#e9e9ee;display:flex;
min-height:100vh;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;text-align:center}
a{color:#d99a4e}</style></head><body><div><h1>Нет интернета</h1>
<p>Эта страница не сохранена на устройстве. Треки, которые вы открывали в мультитреке,
работают и без интернета — они в <a href="/studio">Студии</a>.</p></div></body></html>`;

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method !== 'GET') {
    if (/^\/api\/auth\/(login|logout|register)/.test(url.pathname)) event.waitUntil(forgetUser());
    return;
  }
  if (NEVER.test(url.pathname)) return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => (await fromNetworkFirst(request, PAGES, url.pathname))
      || new Response(OFFLINE_PAGE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } }))());
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(fromCacheFirst(request, STATIC));
    return;
  }
  if (url.pathname.startsWith('/api/studio/file/')) {
    if (request.headers.has('range')) { event.respondWith(rangeFromCache(request)); return; }
    event.respondWith((async () => (await caches.match(request.url, { cacheName: AUDIO })) || fetch(request))());
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    event.respondWith((async () => (await fromNetworkFirst(request, DATA))
      || new Response(JSON.stringify({ detail: 'Нет интернета' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }))());
  }
});

// Страница просит сохранить то, что ей нужно без интернета (мультитрек:
// сама страница, её скрипты и данные трека)
self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type !== 'keep' || !Array.isArray(data.urls)) return;
  event.waitUntil((async () => {
    for (const raw of data.urls) {
      try {
        const url = new URL(raw, self.location.origin);
        if (url.origin !== self.location.origin || NEVER.test(url.pathname)) continue;
        const page = !url.pathname.startsWith('/static/') && !url.pathname.startsWith('/api/');
        const name = page ? PAGES : url.pathname.startsWith('/static/') ? STATIC : DATA;
        const cache = await caches.open(name);
        const key = page ? url.pathname : url.href;
        if (name === STATIC && await cache.match(key)) continue;
        const response = await fetch(url.href, { credentials: 'same-origin' });
        if (response.ok && response.status === 200) {
          if (page && pageTemplate(key)) await cache.put(pageTemplate(key), response.clone());
          await cache.put(key, response);
        }
      } catch (error) { /* нет сети -- сохраним в следующий раз */ }
    }
  })());
});
