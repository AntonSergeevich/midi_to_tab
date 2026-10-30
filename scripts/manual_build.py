"""Собрать PDF-инструкцию NASLUX: docs/manual/manual.html -> web/media/naslux-manual.pdf.

Цены, номер версии и «Что нового» подставляются из кода сайта (те же
pricing_values(), что у страницы тарифов, и web/releases.py), поэтому в
инструкции не бывает старых цен. Каждый выпуск -- пересобрать:

    python3 scripts/manual_build.py

Рядом пишется docs/manual/built.json (версия, отпечаток шаблона, цены);
tests/test_manual.py падает, если вышла новая версия, поменялись цены или
шаблон, а PDF не пересобран. Снимки экрана -- docs/manual/img (демо-аккаунт,
scripts/guide_video/seed.py), переснимать после заметных изменений вида.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs/manual/manual.html"
BUILT = ROOT / "docs/manual/built.json"
PDF = ROOT / "web/media/naslux-manual.pdf"
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")


def manual_env() -> None:
    """Цены как на сайте сейчас: кавер чужой песни идёт на YuE2 (15 кредитов),
    пока Mureka без денег -- так же, как в рабочем окружении."""
    os.environ.setdefault("MIDI2TAB_DATA", tempfile.mkdtemp(prefix="manual-"))
    os.environ.setdefault("MIDI2TAB_SECRET", "manual")
    os.environ.setdefault("RUNPOD_API_KEY", "manual")
    os.environ.setdefault("MUREKA_API_KEY", "manual")
    os.environ.setdefault("NASLUX_MUREKA_OFF", "1")


def manual_values() -> dict:
    """Всё, что подставляется в шаблон, кроме «Что нового» (оно -- из выпуска)."""
    sys.path.insert(0, str(ROOT))
    from web import app as app_module, releases

    latest = releases.RELEASES[0]
    return {**app_module.pricing_values(), "version": latest.version,
            "date": f"{latest.date.day} {MONTHS[latest.date.month - 1]} {latest.date.year}"}


def whats_new() -> str:
    from web import releases

    latest = releases.RELEASES[0]
    items = "".join(f"<li><b>{html.escape(c.title)}.</b> {html.escape(c.text)}"
                    + (f' <span class="muted">({html.escape(c.where)})</span>' if c.where else "") + "</li>"
                    for c in latest.changes)
    return f'<p><b>{html.escape(latest.title)}.</b> {html.escape(latest.summary)}</p><ul class="news">{items}</ul>'


def template_hash() -> str:
    return hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()[:16]


def render(values: dict) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {**values, "whatsNew": whats_new()}.items():
        text = text.replace("{{" + key + "}}", str(value))
    if "{{" in text:
        raise SystemExit(f"в шаблоне остались подстановки: {text[text.index('{{'):][:40]}")
    return text


def main() -> None:
    manual_env()
    values = manual_values()
    page = TEMPLATE.with_name("_render.html")      # рядом с шаблоном: картинки -- по относительным путям
    page.write_text(render(values), encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            tab = browser.new_page()
            tab.goto(page.as_uri())
            tab.wait_for_load_state("networkidle")
            PDF.parent.mkdir(parents=True, exist_ok=True)
            tab.pdf(path=str(PDF), format="A4", print_background=True, prefer_css_page_size=True,
                    display_header_footer=True, header_template="<span></span>",
                    footer_template='<div style="width:100%;font-size:8px;color:#888;text-align:center">'
                                    f'NASLUX · инструкция · версия {values["version"]} · '
                                    '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
            browser.close()
    finally:
        page.unlink(missing_ok=True)
    BUILT.write_text(json.dumps({"version": values["version"], "template": template_hash(), "values": values},
                                ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{PDF.relative_to(ROOT)}: {PDF.stat().st_size // 1024} КБ, версия {values['version']}")


if __name__ == "__main__":
    main()
