"""Шпаргалка для мастер-класса учителей: /prompts/ и PDF -- как есть."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_prompts_page_and_pdf_are_served_as_is(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    import web.app as app_module

    client = TestClient(app_module.app)
    moved = client.get("/prompts", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/prompts/"
    page = client.get("/prompts/")
    assert page.status_code == 200 and page.headers["content-type"] == "text/html; charset=utf-8"
    assert page.content == (ROOT / "web/prompts/index.html").read_bytes()      # байт в байт
    assert "20 промптов, которые" in page.text
    pdf = client.get("/prompts/prompty-dlya-uchiteley.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    for url in ("/prompts", "/prompts/", "/prompts/prompty-dlya-uchiteley.pdf"):   # curl -I
        assert client.head(url, follow_redirects=False).status_code in (200, 301)
