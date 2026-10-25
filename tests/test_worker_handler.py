"""GPU-воркер RunPod (worker/handler.py): только то, что проверяется без
настоящей видеокарты и запущенного ACE-Step -- отказ на неизвестном engine.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("runpod")

import worker.handler as handler


@pytest.mark.parametrize("engine", ["turbo", "fast", "unknown"])
def test_restyle_rejects_unsupported_engine_instead_of_silently_running_sft(engine):
    """turbo лежит в образе, но никогда не грузится в видеопамять (см.
    docstring модуля) -- раньше любой engine, кроме 'base', тихо попадал
    на SFT, и живой тест/замер с --knobs ...,turbo на самом деле всегда
    гонял SFT, думая, что сравнивает его с turbo."""
    with pytest.raises(ValueError, match="engine"):
        handler.restyle({"engine": engine}, "не дойдёт до файла")


@pytest.mark.parametrize("engine", [None, "", "base"])
def test_restyle_accepts_known_engines_and_picks_the_right_model(engine, monkeypatch):
    job_input = {} if engine is None else {"engine": engine}
    monkeypatch.setattr(handler, "tempo_and_key", lambda source: (120, "C", None, "C"))
    monkeypatch.setattr(handler, "duration_of", lambda source: 30.0)
    monkeypatch.setattr(handler, "ace_step", lambda body: ([body["model"]], {}))
    monkeypatch.setattr(handler, "harmony_score", lambda *a, **k: 1.0)
    files, info = handler.restyle(job_input, "src.mp3")
    expected = handler.FULL if engine == "base" else handler.SFT
    assert [model for _, model in files] == [expected, expected]
    assert info["settings"]["model"] == expected
