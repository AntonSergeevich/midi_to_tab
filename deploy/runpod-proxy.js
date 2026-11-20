// Посредник к RunPod на Cloudflare Workers (бесплатный тариф).
//
// Зачем: 19.11.2026 защита Cloudflare у RunPod перестала пускать сервер
// NASLUX (403 «Attention Required» на любой запрос с его адреса), а из
// других стран те же запросы проходят. Посредник шлёт их со своего адреса.
//
// Как поставить (5 минут, бесплатно):
//   1. dash.cloudflare.com -> Workers & Pages -> Create -> Worker -> Hello World -> Deploy
//   2. Edit code -> вставить этот файл целиком -> Deploy
//   3. На сервере в /opt/nasluh/nasluh.env:
//        NASLUX_RUNPOD_API=https://<имя>.<аккаунт>.workers.dev/api/v2
//        NASLUX_RUNPOD_REST=https://<имя>.<аккаунт>.workers.dev/rest/v1
//      затем: systemctl restart nasluh
//
// Посредник пересылает только на RunPod и только то, что пришло с ключом
// RunPod (без ключа RunPod сам ничего не даст) -- открытым прокси он не станет.
const HOSTS = { api: 'api.runpod.ai', rest: 'rest.runpod.io' };

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const [, kind, ...rest] = url.pathname.split('/');
    const host = HOSTS[kind];
    if (!host || !request.headers.get('Authorization')) {
      return new Response('NASLUX RunPod proxy', { status: 404 });
    }
    const target = `https://${host}/${rest.join('/')}${url.search}`;
    const headers = new Headers(request.headers);
    ['host', 'cf-connecting-ip', 'x-forwarded-for', 'x-real-ip'].forEach((h) => headers.delete(h));
    return fetch(target, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    });
  },
};
