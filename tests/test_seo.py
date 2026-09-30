"""Главная -- Студия, аккорды на /chords, страницы возможностей, sitemap, robots."""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "s")
    monkeypatch.setenv("NASLUX_SITE_URL", "https://naslux.ru")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as test_client:
        yield test_client


def test_home_is_upload_first_and_studio_has_its_page(client):
    home = client.get("/").text
    assert 'id="drop"' in home and 'href="/studio" class="studio-card"' in home
    # загрузка -- сразу под заголовком, раньше схемы и Студии
    assert home.index("<h1>") < home.index('id="drop"') < home.index('class="flow"') \
        < home.index('class="studio-card"')
    assert '<link rel="canonical" href="https://naslux.ru/">' in home
    assert "{{" not in home and 'href="/podbor-akkordov"' in home
    studio = client.get("/studio").text
    assert 'id="modes"' in studio and 'href="https://naslux.ru/studio"' in studio and "{{" not in studio
    moved = client.get("/chords", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/"


def test_landings_have_meta_faq_and_cta(client):
    from web import seo

    for landing in seo.LANDINGS:
        page = client.get(f"/{landing['slug']}")
        assert page.status_code == 200, landing["slug"]
        html = page.text
        assert f"<h1>{landing['h1']}</h1>" in html
        assert 'property="og:title"' in html and 'rel="canonical"' in html
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1))
        faq = data["@graph"][0]
        assert faq["@type"] == "FAQPage" and len(faq["mainEntity"]) == len(landing["faq"])
        assert len(landing["title"]) <= 90 and len(landing["description"]) <= 200


def test_sitemap_lists_every_page(client):
    from web import seo

    response = client.get("/sitemap.xml")
    assert response.headers["content-type"].startswith("application/xml")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = {el.text for el in ET.fromstring(response.content).findall("s:url/s:loc", ns)}
    assert "https://naslux.ru/" in locs and "https://naslux.ru/studio" in locs
    for landing in seo.LANDINGS:
        assert f"https://naslux.ru/{landing['slug']}" in locs


def test_robots_hides_private_pages(client):
    text = client.get("/robots.txt").text
    assert "Disallow: /api/" in text and "Disallow: /account" in text
    assert "Sitemap: https://naslux.ru/sitemap.xml" in text


def test_favicon_is_a_real_ico(client):
    """Робот Яндекса берёт /favicon.ico и не принимает SVG под этим именем."""
    icon = client.get("/favicon.ico")
    assert icon.headers["content-type"] == "image/x-icon"
    assert icon.content[:4] == b"\x00\x00\x01\x00"
    assert 'href="/static/favicon-120.png"' in client.get("/").text
    assert client.get("/static/favicon-120.png").status_code == 200
