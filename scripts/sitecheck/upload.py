"""Проверка живого сайта: открыть Студию анонимом и загрузить 10 секунд
тона -- как человек. Печатает ответы сервера, ошибки страницы и список
треков. Запуск -- site-check.yml (у GitHub naslux.ru открыт)."""

import asyncio
import subprocess
import sys

from playwright.async_api import async_playwright

SITE = sys.argv[1] if len(sys.argv) > 1 else "https://naslux.ru"


async def main() -> None:
    tracks = []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=10",
                    "-b:a", "128k", "probe.mp3"], check=True)
    log = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context(viewport={"width": 1440, "height": 900})).new_page()
        page.on("pageerror", lambda e: log.append(f"ОШИБКА СТРАНИЦЫ: {e}"))
        page.on("console", lambda m: log.append(f"консоль {m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("response", lambda r: log.append(f"{r.request.method} {r.url.replace(SITE, '')} -> {r.status}")
                if "/api/" in r.url else None)
        await page.goto(f"{SITE}/studio", wait_until="networkidle")
        await page.evaluate("document.querySelectorAll('.cookiebar').forEach(e=>e.remove())")
        print("кнопка загрузки видна:", await page.is_visible("#uploadTrack"))
        await page.set_input_files("#uploadFile", "probe.mp3")
        try:
            await page.wait_for_selector("#rightsModal:not([hidden])", timeout=8000)
            await page.check("[name=rights][value=own]")
            await page.check("#rightsAgree")
            await page.click("#rightsOk")
        except Exception as error:  # noqa: BLE001
            log.append(f"окно прав не появилось: {error}")
        await page.wait_for_timeout(12000)
        print("тост:", await page.inner_text("#toast") if await page.is_visible("#toast") else "(скрыт)")
        tracks = await page.eval_on_selector_all(".st-row b", "e => e.map(x => x.textContent)")
        print("треки:", tracks)
        await page.screenshot(path="upload.png", full_page=False)
        await browser.close()
    print("\n".join(log))
    uploads = [line for line in log if "/api/studio/upload" in line]
    # Итог -- в аннотацию: её видно через API, даже когда лог не скачать
    ok = any("probe" in t for t in tracks)
    print(f"::{'notice' if ok else 'error'}::загрузка {'работает' if ok else 'НЕ работает'}: "
          f"{'; '.join(uploads)[:300] or 'запроса загрузки нет'}")
    sys.exit(0 if ok else 1)


asyncio.run(main())
