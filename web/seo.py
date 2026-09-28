"""
Поисковая оптимизация: страницы возможностей, sitemap.xml, robots.txt и
общие мета-теги.

Каждая страница возможности отвечает на один поисковый запрос («подобрать
аккорды по песне», «разделить песню на дорожки»...): заголовок, короткий
рассказ, три шага, вопросы-ответы (FAQPage в JSON-LD -- Яндекс и Google
показывают их прямо в выдаче) и кнопка в нужный инструмент. Текст -- только
правда о том, что сервис умеет: поисковики наказывают за обещания, которых
страница не держит, а люди -- тем более.
"""
from __future__ import annotations

import html
import json
import os

SITE_NAME = "NASLUX"


def site_url() -> str:
    """Адрес сайта для canonical и sitemap (за прокси request.base_url бывает http)."""
    return os.environ.get("NASLUX_SITE_URL", "https://naslux.ru").rstrip("/")


# Страницы приложения, которые стоит показывать поисковикам
APP_PAGES = [
    ("/", "1.0", "daily"),
    ("/studio", "0.9", "weekly"),
    ("/pricing", "0.6", "monthly"),
    ("/offer", "0.2", "yearly"),
    ("/privacy", "0.2", "yearly"),
]

LANDINGS: list[dict] = [
    {
        "slug": "podbor-akkordov",
        "title": "Подбор аккордов онлайн по песне — бесплатно | NASLUX",
        "description": "Загрузите mp3 — нейросеть услышит аккорды песни и покажет их под бегущей "
                       "строкой. Бесплатно до 30 песен в день, без регистрации.",
        "h1": "Подбор аккордов по песне онлайн",
        "lead": "Загрузите запись — нейросеть на слух определит аккорды, тональность и темп. "
                "Аккорды бегут под музыку, как суфлёр: можно сразу играть.",
        "steps": ["Загрузите mp3, m4a, wav или flac", "Нейросеть слушает запись",
                  "Играйте по бегущим аккордам и схемам аппликатур"],
        "faq": [
            ("Это бесплатно?", "Да, аккорды — бесплатно, до 30 песен в день. Платные только "
             "партии инструментов, табы и MIDI."),
            ("Какие аккорды распознаются?", "Мажорные и минорные трезвучия, а также септаккорды "
             "(7, maj7, m7). Нейросеть обучена на живых гитарных записях."),
            ("Нужна ли регистрация?", "Нет. Регистрация нужна, только чтобы история треков "
             "сохранялась на всех устройствах."),
        ],
        "cta": ("Подобрать аккорды", "/"),
        "keywords": "подбор аккордов, аккорды по песне, аккорды онлайн, распознать аккорды",
    },
    {
        "slug": "tabulatura-iz-mp3",
        "title": "Табулатура из mp3 — табы для гитары по записи | NASLUX",
        "description": "Гитарные табы из любой записи: нейросеть выделит партию гитары или баса "
                       "и запишет её табулатурой с MIDI.",
        "h1": "Табулатура из mp3",
        "lead": "Нейросеть отделяет партию гитары или баса от остальной музыки, расшифровывает "
                "ноты и раскладывает их по ладам — получаются табы, которые удобно играть.",
        "steps": ["Загрузите запись песни", "Выберите партию: гитара, бас, клавиши",
                  "Скачайте табы, MIDI или играйте с экрана"],
        "faq": [
            ("Насколько точны табы?", "Лучше всего получаются чёткие партии: соло, риффы, бас. "
             "Густые перегруженные аккорды расшифровываются хуже — их удобнее играть по аккордам."),
            ("В каком формате табы?", "Текстовые табы для гитары и баса, а ещё MIDI каждой "
             "партии — его открывает Guitar Pro, MuseScore и любая DAW."),
            ("Сколько стоит?", "Аккорды бесплатно, партии и табы — по тарифу; есть подписка."),
        ],
        "cta": ("Сделать табы", "/"),
        "keywords": "табулатура из mp3, табы по песне, табы для гитары онлайн, аудио в табы",
    },
    {
        "slug": "mp3-v-midi",
        "title": "Конвертер mp3 в MIDI онлайн — по партиям | NASLUX",
        "description": "Переведите песню в MIDI: нейросеть разделит запись на инструменты и "
                       "расшифрует ноты каждой партии отдельно.",
        "h1": "mp3 в MIDI — по инструментам",
        "lead": "Обычные конвертеры превращают весь микс в кашу нот. Мы сначала делим песню на "
                "партии — вокал, гитару, бас, клавиши, — и только потом переводим каждую в MIDI.",
        "steps": ["Загрузите mp3 или wav", "Нейросеть разделит песню на партии",
                  "Скачайте MIDI каждой партии или архивом"],
        "faq": [
            ("Будут ли барабаны в MIDI?", "В глубоком разделении PRO — да: до 12 дорожек и MIDI "
             "для каждой, включая ударные."),
            ("Где открыть MIDI?", "В любой DAW (FL Studio, Ableton, Reaper, Cubase), в Guitar Pro "
             "и MuseScore."),
        ],
        "cta": ("Перевести в MIDI", "/studio?mode=stems"),
        "keywords": "mp3 в midi, конвертер mp3 в midi, аудио в midi онлайн, wav в midi",
    },
    {
        "slug": "razdelit-pesnyu-na-dorozhki",
        "title": "Разделить песню на дорожки онлайн: вокал, гитара, бас, барабаны | NASLUX",
        "description": "Нейросеть разложит трек на вокал, гитару, бас, барабаны, клавиши — или на "
                       "12 дорожек в режиме PRO с MIDI каждой партии.",
        "h1": "Разделить песню на дорожки",
        "lead": "Загрузите трек — получите отдельные дорожки: вокал, гитару, бас, барабаны, "
                "клавиши и остальное. В режиме PRO — до 12 партий, вплоть до струнных, духовых "
                "и синтезаторов, плюс MIDI.",
        "steps": ["Загрузите песню", "Выберите обычное или глубокое разделение",
                  "Слушайте каждую дорожку и скачивайте mp3"],
        "faq": [
            ("Сколько дорожек получится?", "Обычное разделение — 6 партий. Глубокое PRO — до 12 "
             "и MIDI каждой."),
            ("Для чего это нужно?", "Выучить партию, сделать минус или акапеллу, свести ремикс, "
             "снять ноты для своей группы."),
        ],
        "cta": ("Разделить трек", "/studio?mode=stems"),
        "keywords": "разделить песню на дорожки, stem separation, выделить вокал, разложить трек",
    },
    {
        "slug": "minusovka-iz-pesni",
        "title": "Сделать минусовку из песни онлайн | NASLUX",
        "description": "Уберите голос из песни: нейросеть отделит вокал от музыки — акапелла и "
                       "инструменты отдельными mp3.",
        "h1": "Минусовка из любой песни",
        "lead": "Нейросеть отделяет голос от музыки. Инструменты без голоса — для караоке и "
                "репетиций, акапелла — для ремиксов и каверов.",
        "steps": ["Загрузите песню", "Выберите «Партии»", "Скачайте вокал и инструменты отдельно"],
        "faq": [
            ("Будет ли слышен голос в минусе?", "Современная нейросеть убирает вокал почти "
             "полностью; на сильно реверберированных записях может остаться лёгкий след."),
            ("Можно ли выступать с минусом чужой песни?", "Для личного использования — да. Для "
             "публичного исполнения нужно разрешение правообладателя."),
        ],
        "cta": ("Сделать минус", "/studio?mode=stems"),
        "keywords": "минусовка из песни, убрать голос из песни, минус онлайн, акапелла",
    },
    {
        "slug": "sozdat-pesnyu-nejrosetyu",
        "title": "Создать песню нейросетью по тексту — на русском | NASLUX",
        "description": "Напишите текст или попросите нейросеть сочинить его — получите готовую "
                       "песню с вокалом в любом стиле. Две версии на выбор.",
        "h1": "Песня нейросетью по вашему тексту",
        "lead": "Опишите стиль словами — «лиричный инди-рок, женский вокал» — и вставьте текст. "
                "Нейросеть споёт его по-русски. Мужской, женский голос или дуэт.",
        "steps": ["Опишите стиль и настроение", "Вставьте текст или нажмите «Сочинить»",
                  "Выберите лучшую из двух версий"],
        "faq": [
            ("Поёт ли нейросеть на русском?", "Да, и на русском, и на английском. Для дуэта "
             "отметьте строки пометками (Мужской) и (Женский)."),
            ("Можно ли продлить песню?", "Да: добавьте куплет — нейросеть продолжит с того же "
             "места в том же звучании."),
            ("Кому принадлежит песня?", "Условия использования — в оферте; для своих текстов и "
             "идей это ваша песня."),
        ],
        "cta": ("Создать песню", "/studio?mode=create"),
        "keywords": "создать песню нейросетью, нейросеть пишет песни, песня по тексту, ии песня",
    },
    {
        "slug": "kaver-nejrosetyu",
        "title": "Кавер нейросетью: перепеть песню в другом стиле | NASLUX",
        "description": "Загрузите свою песню — нейросеть перепоёт её с той же мелодией в новом "
                       "жанре: рок, поп, джаз, электроника. Слова распознаются сами.",
        "h1": "Кавер нейросетью",
        "lead": "Та же мелодия и слова — новый жанр и аранжировка. Слова песни нейросеть "
                "распознаёт сама, вставлять текст не обязательно.",
        "steps": ["Загрузите свою песню", "Опишите новый стиль", "Получите две версии кавера"],
        "faq": [
            ("Можно ли загрузить чужую песню?", "Только для личного использования, без "
             "публикации: права на мелодию и текст остаются у авторов. Перед загрузкой сервис "
             "попросит это подтвердить."),
            ("Сохранится ли мой голос?", "Можно оставить свой записанный вокал, а нейросеть "
             "напишет под него новую аранжировку."),
        ],
        "cta": ("Сделать кавер", "/studio?mode=restyle"),
        "keywords": "кавер нейросетью, перепеть песню ии, песня в другом стиле, ai cover",
    },
    {
        "slug": "smenit-tonalnost-pesni",
        "title": "Изменить тональность и темп песни онлайн | NASLUX",
        "description": "Поднимите или опустите тональность под свой голос и замедлите песню, "
                       "чтобы выучить партию, — без искажения звука.",
        "h1": "Сменить тональность и темп",
        "lead": "Транспонируйте песню под свой диапазон или гитарный строй и замедляйте, "
                "чтобы разобрать быстрые места. Высота и скорость меняются независимо.",
        "steps": ["Загрузите трек в Студию", "В меню ⋯ выберите «Темп и тональность»",
                  "Скачайте новую версию"],
        "faq": [
            ("Не станет ли голос «мультяшным»?", "Нет: темп и высота меняются раздельно, "
             "качественным алгоритмом растяжения времени."),
        ],
        "cta": ("Открыть Студию", "/studio"),
        "keywords": "изменить тональность песни, транспонировать онлайн, замедлить песню",
    },
    {
        "slug": "alternativa-suno",
        "title": "Нейросеть для музыки в России: аналог Suno с оплатой картой РФ | NASLUX",
        "description": "Песни нейросетью, каверы, разделение на дорожки, аккорды и табы в одном "
                       "сервисе. Работает в России, оплата российской картой.",
        "h1": "Музыкальная нейросеть, которая работает в России",
        "lead": "Всё в одном месте: песня с нуля по тексту, кавер своей песни, разделение на "
                "дорожки с MIDI, аккорды и табы. Интерфейс на русском, оплата российской картой.",
        "steps": ["Откройте Студию — аккорды можно без регистрации", "Выберите, что сделать",
                  "Платите за результат или по подписке"],
        "faq": [
            ("Чем отличается от Suno?", "Кроме песен с нуля: подбор аккордов, табы, MIDI, "
             "разделение на партии и каверы своих песен — в одном месте и на русском."),
            ("Как оплатить?", "Российской картой, без VPN и зарубежных карт."),
        ],
        "cta": ("Открыть Студию", "/studio"),
        "keywords": "аналог suno, suno в россии, нейросеть для музыки, генератор музыки",
    },
]
BY_SLUG = {landing["slug"]: landing for landing in LANDINGS}


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def head_tags(path: str, title: str, description: str) -> str:
    """canonical + Open Graph + Twitter -- для <head> любой страницы."""
    url = site_url() + path
    image = site_url() + "/static/og.png"
    return "\n".join([
        f'<link rel="canonical" href="{_e(url)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        '<meta property="og:locale" content="ru_RU">',
        f'<meta property="og:url" content="{_e(url)}">',
        f'<meta property="og:title" content="{_e(title)}">',
        f'<meta property="og:description" content="{_e(description)}">',
        f'<meta property="og:image" content="{_e(image)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
    ])


def features_nav() -> str:
    """Подвал «Все возможности» -- внутренние ссылки на страницы возможностей."""
    links = "".join(f'<a href="/{o["slug"]}">{_e(o["h1"])}</a>' for o in LANDINGS)
    return (f'<nav class="landing-more" aria-label="Все возможности">'
            f'<h2>Все возможности NASLUX</h2>{links}</nav>')


def _ld(data: dict) -> str:
    # </script> внутри JSON не даём закрыть тег
    raw = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{raw}</script>'


def app_ld() -> str:
    """Описание сервиса целиком (для главной)."""
    return _ld({
        "@context": "https://schema.org", "@type": "WebApplication",
        "name": SITE_NAME, "url": site_url() + "/", "inLanguage": "ru",
        "applicationCategory": "MultimediaApplication", "operatingSystem": "Web",
        "description": "Песни нейросетью, каверы, разделение на дорожки, MIDI, аккорды и табы.",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "RUB",
                   "description": "Аккорды бесплатно"},
        "featureList": [landing["h1"] for landing in LANDINGS],
    })


def render_landing(landing: dict) -> str:
    path = f"/{landing['slug']}"
    steps = "".join(f"<li>{_e(step)}</li>" for step in landing["steps"])
    faq = "".join(f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>"
                  for q, a in landing["faq"])
    others = "".join(f'<a href="/{o["slug"]}">{_e(o["h1"])}</a>'
                     for o in LANDINGS if o is not landing)
    cta_text, cta_href = landing["cta"]
    ld = _ld({
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in landing["faq"]]},
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": site_url() + "/"},
                {"@type": "ListItem", "position": 2, "name": landing["h1"],
                 "item": site_url() + path}]},
        ],
    })
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#15161c">
<title>{_e(landing['title'])}</title>
<meta name="description" content="{_e(landing['description'])}">
<meta name="keywords" content="{_e(landing['keywords'])}">
{head_tags(path, landing['title'], landing['description'])}
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="stylesheet" href="/static/app.css">
{ld}
</head>
<body>
<div class="wrap">
  <header class="top">
    <a href="/" class="brand"><img src="/static/logo.svg" alt="" width="76" height="24"><span>NASLUX</span></a>
    <div style="display:flex;gap:14px;align-items:center">
      <a href="/studio">Студия</a>
      <a href="/pricing">Тарифы</a>
      <a href="/account" id="account" class="who">Вход</a>
    </div>
  </header>
  <main class="landing">
    <section class="hero">
      <h1>{_e(landing['h1'])}</h1>
      <p class="lead">{_e(landing['lead'])}</p>
      <a class="landing-cta" href="{_e(cta_href)}">{_e(cta_text)} →</a>
    </section>
    <section class="card">
      <h2>Как это работает</h2>
      <ol class="landing-steps">{steps}</ol>
    </section>
    <section class="card landing-faq">
      <h2>Вопросы и ответы</h2>
      {faq}
    </section>
    <nav class="landing-more" aria-label="Другие возможности">
      <h2>Ещё в NASLUX</h2>
      {others}
    </nav>
  </main>
  <footer class="legal">
    <a href="/privacy">Политика конфиденциальности</a>
    <a href="/offer">Оферта</a>
    <span>© NASLUX</span>
  </footer>
</div>
<script src="/static/nav.js" defer></script>
</body>
</html>
"""


def sitemap() -> str:
    base = site_url()
    rows = [(path, priority, freq) for path, priority, freq in APP_PAGES]
    rows += [(f"/{landing['slug']}", "0.8", "monthly") for landing in LANDINGS]
    urls = "".join(f"<url><loc>{_e(base + path)}</loc><changefreq>{freq}</changefreq>"
                   f"<priority>{priority}</priority></url>" for path, priority, freq in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')


def robots() -> str:
    # Личные страницы и API поисковикам не нужны; Clean-param -- для Яндекса,
    # чтобы ?mode=… и ?next=… не плодили дубли.
    return "\n".join([
        "User-agent: *",
        "Disallow: /api/",
        "Disallow: /admin",
        "Disallow: /account",
        "Disallow: /library",
        "Disallow: /player/",
        "Allow: /",
        "",
        "User-agent: Yandex",
        "Disallow: /api/",
        "Disallow: /admin",
        "Disallow: /account",
        "Disallow: /library",
        "Disallow: /player/",
        "Clean-param: mode&style&next&need&paid /",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
        "",
    ])
