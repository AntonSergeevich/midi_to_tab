"""Английская версия сайта: словарь фраз и подключение переводчика на страницы."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CYR = re.compile(r"[А-Яа-яЁё]")


def test_dictionary_is_english_and_built():
    words = json.loads((ROOT / "web/i18n/en.json").read_text(encoding="utf-8"))
    assert len(words) > 900
    bad = [k for k, v in words.items() if not isinstance(v, str) or not v.strip() or CYR.search(v)]
    assert not bad, bad[:5]
    built = (ROOT / "web/static/i18n_en.js").read_text(encoding="utf-8")
    body = json.loads(built[built.index("{"):built.rindex("}") + 1])
    assert body == words, "web/static/i18n_en.js устарел -- запустите scripts/i18n_build.py"


def test_every_page_loads_the_translator_once(monkeypatch, tmp_path):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    import sys

    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        for path in ("/", "/studio", "/pricing", "/account", "/offer", "/privacy", "/podbor-akkordov"):
            html = client.get(path).text
            assert html.count("/static/i18n.js?v=") == 1, path
            # первым скриптом в <head>, сразу после кодировки -- до того, как страница видна
            head = html[:html.index("</head>")]
            assert head.index("i18n.js") < head.index("<title>") if "<title>" in head else True, path
