"""PDF-инструкция не отстаёт от сайта: новая версия, другие цены или правка
шаблона без пересборки (python3 scripts/manual_build.py) -- тест падает."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_manual_pdf_matches_latest_release_and_prices(tmp_path):
    built = json.loads((ROOT / "docs/manual/built.json").read_text(encoding="utf-8"))
    assert (ROOT / "web/media/naslux-manual.pdf").stat().st_size > 50_000
    code = ("import json, sys; sys.path.insert(0, 'scripts'); import manual_build as m; m.manual_env();"
            " print(json.dumps({'values': m.manual_values(), 'template': m.template_hash()}, ensure_ascii=False))")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NASLUX_", "MUREKA_", "RUNPOD_"))}
    env["MIDI2TAB_DATA"] = str(tmp_path / "data")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    now = json.loads(out.stdout.strip().splitlines()[-1])
    hint = "пересоберите инструкцию: python3 scripts/manual_build.py"
    assert built["version"] == now["values"]["version"], f"вышла версия {now['values']['version']} — {hint}"
    assert built["template"] == now["template"], f"шаблон docs/manual/manual.html изменён — {hint}"
    assert built["values"] == now["values"], f"цены изменились — {hint}"
