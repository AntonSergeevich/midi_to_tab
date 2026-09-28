"""Нейросеть аккордов: подписи классов, сглаживание, подключение к разбору."""

from __future__ import annotations

import numpy as np
import pytest

from midi2tab import chordnet


def test_class_names_follow_vocabulary_size():
    # 25 классов -- мажор/минор, 61 -- ещё 7, maj7, m7; последний -- «нет аккорда»
    assert chordnet.class_name(0, 25) == "C"
    assert chordnet.class_name(12 + 9, 25) == "Am"
    assert chordnet.class_name(24, 25) == "N"
    assert chordnet.class_name(2 * 12 + 7, 61) == "G7"
    assert chordnet.class_name(3 * 12 + 5, 61) == "Fmaj7"
    assert chordnet.class_name(4 * 12 + 2, 61) == "Dm7"
    assert chordnet.class_name(60, 61) == "N"


def test_viterbi_removes_single_frame_jitter():
    probs = np.full((12, 3), 0.1)
    probs[:, 0] = 0.8
    probs[5] = [0.3, 0.6, 0.1]          # один кадр «перещёлкнуло»
    probs[8:, 0], probs[8:, 2] = 0.1, 0.8  # настоящая смена
    path = chordnet.viterbi(probs, change_penalty=2.0)
    assert list(path) == [0] * 8 + [2] * 4


def test_engine_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("NASLUX_CHORD_ENGINE", "classic")
    assert chordnet.available() is False


@pytest.mark.skipif(not chordnet.MODEL.is_file(), reason="модели нет в сборке")
def test_model_hears_a_c_major_triad():
    """Живая проверка модели: чистое до-мажорное трезвучие -- «C»."""
    pytest.importorskip("onnxruntime")
    pytest.importorskip("librosa")
    sr = 22050
    t = np.arange(int(sr * 4)) / sr
    y = sum(np.sin(2 * np.pi * f * t) for f in (130.81, 164.81, 196.0, 261.63)) * 0.2
    found = chordnet.detect(y.astype(np.float32), sr)
    longest = max(found, key=lambda c: c[1] - c[0])
    assert longest[2] in ("C", "Cmaj7")


def test_key_settles_major_minor_doubt_but_not_confident_chords():
    """В ре миноре сомнение D/Dm решается в пользу Dm; уверенный D остаётся D;
    чужие аккорды без пары (например, E7) не трогаются."""
    k = 61
    d_major, d_minor = 2, 12 + 2
    doubtful = np.full((1, k), 0.15 / (k - 2))
    doubtful[0, d_major], doubtful[0, d_minor] = 0.45, 0.40
    confident = np.full((1, k), 0.04 / (k - 1))
    confident[0, d_major] = 0.96
    key = (2, False)                                     # Dm
    assert chordnet.with_key(doubtful, key, strength=2.5)[0].argmax() == d_minor
    assert chordnet.with_key(confident, key, strength=2.5)[0].argmax() == d_major
    assert chordnet.with_key(doubtful, key, strength=0.0) is doubtful
    assert chordnet.parallel(2 * 12 + 7) == 4 * 12 + 7 and chordnet.parallel(3 * 12) == 3 * 12
