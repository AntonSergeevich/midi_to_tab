"""Промо-ролик NASLUX для TikTok / YouTube Shorts / VK Клипов (9:16) и YouTube (16:9).

Кадры -- настоящий сайт на демо-аккаунте (scripts/guide_video/seed.py), снятые
сразу в итоговом разрешении (не растягиваем запись экрана -- текст чёткий);
подписи -- поверх страницы шрифтом сайта; ffmpeg -- плавный наезд камеры,
переходы и музыка (гимн NASLUX) с самого громкого места.

    NASLUX_CODE=<папка с кодом сайта> python3 tools/make_promo.py <папка демо (data/demo.json)> <куда> [vertical|horizontal] [сценарий.json]

Лежит в ветке promo, отдельно от кода. Сайт на демо-данных -- как для
видеоинструкции (scripts/guide_video/README.md в ветке с кодом), адрес --
PROMO_SITE (по умолчанию http://127.0.0.1:8784), логотип берётся из
NASLUX_CODE, музыка -- PROMO_MUSIC (по умолчанию music/naslux-anthem.mp3).
Сценарий -- список сцен {kind, caption, sub, seconds}; без него -- SCENES ниже.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SITE = os.environ.get("PROMO_SITE", "http://127.0.0.1:8784")

# Сценарий по умолчанию: «записал на телефон -> полноценная песня -> минус ->
# мультитрек -> поделиться». Крючок в первые 2 секунды, конец -- призыв.
SCENES = [
    {"kind": "card", "caption": "Записал песню<br>на телефон?", "sub": "Сделай из неё полноценный трек", "seconds": 2.6},
    {"kind": "remake", "caption": "Загрузи запись", "sub": "и напиши стиль своими словами", "seconds": 3.2},
    {"kind": "track", "caption": "Пара минут —", "sub": "аранжировка, голос и обложка", "seconds": 3.4},
    {"kind": "player", "caption": "Слушай и заучивай", "sub": "плейлисты, повтор, текст в такт", "seconds": 3.0},
    {"kind": "parts", "caption": "Минус и голос", "sub": "в один клик — пой сам", "seconds": 3.0},
    {"kind": "mixer", "caption": "Мультитрек", "sub": "каждая партия отдельно", "seconds": 3.2},
    {"kind": "end", "caption": "NASLUX", "sub": "naslux.ru · попробуй бесплатно", "seconds": 3.4},
]

CAPTION_CSS = """
#promoCap { position: fixed; left: 0; right: 0; z-index: 2147483000; pointer-events: none; text-align: center;
  padding: 0 7%; font-family: Inter, system-ui, sans-serif; }
#promoCap.top { top: 6%; } #promoCap.bottom { bottom: 7%; }
#promoCap b { display: inline-block; padding: .18em .55em; border-radius: .45em; background: #d99a4e; color: #1a1206;
  font-size: var(--cap, 34px); font-weight: 900; letter-spacing: -.01em; box-shadow: 0 10px 40px rgba(0,0,0,.55); }
#promoCap small { display: table; margin: .45em auto 0; padding: .3em .7em; border-radius: .6em; color: #fff;
  background: rgba(15,16,21,.88); font-size: calc(var(--cap, 34px) * .6); font-weight: 700; }
"""

CARD_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0f1015;font-family:Inter,system-ui,sans-serif;color:#fff;overflow:hidden}
.bg{position:fixed;inset:-20%;background:radial-gradient(40% 30% at 30% 30%,#ff2fb3aa,transparent 70%),
 radial-gradient(45% 35% at 70% 65%,#1e6bffaa,transparent 70%),radial-gradient(40% 30% at 50% 90%,#ff9a3c99,transparent 70%);
 filter:blur(30px)}
.wrap{position:relative;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 8%}
img{width:var(--logo);margin-bottom:5%}
h1{margin:0;font-size:var(--h);font-weight:900;line-height:1.05;letter-spacing:-.02em}
p{margin:.8em 0 0;font-size:var(--p);font-weight:700;color:#f3c58a}
</style></head><body><div class="bg"></div><div class="wrap">LOGO<h1>CAPTION</h1><p>SUB</p></div></body></html>"""


def caption(page, scene: dict, where: str, size: int) -> None:
    page.evaluate("""([css, html, where, size]) => {
      let s = document.getElementById('promoCapCss');
      if (!s) { s = document.createElement('style'); s.id = 'promoCapCss'; s.textContent = css; document.head.append(s); }
      let c = document.getElementById('promoCap');
      if (!c) { c = document.createElement('div'); c.id = 'promoCap'; document.body.append(c); }
      c.className = where; c.style.setProperty('--cap', size + 'px'); c.innerHTML = html;
      document.querySelectorAll('[class*=cookie], .st-toast, .wn-modal').forEach((x) => x.remove());
    }""", [CAPTION_CSS, f"<b>{scene['caption']}</b><small>{scene.get('sub', '')}</small>", where, size])


def capture(demo: Path, out: Path, vertical: bool, scenes: list[dict]) -> list[Path]:
    cfg = json.loads((demo / "data/demo.json").read_text())
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    # 9:16 -- телефон (360x640, x3 = 1080x1920); 16:9 -- компьютер (1280x720, x1.5 = 1920x1080)
    view = {"width": 360, "height": 640} if vertical else {"width": 1280, "height": 720}
    scale = 3 if vertical else 1.5
    cap = 30 if vertical else 40
    logo = (Path(os.environ.get("NASLUX_CODE", ".")) / "web/static/logo.svg").read_text()
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport=view, device_scale_factor=scale, locale="ru-RU",
                                  is_mobile=vertical, has_touch=vertical)
        ctx.add_cookies([{"name": "uid", "value": cfg["cookie"], "url": SITE}])
        page = ctx.new_page()
        clean = "() => document.querySelectorAll('[class*=cookie], .wn-modal, .st-modal').forEach((x) => { if (x.classList.contains('st-modal')) x.hidden = true; else x.remove(); })"
        for n, scene in enumerate(scenes, 1):
            kind = scene["kind"]
            if kind in ("card", "end"):
                html = (CARD_HTML.replace("LOGO", logo.replace("<svg", '<svg style="width:var(--logo);height:auto;margin-bottom:6%"', 1) if kind == "end" else "")
                        .replace("CAPTION", scene["caption"]).replace("SUB", scene.get("sub", "")))
                page.set_content(html)
                page.evaluate("([v]) => { const r = document.documentElement.style; r.setProperty('--h', v ? '15vw' : '7.5vw');"
                              " r.setProperty('--p', v ? '6vw' : '2.6vw'); r.setProperty('--logo', v ? '62vw' : '30vw'); }",
                              [vertical])
            elif kind == "remake":
                page.goto(f"{SITE}/studio")
                page.wait_for_timeout(1200)
                page.evaluate(clean)
                page.click('[data-mode="restyle"]')
                page.set_input_files("#file", str(demo / "audio/guitar.mp3"))
                page.wait_for_timeout(600)
                page.evaluate(clean)
                page.fill("#prompt", "ню-метал, тяжёлые гитары, мощный припев, мужской вокал с надрывом")
                page.evaluate("window.scrollTo(0, 0)")
                caption(page, scene, "bottom", cap)
                # подпись -- не поверх поля стиля: поле -- в верхней половине кадра
                if vertical:
                    page.evaluate("document.getElementById('prompt').scrollIntoView({block: 'start'}); window.scrollBy(0, -170)")
            elif kind in ("track", "player", "parts"):
                page.goto(f"{SITE}/studio")
                page.wait_for_timeout(1200)
                page.evaluate(clean)
                page.evaluate(f"openDetail('{cfg['song']}')")   # Студия помнит открытый трек -- открываем сами
                page.wait_for_timeout(1600)
                if kind == "track":
                    page.evaluate("window.scrollTo(0, Math.max(0, document.getElementById('detailView').offsetTop - 10))")
                    caption(page, scene, "bottom", cap)
                elif kind == "parts":
                    page.locator(".st-parts").scroll_into_view_if_needed()
                    page.evaluate("window.scrollBy(0, -80)")
                    caption(page, scene, "top", cap)
                else:
                    page.evaluate("document.querySelector('#detailView .st-version [data-play]').click()")
                    page.wait_for_timeout(500)
                    page.evaluate("fullPlayer(true)")
                    page.wait_for_timeout(900)
                    page.evaluate("document.getElementById('audio').currentTime = 12")
                    page.wait_for_timeout(300)
                    caption(page, scene, "bottom" if vertical else "bottom", cap)
            elif kind == "mixer":
                page.goto(f"{SITE}/studio/mix/{cfg['song']}?file=create_1.mp3")
                page.wait_for_timeout(3500)
                page.evaluate(clean)
                page.evaluate("document.querySelectorAll('.mx-hint, [data-tip-show], .tip-pop').forEach((x) => x.remove())")
                caption(page, scene, "bottom", cap)
            page.wait_for_timeout(300)
            path = frames / f"{n:02d}.png"
            page.screenshot(path=str(path))
            paths.append(path)
            print(f"кадр {n}: {kind}", flush=True)
        browser.close()
    return paths


def loudest_start(music: str, length: float) -> float:
    """Начало самого громкого куска нужной длины -- ролик сразу «качает»."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", music, "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    import array
    data = array.array("h", raw)
    step = 2000                                   # полсекунды
    energy = [sum(x * x for x in data[i:i + step]) / step for i in range(0, len(data) - step, step)]
    win = int(length * 2)
    best, at = -1.0, 0
    for i in range(0, max(1, len(energy) - win)):
        e = sum(energy[i:i + win])
        if e > best:
            best, at = e, i
    return max(0.0, at / 2 - 0.5)


def render(paths: list[Path], scenes: list[dict], out: Path, vertical: bool, music: str) -> Path:
    w, h = (1080, 1920) if vertical else (1920, 1080)
    fade = 0.35
    clips = []
    for n, (path, scene) in enumerate(zip(paths, scenes), 1):
        d = scene["seconds"]
        frames = int(d * 30)
        clip = out / f"clip{n:02d}.mp4"
        # плавный наезд камеры: 1.00 -> 1.06 за сцену (картинку сперва вдвое больше -- без дрожи)
        zoom = f"zoompan=z='1+0.06*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps=30"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(path), "-vf",
                        f"scale={w * 2}:{h * 2},{zoom},format=yuv420p", "-t", str(d), "-r", "30",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(clip)], check=True)
        clips.append((clip, d))
    inputs, chain, last, offset = [], "", "0:v", 0.0
    for clip, _ in clips:
        inputs += ["-i", str(clip)]
    for k in range(1, len(clips)):
        offset += clips[k - 1][1] - fade
        label = f"v{k}"
        chain += f"[{last}][{k}:v]xfade=transition=fade:duration={fade}:offset={offset:.2f}[{label}];"
        last = label
    total = sum(d for _, d in clips) - fade * (len(clips) - 1)
    start = loudest_start(music, total)
    name = out / ("naslux-promo-vertical.mp4" if vertical else "naslux-promo-horizontal.mp4")
    audio = (f"[{len(clips)}:a]atrim={start:.2f}:{start + total:.2f},asetpts=PTS-STARTPTS,"
             f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.4,afade=t=out:st={total - 1.6:.2f}:d=1.6[a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-i", music, "-filter_complex",
                    chain + audio, "-map", f"[{last}]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    "-t", f"{total:.2f}", str(name)], check=True)
    for clip, _ in clips:
        clip.unlink()
    return name


def main() -> None:
    demo, out = Path(sys.argv[1]), Path(sys.argv[2])
    vertical = (sys.argv[3] if len(sys.argv) > 3 else "vertical") == "vertical"
    scenes = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else SCENES
    music = os.environ.get("PROMO_MUSIC", str(Path(__file__).resolve().parents[1] / "music/naslux-anthem.mp3"))
    out.mkdir(parents=True, exist_ok=True)
    paths = capture(demo, out / ("v" if vertical else "h"), vertical, scenes)
    print(render(paths, scenes, out, vertical, music))


if __name__ == "__main__":
    main()
