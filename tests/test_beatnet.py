"""Доли нейросетью Beat This: без модели -- молчим, с моделью -- куски
по 30 секунд склеиваются без швов, темп -- по медиане промежутков."""

import numpy as np
import pytest

from midi2tab import beatnet


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
