"""Запись видеоинструкции /guide -- см. scripts/guide_video/README.md."""
"""Съёмка видеоинструкции NASLUX: настоящий сайт на демо-аккаунте, подписи и курсор."""
import json, sys, time, os
from playwright.sync_api import sync_playwright
G = sys.argv[1]; B = os.environ.get('GUIDE_SITE', 'http://127.0.0.1:8783')
EN = os.environ.get('GUIDE_LANG', 'ru') == 'en'      # английская версия: интерфейс, подписи, демо-тексты
cfg = json.load(open(G + '/data/demo.json'))
OVER = open(G + '/overlay.js').read()
CAPS_EN = {
    '<b>FX</b> — обработка и эффекты дорожки, как в Audition.': '<b>FX</b> — processing and effects for the track, like in Audition.',
    '<b>M</b> — заглушить дорожку. Например, голос нейросети, чтобы спеть самому.': '<b>M</b> — mute a track. For example, the AI vocal, so you can sing it yourself.',
    '<b>NASLUX</b> — студия песен на нейросети. Нейросеть пишет музыку — <b>поёте вы</b>.': '<b>NASLUX</b> — an AI song studio. The AI writes the music — <b>you sing</b>.',
    '<b>S</b> — слушать только эту дорожку: удобно разучивать партию.': '<b>S</b> — solo this track: handy for learning a part.',
    '<b>«Запись»</b> — наденьте наушники и спойте свою партию: дубль ляжет на свою дорожку ровно в такт.': '<b>“Record”</b> — put on headphones and sing your part: the take lands on its own track, right in time.',
    '<b>«Мультитрек»</b> — каждая партия на своей дорожке.': '<b>“Multitrack”</b> — every part on its own track.',
    '<b>«Передать музыканту»</b> — гитарист войдёт по ссылке, и трек со всеми дорожками будет у него.': '<b>“Hand to a musician”</b> — your guitarist opens the link and gets the track with all its stems.',
    '<b>«Свести»</b> — голос вольётся в музыку, как у звукорежиссёра: уровни, пространство, громкость.': '<b>“Mix”</b> — your voice blends into the music like a sound engineer did it: levels, space, loudness.',
    '<b>«Скачать микс»</b> — готовый трек одним файлом. Можно с громкостью для публикации.': '<b>“Download mix”</b> — the finished track as one file, optionally mastered to streaming loudness.',
    '<b>«＋ Добавить эффект»</b> — эквалайзер, компрессор, реверберация, дилей, хорус, сатурация…': '<b>“＋ Add effect”</b> — EQ, compressor, reverb, delay, chorus, saturation…',
    '<b>Метроном</b> идёт по долям самой песни, а не по ровной сетке. <b>Отсчёт</b> — такт перед стартом.': 'The <b>metronome</b> follows the song’s own beats, not a rigid grid. <b>Count-in</b> — one bar before the start.',
    'M и S — в шапке каждой дорожки.': 'M and S — in the header of every track.',
    'В меню <b>«⋯»</b>: минус без голоса, партии, табы и MIDI, ссылка «послушать».': 'In the <b>“⋯”</b> menu: backing track without vocals, stems, tabs & MIDI, a listen link.',
    'Внутри — версии и партии. Справа <b>«О треке»</b>: стиль и текст — «Копировать» или «В форму».': 'Inside — versions and stems. On the right, <b>“About the track”</b>: style and lyrics — “Copy” or “To the form”.',
    'Выберите голос — мужской, женский или дуэт.': 'Pick a voice — male, female or a duet.',
    'Выберите стиль сведения и отметьте, какая дорожка — голос. Голос нейросети можно заглушить.': 'Choose a mix style and mark which track is the vocal. The AI vocal can be muted.',
    'Готовые песни — в <b>«Моих треках»</b>. Нажмите на трек, чтобы открыть его.': 'Finished songs are in <b>“My tracks”</b>. Tap a track to open it.',
    'Записанный голос — в <b>FX</b>: нейросеть уберёт шум комнаты и подтянет ноты к тональности.': 'Your recorded voice → <b>FX</b>: AI removes room noise and tunes the notes to the key.',
    'Курсор идёт по песне. Линейка сверху — аккорды и такты.': 'The playhead moves through the song. The ruler on top shows chords and bars.',
    'Мультитрек: голос, барабаны, бас, гитара, клавиши и <b>ваши записи</b> — каждая партия отдельно.': 'Multitrack: vocals, drums, bass, guitar, keys and <b>your recordings</b> — every part separately.',
    'На телефоне': 'On your phone',
    'На телефоне — всё то же самое.': 'Everything works the same on your phone.',
    'Нажмите <b>«Создать»</b> — через несколько минут будут готовы <b>две версии</b> на выбор.': 'Press <b>“Create”</b> — in a few minutes you get <b>two versions</b> to choose from.',
    'Нет текста? <b>«Сочинить»</b> — опишите, о чём песня, и ИИ напишет текст с рифмой и припевом.': 'No lyrics? <b>“Compose”</b> — say what the song is about, and AI writes rhymed lyrics with a chorus.',
    'Опишите стиль <b>своими словами</b>…': 'Describe the style <b>in your own words</b>…',
    'Откройте <b>Студию → «Создать»</b>. Название — по желанию.': 'Open <b>Studio → “Create”</b>. A title is optional.',
    'Ползунок — громкость дорожки.': 'The slider sets the track volume.',
    'Попробуйте бесплатно: <b>naslux.ru</b> — три песни после регистрации. Оплата картой РФ, без VPN.': 'Try it free at <b>naslux.ru</b> — three songs after sign-up.',
    'Потяните шапку вправо — громкость и эффекты всех дорожек.': 'Drag a header to the right — volume and effects for all tracks.',
    'Свайп влево — спрятать.': 'Swipe left to hide.',
    'Слушайте прямо здесь — плеер внизу экрана.': 'Listen right here — the player is at the bottom of the screen.',
    'Создайте песню, разложите её на дорожки, запишите свой голос и сведите трек.': 'Create a song, split it into stems, record your voice and mix the track.',
    'У каждого эффекта свои ручки, ⏻ вкл/выкл и порядок. Звучит сразу — и в скачанном миксе.': 'Each effect has its own knobs, ⏻ on/off and order. You hear it instantly — and in the downloaded mix.',
    'Шаг 1 · Создать песню': 'Step 1 · Create a song',
    'Шаг 2 · Мои треки': 'Step 2 · My tracks',
    'Шаг 3 · Мультитрек': 'Step 3 · Multitrack',
    '…и нажмите <b>«Дописать с ИИ»</b> — нейросеть добавит жанр, темп, голос и звук.': '…and press <b>“Finish with AI”</b> — AI adds the genre, tempo, voice and sound.',
}
def tr(text):
    return CAPS_EN.get(text, text) if EN else text
STYLE_EN = ("Garage rock, dark and cold sound, raw overdriven guitars, muffled bass, live drums echoing off "
            "concrete walls, raspy strained male vocals, around 120 BPM, quiet verse and explosive chorus")
LYR_EN = {"title": "Garage Rock", "lyrics": "[Verse]\nSnow is piling up outside, it's twenty-five below,\nThe heater in the garage is humming soft and slow,\nFingers sticking to the strings, our breath is turning white,\nBut the roof is shaking with the noise we make tonight.\n\n[Chorus]\nHey, light it up, let the snow pile to the sky,\nWe are burning like a candle in the cold of the night!\nThis garage is our first world, our very first stage —\nSoon the hall will be full, and the crowd will rage!"}
STYLE = ("Гаражный рок, мрачный и холодный звук, сырые перегруженные гитары, глухой бас, живые барабаны "
         "с эхом бетонных стен, мужской хрипловатый вокал с надрывом, около 120 BPM, тихий куплет и взрывной припев")
LYR = {"title": "Гаражный рок", "lyrics": "[Verse]\nЗа окном метель и минус двадцать пять,\nВ гараже обогреватель тянет ток опять,\nПальцы липнут к струнам, пар идёт из рта,\nНо гремит над крышей наша громкота.\n\n[Chorus]\nЭй, зажигай, пусть сугробы до плеча,\nМы горим, как в морозе свеча!\nЭто наш гараж и наш первый мир —\nСкоро будет сцена, будет полный зал!"}

if EN:
    STYLE, LYR = STYLE_EN, LYR_EN

def setup(ctx):
    ctx.add_cookies([{'name': 'uid', 'value': cfg['cookie'], 'url': B}] +
                    ([{'name': 'lang', 'value': 'en', 'url': B}] if EN else []))
    if EN:
        ctx.add_init_script("try{localStorage.setItem('naslux-lang','en')}catch(e){}")
    ctx.add_init_script("localStorage.setItem('naslux_consent','necessary'); localStorage.setItem('naslux.mixPullHint','1');"
                        "try{localStorage.setItem('naslux.seenRelease','99')}catch(e){}")
    ctx.add_init_script(OVER)
    def slow(route, body):
        time.sleep(1.4); route.fulfill(status=200, content_type='application/json', body=json.dumps(body, ensure_ascii=False))
    ctx.route('**/api/studio/style', lambda r: slow(r, {"style": STYLE}))
    ctx.route('**/api/studio/lyrics', lambda r: slow(r, LYR))

STEPS = []   # (видео, шаг, секунда от начала записи) -- для глав на странице /guide

class Shot:
    def __init__(s, pg, name=''): s.pg = pg; s.name = name; s.t0 = time.time()
    def cap(s, html, wait=0.0):
        s.pg.evaluate("h => __dm.cap(h)", tr(html)); s.wait(wait)
    def step(s, t):
        STEPS.append([s.name, tr(t), round(time.time() - s.t0, 1)]); s.pg.evaluate("t => __dm.step(t)", tr(t))
    def wait(s, sec): s.pg.wait_for_timeout(int(sec * 1000))
    def point(s, sel, dx=0, dy=0):
        xy = s.pg.evaluate("([s,dx,dy]) => __dm.point(s,dx,dy)", [sel, dx, dy]); s.wait(0.85); return xy
    def click(s, sel, dx=0, dy=0, after=0.6):
        xy = s.point(sel, dx, dy)
        if xy: s.pg.evaluate("([x,y]) => __dm.rip(x,y)", xy)
        s.pg.click(sel); s.wait(after)
    def type(s, sel, text, delay=35):
        s.click(sel, after=0.2); s.pg.type(sel, text, delay=delay)
    def close_dialogs(s):
        for d in s.pg.query_selector_all('dialog.whats-new [data-close]'):
            try: d.click()
            except Exception: pass

def desktop(p, out):
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--autoplay-policy=no-user-gesture-required'])
    ctx = b.new_context(viewport={'width': 1280, 'height': 720}, locale='en-US' if EN else 'ru-RU', record_video_dir=out,
                        record_video_size={'width': 1280, 'height': 720})
    setup(ctx); pg = ctx.new_page(); s = Shot(pg, 'desktop')
    pg.goto(B + '/'); pg.wait_for_timeout(900); s.close_dialogs()
    s.step('NASLUX')
    s.cap('<b>NASLUX</b> — студия песен на нейросети. Нейросеть пишет музыку — <b>поёте вы</b>.', 3.5)
    pg.mouse.wheel(0, 480); s.cap('Создайте песню, разложите её на дорожки, запишите свой голос и сведите трек.', 3.5)
    pg.mouse.wheel(0, -480); s.wait(0.6)
    s.click('.lp-cta .lp-btn', after=1.4); s.close_dialogs()
    # --- шаг 1: создать песню
    s.step('Шаг 1 · Создать песню')
    s.cap('Откройте <b>Студию → «Создать»</b>. Название — по желанию.', 0.5)
    s.click('[data-mode="create"]', after=0.3)
    s.type('#title', 'Garage Rock' if EN else 'Гаражный рок'); s.wait(1.0)
    s.cap('Опишите стиль <b>своими словами</b>…', 0.3)
    s.type('#prompt', 'garage rock, dark, like a basement in winter' if EN else 'гаражный рок, мрачно, как зимой в подвале', delay=45); s.wait(0.8)
    s.cap('…и нажмите <b>«Дописать с ИИ»</b> — нейросеть добавит жанр, темп, голос и звук.', 0.4)
    s.click('#styleAi', after=2.6)
    s.cap('Нет текста? <b>«Сочинить»</b> — опишите, о чём песня, и ИИ напишет текст с рифмой и припевом.', 0.4)
    s.click('#lyricsAi', after=0.4)
    s.type('#aiPrompt', 'a band rehearsing in a garage in winter, dreaming of their first gig' if EN else 'группа репетирует в гараже зимой и мечтает о первом концерте', delay=30)
    s.click('#aiGo', after=2.8)
    s.point('#lyrics'); s.wait(1.6)
    s.cap('Выберите голос — мужской, женский или дуэт.', 0.3)
    s.click('[data-voice="male"]', after=1.2)
    s.cap('Нажмите <b>«Создать»</b> — через несколько минут будут готовы <b>две версии</b> на выбор.', 0.3)
    s.point('#start'); s.wait(3.2)
    # --- шаг 2: мои треки
    s.step('Шаг 2 · Мои треки')
    s.cap('Готовые песни — в <b>«Моих треках»</b>. Нажмите на трек, чтобы открыть его.', 0.3)
    s.click(f'[data-open="{cfg["song"]}"]', dx=-120, after=1.6)
    s.cap('Внутри — версии и партии. Справа <b>«О треке»</b>: стиль и текст — «Копировать» или «В форму».', 0.3)
    s.point('#detailView .st-info-text'); s.wait(1.6)
    s.click('#detailView [data-copy="style"]', after=2.0)
    s.cap('Слушайте прямо здесь — плеер внизу экрана.', 0.3)
    s.click('#detailView [data-play]', after=2.6)
    s.cap('В меню <b>«⋯»</b>: минус без голоса, партии, табы и MIDI, ссылка «послушать».', 0.3)
    s.click(f'#detailView [data-menu="{cfg["song"]}"][data-file="create_1.mp3"]', after=2.8)
    s.cap('<b>«Передать музыканту»</b> — гитарист войдёт по ссылке, и трек со всеми дорожками будет у него.', 3.2)
    pg.keyboard.press('Escape'); pg.mouse.click(700, 160); s.wait(0.4)
    try: pg.click('#detailView [data-play].on', timeout=1500)
    except Exception: pass
    s.cap('<b>«Мультитрек»</b> — каждая партия на своей дорожке.', 0.2)
    s.click('#detailView a.st-mixbtn', after=0.5)
    pg.wait_for_function('duration > 0 && tracks.length > 0', timeout=60000); s.wait(1.0); s.close_dialogs()
    # --- шаг 3: мультитрек
    s.step('Шаг 3 · Мультитрек')
    s.cap('Мультитрек: голос, барабаны, бас, гитара, клавиши и <b>ваши записи</b> — каждая партия отдельно.', 3.0)
    s.click('#play', after=1.6)
    s.cap('Курсор идёт по песне. Линейка сверху — аккорды и такты.', 3.0)
    idx = pg.evaluate("n => tracks.findIndex(t => /голос нейрос|вокал|vocals/i.test(t.name) && !t.extra)", None)
    idx = idx if idx >= 0 else 0
    s.cap('<b>M</b> — заглушить дорожку. Например, голос нейросети, чтобы спеть самому.', 0.3)
    s.click(f'.mx-btns [data-mute="{idx}"]', after=2.4)
    bass = pg.evaluate("() => Math.max(0, tracks.findIndex(t => /бас|bass/i.test(t.name)))")
    s.cap('<b>S</b> — слушать только эту дорожку: удобно разучивать партию.', 0.3)
    s.click(f'.mx-btns [data-solo="{bass}"]', after=2.2); s.click(f'.mx-btns [data-solo="{bass}"]', after=0.6)
    s.cap('Ползунок — громкость дорожки.', 0.3)
    s.point(f'[data-vol="{bass}"]', 20); pg.evaluate("i => { const el=document.querySelector(`[data-vol=\"${i}\"]`); el.value=135; el.dispatchEvent(new Event('input',{bubbles:true})); }", bass); s.wait(1.8)
    gtr = pg.evaluate("() => Math.max(0, tracks.findIndex(t => /гитар|guitar/i.test(t.name)))")
    s.cap('<b>FX</b> — обработка и эффекты дорожки, как в Audition.', 0.3)
    s.click(f'[data-fx="{gtr}"]', after=1.0)
    s.cap('<b>«＋ Добавить эффект»</b> — эквалайзер, компрессор, реверберация, дилей, хорус, сатурация…', 0.3)
    s.click('#fxAdd', after=0.8); s.click('[data-effadd="eq"]', after=0.8)
    knob = '[data-effknob$=":pres"]'
    s.point(knob, 40)
    pg.evaluate("k => { const el=document.querySelector(k); el.value=5; el.dispatchEvent(new Event('input',{bubbles:true})); }", knob); s.wait(1.2)
    s.click('#fxAdd', after=0.8); s.click('[data-effadd="reverb"]', after=1.4)
    s.cap('У каждого эффекта свои ручки, ⏻ вкл/выкл и порядок. Звучит сразу — и в скачанном миксе.', 3.2)
    s.click('#fxDone', after=0.8)
    s.cap('<b>Метроном</b> идёт по долям самой песни, а не по ровной сетке. <b>Отсчёт</b> — такт перед стартом.', 0.3)
    s.click('#metro', after=2.6)
    s.cap('<b>«Запись»</b> — наденьте наушники и спойте свою партию: дубль ляжет на свою дорожку ровно в такт.', 0.3)
    s.point('#recBtn'); s.wait(3.4)
    me = pg.evaluate("() => Math.max(0, tracks.findIndex(t => /мой голос|my voice/i.test(t.name)))")
    s.cap('Записанный голос — в <b>FX</b>: нейросеть уберёт шум комнаты и подтянет ноты к тональности.', 0.3)
    s.click(f'[data-fx="{me}"]', after=1.2); s.point('#fxAi'); s.wait(2.6); s.click('#fxDone', after=0.6)
    s.cap('<b>«Свести»</b> — голос вольётся в музыку, как у звукорежиссёра: уровни, пространство, громкость.', 0.3)
    s.click('#mixBtn', after=1.6)
    if pg.is_visible('#mixSheet'):
        s.cap('Выберите стиль сведения и отметьте, какая дорожка — голос. Голос нейросети можно заглушить.', 3.2)
        s.click('#mixGo', after=2.6)
    s.cap('<b>«Скачать микс»</b> — готовый трек одним файлом. Можно с громкостью для публикации.', 0.3)
    s.click('#exportMix', after=2.8); s.click('#exportCancel', after=0.6)
    try: pg.click('#play', timeout=1500)
    except Exception: pass
    s.step('NASLUX')
    s.cap('Попробуйте бесплатно: <b>naslux.ru</b> — три песни после регистрации. Оплата картой РФ, без VPN.', 4.5)
    path = pg.video.path(); ctx.close(); b.close(); return path

def phone(p, out):
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
    ctx = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, locale='en-US' if EN else 'ru-RU',
                        record_video_dir=out, record_video_size={'width': 390, 'height': 844}, device_scale_factor=2)
    setup(ctx); pg = ctx.new_page(); s = Shot(pg, 'phone')
    pg.goto(f"{B}/studio/mix/{cfg['song']}?file=create_1.mp3")
    pg.wait_for_function('duration > 0 && tracks.length > 0', timeout=60000); s.wait(0.8); s.close_dialogs()
    s.step('На телефоне')
    s.cap('На телефоне — всё то же самое.', 2.2)
    pg.evaluate("document.querySelector('.mx-row[data-i=\"1\"]').scrollIntoView({block:'center'})"); s.wait(0.8)
    s.cap('M и S — в шапке каждой дорожки.', 2.4)
    s.cap('Потяните шапку вправо — громкость и эффекты всех дорожек.', 0.5)
    r = pg.query_selector('.mx-row[data-i="1"] .mx-ctrl').bounding_box(); y = r['y'] + r['height'] / 2
    pg.evaluate("([x,y]) => __dm.at(x,y)", [60, y]); s.wait(0.8)
    pg.evaluate("""([y]) => { const el=document.elementFromPoint(40,y); const mk=(t,x)=>{const tt=new Touch({identifier:5,target:el,clientX:x,clientY:y});
      el.dispatchEvent(new TouchEvent(t,{bubbles:true,cancelable:true,touches:t==='touchend'?[]:[tt],changedTouches:[tt],targetTouches:t==='touchend'?[]:[tt]}))};
      return new Promise(res=>{mk('touchstart',40);let i=0;const step=()=>{i++;mk('touchmove',40+i*12);__dm.at(40+i*12,y);if(i<20)setTimeout(step,30);else{mk('touchend',280);res();}};step();}); }""", [y])
    s.wait(2.6)
    s.cap('Свайп влево — спрятать.', 0.3)
    pg.evaluate("""([y]) => { const el=document.elementFromPoint(300,y); const mk=(t,x)=>{const tt=new Touch({identifier:6,target:el,clientX:x,clientY:y});
      el.dispatchEvent(new TouchEvent(t,{bubbles:true,cancelable:true,touches:t==='touchend'?[]:[tt],changedTouches:[tt],targetTouches:t==='touchend'?[]:[tt]}))};
      return new Promise(res=>{mk('touchstart',300);let i=0;const step=()=>{i++;mk('touchmove',300-i*12);__dm.at(300-i*12,y);if(i<20)setTimeout(step,30);else{mk('touchend',60);res();}};step();}); }""", [y])
    s.wait(2.2)
    path = pg.video.path(); ctx.close(); b.close(); return path

with sync_playwright() as p:
    out = G + '/raw'; os.makedirs(out, exist_ok=True)
    d = desktop(p, out); ph = phone(p, out)
    json.dump({"desktop": d, "phone": ph, "steps": STEPS}, open(G + '/raw/videos.json', 'w'), ensure_ascii=False)
    print(d, ph)
