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
    # Мажорная доминанта минора (A в ре миноре) -- без штрафа: решает сеть
    a_major, a_minor = 9, 12 + 9
    dominant = np.full((1, k), 0.15 / (k - 2))
    dominant[0, a_major], dominant[0, a_minor] = 0.45, 0.40
    assert chordnet.with_key(dominant, key, strength=2.5)[0].argmax() == a_major
    # ...и когда тональность услышана как параллельный мажор (F вместо Dm)
    assert chordnet.with_key(dominant, (5, True), strength=2.5)[0].argmax() == a_major
    assert chordnet.parallel(2 * 12 + 7) == 4 * 12 + 7 and chordnet.parallel(3 * 12) == 3 * 12


def test_extended_vocabulary_names():
    """121 класс: к пяти качествам добавлены sus4, sus2, dim, aug и 6."""
    assert chordnet.class_name(5 * 12, 121) == "Csus4"
    assert chordnet.class_name(6 * 12 + 2, 121) == "Dsus2"
    assert chordnet.class_name(7 * 12 + 11, 121) == "Bdim"
    assert chordnet.class_name(8 * 12, 121) == "Caug"
    assert chordnet.class_name(9 * 12 + 7, 121) == "G6"
    assert chordnet.class_name(120, 121) == "N"
    assert chordnet.class_name(2 * 12 + 7, 61) == "G7"          # старые модели -- как раньше


def test_tuning_follows_detuned_recording():
    """Запись, настроенная на 40 центов выше 440 Гц: без подстройки сеть
    путает аккорды, с оценённым строем -- слышит как надо."""
    librosa = pytest.importorskip("librosa")
    sr = chordnet.SR
    seq = [("Am", [57, 60, 64]), ("F", [53, 57, 60]), ("C", [48, 52, 55]), ("G", [55, 59, 62])] * 2
    t = np.arange(int(sr * 2)) / sr
    y = np.concatenate([sum(sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69 + 0.4) / 12) * k * t) / k
                                    for k in (1, 2, 3)) for m in notes + [notes[0] - 12]) * np.exp(-t * 0.6)
                        for _, notes in seq]).astype(np.float32) * 0.1
    tuning = float(librosa.estimate_tuning(y=y, sr=sr, n_fft=8192, resolution=0.01))
    assert abs(tuning - 0.4) < 0.05
    segments = chordnet.decode(chordnet.probabilities(y, sr, tuning=tuning))
    heard = [next((c for a, b, c, _ in segments if a <= i * 2 + 1 < b), None) for i in range(len(seq))]
    assert heard == [name for name, _ in seq]


def test_unparseable_chord_hint_does_not_disable_the_neural_net(tmp_path, monkeypatch):
    """
    `detect_from_audio(allowed=...)` решает, слушать ли запись нейросетью,
    по тому, нашлось ли в подсказке хоть одно настоящее имя аккорда -- а не
    по тому, было ли поле просто не пустым.

    Раньше условие было `if allowed is None`: любая непустая строка --
    даже опечатка или бессмыслица, которую `_pick_names()` не разбирает ни
    во что -- выключала нейросеть насовсем и откатывала разбор на шаблоны
    (втрое менее точные, по докстрингу `detect_from_audio`). Человек,
    который пытался ПОМОЧЬ разбору списком аккордов, но ошибся в написании,
    получал худший результат на всей песне, а не только там, где опечатался.
    """
    librosa = pytest.importorskip("librosa")
    import soundfile

    from midi2tab import audiochords

    sr = 22050
    t = np.arange(int(sr * 3)) / sr
    y = (sum(np.sin(2 * np.pi * f * t) for f in (130.81, 164.81, 196.0))
         * 0.2).astype(np.float32)  # до-мажорное трезвучие, 3 секунды
    path = tmp_path / "c.wav"
    soundfile.write(str(path), y, sr)

    calls: list[object] = []

    def fake_net_analysis(*args, **kwargs):
        calls.append(True)
        return audiochords.ChordAnalysis(
            chords=[audiochords.AudioChord("C", 0.0, 3.0, 1.0)],
            tempo=120.0, beats=[0.0], downbeats=[0.0], key="C",
        )

    monkeypatch.setattr(audiochords, "_net_analysis", fake_net_analysis)
    monkeypatch.setattr(chordnet, "available", lambda: True)

    # "Хрень" не разбирается ни в один аккорд (как и в test_chord_list_from_user_is_parsed) --
    # но это не повод откатываться на шаблоны вместо нейросети.
    result = audiochords.detect_from_audio(str(path), allowed="хрень")

    assert calls, "нейросеть должна была быть вызвана, несмотря на нераспознанную подсказку"
    assert [c.name for c in result.chords] == ["C"]

    # А настоящая подсказка так и выключает нейросеть, как и задумано --
    # это поведение не меняется.
    calls.clear()
    audiochords.detect_from_audio(str(path), allowed="C")
    assert not calls, "настоящая подсказка должна отключать нейросеть и идти по шаблонам"


def test_net_analysis_estimates_and_forwards_tuning():
    """
    `audiochords._net_analysis()` (боевой путь -- `detect_from_audio()`,
    то, что реально слушает сайт) обязан сам оценивать строй записи и
    передавать его в `chordnet.detect()`.

    Поправка строя уже была проверена (`test_tuning_follows_detuned_recording`
    выше, `web/benchmark.py:run_models`) -- но только КАК ВОЗМОЖНОСТЬ, не
    как часть боевого разбора: `chordnet.detect()` раньше не принимал
    `tuning` вовсе, а `_net_analysis()` звал его без этого довода. Сайт
    слушал любую запись так, будто она идеально настроена на 440 Гц, хотя
    живая гитара и телефонная запись сплошь и рядом на треть полутона
    ниже или выше.
    """
    librosa = pytest.importorskip("librosa")

    from midi2tab import audiochords

    sr = 22050
    t = np.arange(int(sr * 2)) / sr
    shift = 2 ** (0.4 / 12)                      # +40 центов от 440 Гц
    y = (sum(np.sin(2 * np.pi * f * shift * t) for f in (220.0, 261.63, 329.63))
         * 0.2).astype(np.float32)               # ля минорное трезвучие

    captured = {}

    class StubChordnet:
        @staticmethod
        def detect(y, sr, key=None, chroma=None, tuning=0.0):
            captured["tuning"] = tuning
            return []

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, bins_per_octave=36)
    beats = np.array([0, 5, 10, 15])             # короче beats_per_bar*2 -- детали такта не нужны здесь

    audiochords._net_analysis(np, StubChordnet, librosa, y, sr, 120.0, beats, chroma,
                              audiochords.MIN_DURATION, 4, None)

    assert "tuning" in captured
    assert abs(captured["tuning"] - 0.4) < 0.05
