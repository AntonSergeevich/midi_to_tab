"""Собрать web/static/i18n_en.js из словаря web/i18n/en.json (источник правды)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
words = json.loads((ROOT / "web/i18n/en.json").read_text(encoding="utf-8"))
(ROOT / "web/static/i18n_en.js").write_text(
    "// Собирается scripts/i18n_build.py из web/i18n/en.json -- правьте там.\n"
    "window.NASLUX_EN = " + json.dumps(words, ensure_ascii=False, indent=0, sort_keys=True) + ";\n",
    encoding="utf-8")
print(f"фраз: {len(words)}")
