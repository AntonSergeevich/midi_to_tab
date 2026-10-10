"""Доли нейросетью Beat This: без модели -- молчим, с моделью -- куски
по 30 секунд склеиваются без швов, темп -- по медиане промежутков."""

from pathlib import Path

import numpy as np
import pytest

from midi2tab import beatnet

ROOT = Path(__file__).resolve().parent.parent


class FrameSession:
    """Вместо модели: логит доли зависит только от своего кадра. В кадре
    спектрограммы [0] -- номер кадра, [1] -- 1 для настоящих кадров (0 --
    поля нарезки). Доля каждые `every` кадров, «раз» -- каждые 4 доли."""

    def __init__(self, every):
        self.every = every

    def run(self, _, feeds):
        x = feeds["spect"][0]
        idx, real = x[:, 0].astype(int), x[:, 1] > 0
        beat = np.where(real & (idx % self.every == 0), 5.0, -5.0)
        down = np.where(real & (idx % (4 * self.every) == 0), 5.0, -5.0)
        return beat[None].astype(np.float32), down[None].astype(np.float32)


@pytest.fixture
def fake(monkeypatch):
    def use(every):
        monkeypatch.setattr(beatnet, "_session", lambda: FrameSession(every))
    return use


def numbered(frames):
    spect = np.zeros((frames, 128), dtype=np.float32)
    spect[:, 0] = np.arange(frames)
    spect[:, 1] = 1
    return spect


def test_without_model_file_nothing_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(beatnet, "MODEL", tmp_path / "нет.onnx")
    beatnet._session.cache_clear()
    assert beatnet.available() is False
    assert beatnet.track(np.zeros(22050, dtype=np.float32)) is None
    beatnet._session.cache_clear()


@pytest.mark.parametrize("frames", [300, 1500, 1501, 4000, 9123])
def test_chunks_are_glued_without_seams(fake, frames):
    fake(37)
    beat, down = beatnet.logits(numbered(frames))
    expected = np.where(np.arange(frames) % 37 == 0, 5.0, -5.0)
    assert np.array_equal(beat, expected)
    assert np.array_equal(down, np.where(np.arange(frames) % 148 == 0, 5.0, -5.0))


def test_tempo_from_beats_81_bpm_not_108(fake, monkeypatch):
    fake(37)                                    # 37 кадров по 20 мс = 0.74 с = 81 BPM
    monkeypatch.setattr(beatnet, "spectrogram", lambda y, sr: numbered(3000))
    bpm, beats, downbeats = beatnet.track(np.zeros(10), 22050)
    assert abs(bpm - 81.08) < 0.1
    assert beats[:3] == [0.0, 0.74, 1.48]
    assert downbeats[:2] == [0.0, 2.96]


def test_bars_are_regular_even_when_net_marks_downbeats_chaotically():
    beats = np.arange(40) * 0.74
    noisy = beats[[1, 5, 9, 13, 17, 2, 21, 25, 30, 29, 33]]      # в основном «раз» на 1, 5, 9…
    assert np.allclose(beatnet.bars(beats, noisy), beats[1::4])
    waltz = beats[[0, 3, 6, 9, 12, 15, 18, 21, 24]]
    assert np.allclose(beatnet.bars(beats, waltz), beats[0::3])
    assert np.allclose(beatnet.bars(beats, np.array([])), beats[::4])


def test_intro_in_eighths_joins_the_song_grid():
    """Вступление, где сеть отметила восьмые, встаёт на сетку песни."""
    step = 0.74
    intro = np.arange(0, 20 * step, step / 2)                 # 40 «долей» по восьмым
    song = np.arange(20 * step, 120 * step, step)
    grid = beatnet.steady(np.concatenate([intro, song]))
    assert np.allclose(np.diff(grid), step)
    assert np.isclose(grid[0], 0.0) and np.isclose(grid[-1], song[-1])
    assert np.allclose(beatnet.steady(song), song)             # ровная песня не меняется


def test_retime_puts_midi_beats_and_bars_on_the_song_grid():
    """MIDI нот YuE2 -- на доли записи: метроном (доли MIDI) = доли песни."""
    import io

    import numpy as np
    import pretty_midi

    from midi2tab import beatgrid

    song = pretty_midi.PrettyMIDI(initial_tempo=120)
    piano = pretty_midi.Instrument(0)
    starts = [0.05, 0.7, 1.33, 2.9, 4.41, 6.02]
    piano.notes = [pretty_midi.Note(100, 60 + i, t, t + 0.3) for i, t in enumerate(starts)]
    song.instruments.append(piano)
    raw = io.BytesIO()
    song.write(raw)
    # неровные доли, первая -- 0.18 с (раньше обрезалась и двигала все ноты)
    beats = [0.18, 0.62, 1.08, 1.5, 1.98, 2.42, 2.86, 3.3, 3.76, 4.2, 4.64, 5.1, 5.56, 6.0]
    out = pretty_midi.PrettyMIDI(io.BytesIO(beatgrid.retime(raw.getvalue(), beats, downbeat=1.08)))
    got = out.get_beats()
    assert np.allclose([min(abs(got - b)) for b in beats], 0, atol=0.002)
    assert np.allclose(out.get_downbeats()[1:3], [1.08, 2.86], atol=0.002)
    assert np.allclose(sorted(n.start for n in out.instruments[0].notes), starts, atol=0.002)


def test_beatgrid_retime_matches_the_yue2_copy():
    """midi2tab/beatgrid.retime существует в двух местах: сам beatgrid.py
    (использует сайт, см. web/studio.py:_align_midi) и его буквальная копия в
    yue2/ss_run.py -- там свой venv и образ без пакета midi2tab (см. docstring
    retime в обоих файлах), импортировать общий код оттуда не вышло бы. Если
    поправить логику темпа/сетки в одном файле и забыть про другой, сайт и
    YuE2-воркер начнут тихо по-разному сводить ноты с долями записи. Сверяем
    тела функций (без докстрок и комментария на сигнатуре -- они специально
    разные, у каждого файла свой "скопировано из..."), а не целый файл."""
    import ast

    def body(path):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "retime":
                stmts = node.body
                if stmts and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant) \
                        and isinstance(stmts[0].value.value, str):
                    stmts = stmts[1:]                       # докстрока -- не код
                return ast.dump(node.args), [ast.dump(n) for n in stmts]
        raise AssertionError(f"retime не найдена в {path}")

    assert body(ROOT / "midi2tab" / "beatgrid.py") == body(ROOT / "yue2" / "ss_run.py")
