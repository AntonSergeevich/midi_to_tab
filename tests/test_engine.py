"""Тесты движка. Запуск: pytest -q"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midi2tab import arrange, gp5out
from midi2tab.midiin import NoteEvent
from midi2tab.timing import TPQ, DURATIONS, GRIDS, decompose, quantize
from midi2tab.tuning import DEFAULT_TUNING, TUNINGS, Fretboard


def board() -> Fretboard:
    return Fretboard(TUNINGS[DEFAULT_TUNING])


# --------------------------------------------------------------- длительности


@pytest.mark.parametrize("ticks", [3, 6, 12, 24, 27, 48, 60, 72, 96, 144, 192, 384, 501])
def test_decompose_is_exact(ticks):
    """Разбиение длительности обязано складываться обратно без остатка."""
    parts = decompose(ticks)
    assert sum(p.ticks for p in parts) == ticks


@pytest.mark.parametrize("grid", sorted(set(GRIDS.values())))
def test_decompose_exact_for_every_grid(grid):
    """Любая длительность, кратная шагу сетки, раскладывается точно."""
    for multiple in range(1, 33):
        ticks = grid * multiple
        assert sum(p.ticks for p in decompose(ticks)) == ticks


def test_decompose_drops_at_most_a_sixtyfourth():
    """Контракт: неразложимый остаток меньше 1/64 отбрасывается, больше -- нет."""
    smallest = min(d.ticks for d in DURATIONS)
    for ticks in range(smallest, 200):
        lost = ticks - sum(p.ticks for p in decompose(ticks))
        assert 0 <= lost < smallest


def test_decompose_without_triplets_avoids_tuplets():
    parts = decompose(96, allow_triplets=False)
    assert all(not p.tuplet for p in parts)
    assert sum(p.ticks for p in parts) == 96


def test_quantize_snaps_to_grid():
    assert quantize(13, 12) == 12
    assert quantize(19, 12) == 24
    assert quantize(0, 12) == 0


# ---------------------------------------------------------------- раскладка


def _sequence(pitches, step=24, dur=24):
    return [NoteEvent(i * step, dur, p, 90) for i, p in enumerate(pitches)]


def test_open_chords_are_recovered():
    """
    Арпеджио C-Am-F-G должно лечь в открытую позицию.

    Это главная проверка смысла: наивный алгоритм уезжает на 7-10 лады,
    правильный -- выдаёт стандартные аппликатуры в пределах трёх ладов.
    """
    notes = _sequence([48, 52, 55, 60, 45, 52, 57, 60, 41, 48, 53, 57, 43, 50, 55, 59])
    placements, report = arrange.arrange(notes, board(), auto_transpose=False)
    assert report.max_fret_used <= 3
    frets = [p.positions[0][1] for p in placements]
    assert frets[:4] == [3, 2, 0, 1]   # C:  x32010
    assert frets[4:8] == [0, 2, 2, 1]  # Am: x02210


def test_arpeggio_avoids_repeating_a_string():
    """Соседние ноты арпеджио не должны попадать на одну струну."""
    notes = _sequence([40, 47, 52, 55, 59, 64], step=12, dur=12)
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    strings = [p.positions[0][0] for p in placements]
    assert len(set(strings)) == len(strings)


def test_chord_notes_land_on_distinct_strings():
    notes = [NoteEvent(0, 48, p, 90) for p in (40, 47, 52, 55, 59, 64)]
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    used = [pos[0] for pos in placements[0].positions if pos]
    assert len(used) == len(set(used))


def test_out_of_range_notes_do_not_crash():
    """
    Регрессия: ноты вне диапазона гитары роняли исходный конвертер
    (TypeError: cannot unpack non-iterable NoneType object).
    """
    notes = _sequence([20, 30, 57, 120, 130])
    placements, report = arrange.arrange(
        notes, board(), auto_transpose=False, fold_octaves=False
    )
    assert len(placements) == 5
    assert report.dropped_out_of_range > 0
    assert any("вне диапазона" in m for m in report.messages)


def test_octave_folding_saves_notes():
    notes = _sequence([24, 26, 28])  # ниже гитары
    _, report = arrange.arrange(notes, board(), auto_transpose=False, fold_octaves=True)
    assert report.folded_into_range == 3
    assert report.dropped_out_of_range == 0


def test_dense_chord_is_thinned_to_string_count():
    notes = [NoteEvent(0, 48, p, 90) for p in (40, 43, 47, 50, 52, 55, 59, 64)]
    placements, report = arrange.arrange(notes, board(), auto_transpose=False)
    played = placements[0].played
    assert len(played) <= 6
    assert report.dropped_excess_voices > 0


def test_auto_transpose_moves_part_into_range():
    notes = _sequence([84, 88, 91])  # выше удобного диапазона
    _, report = arrange.arrange(notes, board(), auto_transpose=True)
    assert report.transposed_semitones != 0
    assert report.dropped_out_of_range == 0


# ------------------------------------------------------------------- такты


@pytest.mark.parametrize("num,den", [(4, 4), (3, 4), (6, 8), (5, 4), (7, 8), (12, 8)])
def test_measures_are_exactly_full(num, den, tmp_path):
    """Каждый такт обязан быть заполнен ровно, при любом размере."""
    import guitarpro as gp

    notes = _sequence([40, 45, 50, 55, 59, 64, 59, 55], step=12, dur=12)
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    out = str(tmp_path / "t.gp5")
    gp5out.write_gp5(placements, board(), out, time_signatures=[(0, num, den)], tempo=100)

    song = gp.parse(out)
    expected = int(num * 960 * 4 / den)  # GP считает целую как 3840
    for measure in song.tracks[0].measures:
        total = sum(b.duration.time for b in measure.voices[0].beats)
        assert total == expected, f"такт {measure.header.number}: {total} вместо {expected}"


def test_note_across_barline_is_tied(tmp_path):
    """Долгая нота должна переезжать через тактовую черту лигой, а не обрезаться."""
    import guitarpro as gp

    notes = [NoteEvent(TPQ * 3, TPQ * 4, 52, 90)]  # с 4-й доли, длиной в целый такт
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    out = str(tmp_path / "tie.gp5")
    gp5out.write_gp5(placements, board(), out, time_signatures=[(0, 4, 4)], tempo=100)

    song = gp.parse(out)
    second = song.tracks[0].measures[1]
    first_sounding = next(
        (b for b in second.voices[0].beats if b.status == gp.BeatStatus.normal), None
    )
    assert first_sounding is not None
    assert first_sounding.notes[0].type == gp.NoteType.tie


def test_time_signature_change_is_honoured():
    plan = gp5out.measure_plan([(0, 4, 4), (TPQ * 4, 3, 4)], TPQ * 10)
    assert plan[0][2:] == (4, 4)
    assert any(p[2:] == (3, 4) for p in plan)


def test_spans_never_overlap():
    notes = [NoteEvent(0, 200, 52, 90), NoteEvent(24, 200, 55, 90)]
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    spans = gp5out.build_spans(placements, GRIDS["1/16 (шестнадцатые)"])
    for a, b in zip(spans, spans[1:]):
        assert a.end <= b.start


# -------------------------------------------------------------------- строи


def test_capo_shifts_open_pitches():
    plain = Fretboard(TUNINGS[DEFAULT_TUNING])
    capoed = Fretboard(TUNINGS[DEFAULT_TUNING], capo=3)
    assert capoed.open_pitch(0) == plain.open_pitch(0) + 3


def test_seven_string_tuning_reaches_lower():
    seven = Fretboard(TUNINGS["7 струн (BEADGBE)"])
    assert seven.string_count == 7
    assert seven.playable(35)  # B1 -- недоступна на шестиструнке


# ----------------------------------------------------------------- кодировки


def test_cyrillic_title_survives_round_trip(tmp_path):
    """
    Регрессия: кириллица в названии роняла запись
    ("'charmap' codec can't encode characters"), потому что PyGuitarPro
    пишет в cp1252. Кодировка обязана подбираться по тексту.
    """
    import guitarpro as gp

    notes = _sequence([52, 55, 59])
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    out = str(tmp_path / "ru.gp5")
    gp5out.write_gp5(placements, board(), out, title="Пожары_D_minor125 (Guitar)")

    song = gp.parse(out, encoding="cp1251")
    assert song.title == "Пожары_D_minor125 (Guitar)"


def test_pick_encoding_prefers_latin_then_cyrillic():
    assert gp5out.pick_encoding(["Fires"]) == "cp1252"
    assert gp5out.pick_encoding(["Пожары"]) == "cp1251"


def test_unsupported_text_falls_back_to_latin(tmp_path):
    """Символы вне всех 8-битных кодировок не должны ронять запись."""
    import guitarpro as gp

    notes = _sequence([52, 55])
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    out = str(tmp_path / "emoji.gp5")
    notices = []
    gp5out.write_gp5(
        placements, board(), out, title="Пожары 🔥 火", on_note=notices.append
    )
    song = gp.parse(out)
    assert song.title  # файл читается, название не пустое
    assert notices


def test_transliterate_handles_russian():
    assert gp5out.transliterate("Пожары") == "Pozhary"
    assert gp5out.transliterate("Guitar 1") == "Guitar 1"


def test_user_tempo_reaches_audio_transcription():
    """
    Темп должен уходить в распознавание, а не только в готовый файл:
    по нему аудио размечается на доли, и от него зависит квантизация.
    """
    import inspect

    from midi2tab import convert as convert_module

    source = inspect.getsource(convert_module.convert)
    assert "tempo=float(settings.tempo)" in source


# ------------------------------------------------------- чистка распознанного


def test_quiet_harmonics_are_removed_loud_octaves_kept():
    """
    Обертон и настоящая октава различаются по громкости.

    Тихое ми октавой выше сыгранного ми -- призрак модели.
    Громкое соль октавой выше сыгранного соль -- настоящая нота.
    """
    from midi2tab import cleanup

    notes = [
        NoteEvent(0, 24, 52, 100),   # E3 сыграно
        NoteEvent(2, 20, 64, 45),    # E4 призрак (+12, вдвое тише)
        NoteEvent(3, 18, 71, 30),    # B4 призрак (+19, тише)
        NoteEvent(24, 24, 55, 95),   # G3 сыграно
        NoteEvent(24, 24, 67, 90),   # G4 настоящая октава, громкая
    ]
    cleaned, report = cleanup.clean(notes)
    pitches = [n.pitch for n in cleaned]
    assert report.removed_ghosts == 2
    assert pitches == [52, 55, 67]


def test_cleanup_can_be_switched_off():
    from midi2tab import cleanup

    notes = [NoteEvent(0, 24, 52, 100), NoteEvent(1, 20, 64, 30)]
    cleaned, report = cleanup.clean(
        notes, cleanup.CleanupSettings(remove_ghosts=False, merge_repeats=False)
    )
    assert len(cleaned) == 2
    assert report.removed_ghosts == 0


def test_polyphony_limit_keeps_loudest():
    from midi2tab import cleanup

    notes = [NoteEvent(0, 24, p, v) for p, v in ((40, 30), (47, 110), (52, 100))]
    cleaned, report = cleanup.clean(
        notes, cleanup.CleanupSettings(remove_ghosts=False, max_polyphony=2)
    )
    assert report.removed_excess == 1
    assert sorted(n.pitch for n in cleaned) == [47, 52]


# ------------------------------------------------------------- прослушивание


def test_playback_pitches_match_original_notes():
    """Обратный пересчёт струна+лад в высоту обязан совпасть с исходником."""
    from midi2tab import playback

    pitches = [48, 52, 55, 60]
    notes = _sequence(pitches)
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    events = playback.build_events(placements, board(), 120)
    assert len(events) == len(pitches) * 2          # на каждую ноту вкл и выкл
    assert sorted({e.pitch for e in events}) == pitches


def test_playback_export_uses_chosen_instrument(tmp_path):
    import pretty_midi

    from midi2tab import playback

    notes = _sequence([48, 52])
    placements, _ = arrange.arrange(notes, board(), auto_transpose=False)
    out = str(tmp_path / "p.mid")
    playback.write_midi(placements, board(), out, 120, "Овердрайв")
    back = pretty_midi.PrettyMIDI(out)
    assert back.instruments[0].program == playback.INSTRUMENTS["Овердрайв"]
    assert len(back.instruments[0].notes) == 2


def test_playback_degrades_without_backend():
    """Без MIDI-выхода приложение обязано объяснить причину, а не молчать."""
    from midi2tab import playback

    ok, why = playback.available()
    assert ok or "pip install" in why


def test_tempo_survives_numpy_2_array():
    """
    librosa отдаёт темп то числом, то массивом из одного элемента.

    В NumPy 2 float() от такого массива падает с "only 0-dimensional
    arrays can be converted to Python scalars" -- на сервере из-за этого
    обрывался разбор любого трека. Проверяем все формы сразу.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import as_float

    assert as_float(np.array([123.4])) == pytest.approx(123.4)
    assert as_float(np.array(123.4)) == pytest.approx(123.4)
    assert as_float(np.float64(123.4)) == pytest.approx(123.4)
    assert as_float(123.4) == pytest.approx(123.4)
    assert as_float(np.array([])) == 0.0
    assert as_float(np.array([np.nan]), default=120.0) == 120.0


def test_chord_list_from_user_is_parsed():
    """
    Человек может сам назвать аккорды песни -- он знает лучше.

    Принимаем и через запятую, и через пробел; бемоли переводим в диезы,
    потому что шаблоны названы диезами. Неизвестное имя молча отбрасываем:
    опечатка не должна ронять разбор целого трека.
    """
    pytest.importorskip("numpy")

    from midi2tab.audiochords import _pick_names, _templates

    names = _templates()[0]
    assert [names[i] for i in _pick_names(names, "Dm, Bb, F, C")] == ["C", "Dm", "F", "A#"]
    assert [names[i] for i in _pick_names(names, "dm bb")] == ["Dm", "A#"]
    assert _pick_names(names, "") is None
    assert _pick_names(names, "хрень") is None


def test_power_chord_costs_more_than_a_triad():
    """
    Квинт-аккорд обязан быть дороже трезвучия, а не дешевле.

    Штраф начисляется за каждую ноту шаблона, и из-за этого двухнотный
    квинт-аккорд когда-то выходил дешевле трезвучия -- при том, что обе
    его ноты в трезвучие входят и подходят всюду, где подходит оно. Разбор
    превращался в частокол из D5, C5, G5.
    """
    pytest.importorskip("numpy")

    from midi2tab.audiochords import _templates

    names, _, penalties, _, _ = _templates()
    cost = dict(zip(names, penalties))
    assert cost["D5"] > cost["Dm"]
    assert cost["D5"] > cost["D"]


def test_separation_quality_levels_are_ordered():
    """
    Качество разделения покупается временем, и цена должна быть честной.

    «Точнее» обязано и перекрывать куски сильнее, и усреднять по сдвигам,
    и заявлять себя более долгим: на этом числе строится оценка процента,
    и если оно соврёт, полоса замрёт на середине.
    """
    from midi2tab import separate

    fast = separate.QUALITY["быстро"]
    better = separate.QUALITY["точнее"]
    assert better[0] > fast[0]      # перекрытие кусков
    assert better[1] > fast[1]      # сдвиги
    assert better[2] > fast[2]      # во сколько раз дольше
    assert separate.DEFAULT_QUALITY in separate.QUALITY


def test_instrument_bands_stay_inside_hearing():
    """Полосы дочистки не должны резать сам инструмент."""
    from midi2tab import separate

    low, high = separate.BANDS["guitar"]
    assert low < 82.4 < high        # нижняя ми шестой струны
    assert high > 1318.5            # ми на 24-м ладу первой струны


def test_key_is_found_from_a_chroma():
    """Тональность узнаётся по тому, какие ступени звучат чаще."""
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import guess_key, key_name

    # Ре минор: D F A плюс остальные ступени лада потише
    chroma = np.full((12, 8), 0.05)
    for pitch, weight in ((2, 1.0), (5, 0.8), (9, 0.8), (0, 0.6), (7, 0.5), (10, 0.4)):
        chroma[pitch, :] = weight
    assert key_name(guess_key(chroma)) == "Dm"


def test_out_of_key_chords_are_penalised():
    """
    Чужая нота в аккорде -- сильный довод против него.

    На реальной песне в ре миноре разбор упорно выдавал Bm (B D F#), где
    на деле звучал B-бемоль: две ноты из трёх в тональность не входят.
    Штраф за них убрал ошибку и довёл совпадение корней до 100%.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _templates, key_penalties

    names = _templates()[0]
    natural_minor = np.zeros((12, 4))
    for pitch in (2, 4, 5, 7, 9, 10, 0):        # ре минор натуральный
        natural_minor[pitch, :] = 1.0
    penalties = dict(zip(names, key_penalties(names, (2, False), natural_minor)))

    for own in ("Dm", "Gm", "A#", "C", "Am", "F"):
        assert penalties[own] == 0.0, own
    assert penalties["Bm"] > 0.0        # B и F# -- чужие
    assert penalties["F#"] > 0.0


def test_harmonic_minor_is_decided_by_the_music():
    """
    Поднятая седьмая ступень -- вопрос к записи, а не к правилу.

    У одной песни доминанта мажорная, у другой минорная, и решать это
    за песню нельзя: ошибка стоит целого аккорда в круге. Смотрим, что
    в записи громче -- поднятая седьмая или натуральная.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _templates, key_penalties

    names = _templates()[0]

    natural = np.zeros((12, 4))
    natural[0, :] = 1.0                 # до-бекар громкий
    assert dict(zip(names, key_penalties(names, (2, False), natural)))["A"] > 0.0

    raised = np.zeros((12, 4))
    raised[1, :] = 1.0                  # до-диез громкий
    assert dict(zip(names, key_penalties(names, (2, False), raised)))["A"] == 0.0


def test_uncertain_bars_are_kept_not_dropped():
    """
    Сомнительный такт остаётся в разборе, а не выбрасывается.

    Раньше такты с низкой уверенностью просто пропускались, и в ленте
    появлялись дыры по одному-два такта. На реальном треке так терялось
    21 секунда из 173 -- на слух это «половины аккордов нет». Человеку
    полезнее сомнительная подпись, помеченная сомнительной, чем пустота,
    под которую нечего играть.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _segments

    names = ["Dm", "C"]
    path = np.array([0, 1, 0, 1])
    confidence = np.array([1.0, 0.01, 1.0, 0.01])   # каждый второй такт сомнителен
    times = [0.0, 2.0, 4.0, 6.0, 8.0]

    chords = _segments(names, (path, confidence), times, min_duration=0.5)
    assert [c.name for c in chords] == ["Dm", "C", "Dm", "C"]

    # Разметка идёт сплошь, без провалов между отрезками
    for previous, following in zip(chords, chords[1:]):
        assert following.start == pytest.approx(previous.end)
    assert chords[0].start == 0.0 and chords[-1].end == pytest.approx(8.0)


def test_bar_grid_is_taken_from_the_recording():
    """
    Начало такта определяется по записи, а не по черновым аккордам.

    Прежде сдвиг выбирался по тому, куда попадали смены из первого,
    заведомо чернового прохода. Ошибка там уводила сетку тактов, и
    дальше КАЖДЫЙ аккорд вставал не на своё место.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _bar_offset

    # Гармония меняется каждые четыре доли, начиная со второй
    chroma = np.zeros((12, 40))
    for block, pitch in enumerate((0, 5, 7, 2, 0, 5, 7, 2, 0)):
        start = 2 + block * 4
        chroma[pitch, start:start + 4] = 1.0
    beats = np.arange(0, 40, 1)

    assert _bar_offset(chroma, beats, 4) == 2


def test_quality_falls_back_to_the_key_when_the_third_is_silent():
    """
    Мажор или минор решается ладом там, где терцию не слышно.

    В плотном миксе с перегруженной гитарой терции в спектре может не
    быть вовсе. Раньше в этом случае побеждал квинт-аккорд -- шаблон из
    двух нот, которому терция не нужна, -- и на двух реальных песнях
    разбор целиком состоял из D5, A#5, G5. Ни одного верного названия:
    0% против 98% после правки.

    Лад отвечает на этот вопрос без всякого спектра: в ре миноре на соль
    ожидается минор, на си-бемоле -- мажор.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _pick_quality, _templates

    names, vectors, penalties, roots, qualities = _templates()
    key = (2, False)                       # ре минор

    def decide(root, sounding):
        profile = np.zeros(12)
        for pitch in sounding:
            profile[pitch] = 1.0
        profile = profile / np.linalg.norm(profile)
        return names[_pick_quality(np, names, vectors, penalties, roots,
                                   qualities, root, profile, key)]

    # Звучат только основной тон и квинта -- терции нет. Решает лад.
    assert decide(7, (7, 2)) == "Gm"       # соль: четвёртая ступень -> минор
    assert decide(10, (10, 5)) == "A#"     # си-бемоль: шестая -> мажор
    assert decide(2, (2, 9)) == "Dm"       # тоника -> минор

    # Отчётливо прозвучавшая чужая терция перевешивает лад: заимствованные
    # аккорды вроде Cm вместо C в ре миноре разбор обязан услышать.
    assert decide(0, (0, 3, 7)) == "Cm"


def test_power_chords_are_never_a_label():
    """
    Квинт-аккорд -- не название гармонии.

    Даже когда гитарист играет D5, в песеннике пишут Dm: подпись
    называет аккорд, а не то, сколько струн зажато.
    """
    from midi2tab.audiochords import LABEL_QUALITIES

    assert "5" not in LABEL_QUALITIES
    assert "m" in LABEL_QUALITIES and "" in LABEL_QUALITIES


def test_chord_shapes_match_the_ones_guitarists_play():
    """
    Аппликатуры выводятся из строя, а не берутся из таблицы.

    Проверяем на тех аккордах, положение которых знает наизусть любой
    гитарист: если совпало с ними, значит, и для остальных выведется
    разумное.
    """
    from midi2tab.shapes import shapes_for
    from midi2tab.tuning import DEFAULT_TUNING, TUNINGS, Fretboard

    board = Fretboard(TUNINGS[DEFAULT_TUNING])

    def best(name):
        shape = shapes_for(name, board, 1)[0]
        return "".join("x" if f is None else str(f) for f in shape.frets)

    assert best("Am") == "x02210"
    assert best("C") == "x32010"
    assert best("D") == "xx0232"
    assert best("Dm") == "xx0231"
    assert best("E") == "022100"
    assert best("Em") == "022000"
    assert best("G") == "320003"
    assert best("A") == "x02220"


def test_barre_is_not_drawn_over_an_open_string():
    """
    Баррэ прижимает ВСЕ струны на своём ладу.

    Значит, рядом с ним не может быть открытой струны: палец её всё
    равно прижмёт, и нота выйдет другая. Такую картинку человек
    поставить не сможет.
    """
    from midi2tab.shapes import shapes_for
    from midi2tab.tuning import DEFAULT_TUNING, TUNINGS, Fretboard

    board = Fretboard(TUNINGS[DEFAULT_TUNING])
    for name in ("Dm", "C", "Gm", "A#", "F", "Am", "Cm", "Fm", "Bm", "G", "E"):
        for shape in shapes_for(name, board, 3):
            if shape.barre:
                assert 0 not in [f for f in shape.frets if f is not None], name


def test_shapes_follow_the_tuning():
    """
    Таблицу аккордов пришлось бы заводить на каждый строй, а их дюжина.

    Здесь аппликатура выводится из строя -- и для укулеле с его
    перевёрнутой первой струной получается то же самое до-мажорное
    0003, которое печатают в самоучителях.
    """
    from midi2tab.shapes import shapes_for
    from midi2tab.tuning import TUNINGS, Fretboard

    ukulele = Fretboard(TUNINGS["Укулеле (GCEA)"])
    shape = shapes_for("C", ukulele, 1)[0]
    assert shape.frets == (0, 0, 0, 3)

    drop_d = Fretboard(TUNINGS["Drop D (DADGBE)"])
    assert shapes_for("D", drop_d, 1)[0].frets == (0, 0, 0, 2, 3, 2)


def test_chords_are_not_shifted_by_a_bar():
    """
    Разметка не должна отставать от музыки.

    librosa.util.sync по умолчанию добавляет границы в начале и в конце,
    и столбцов выходит на один больше, чем промежутков: нулевой столбец
    покрывает то, что было ДО первой доли. Из-за этого нулевой столбец
    подписывался именем первого такта, первый -- именем второго, и так
    вся песня. Отсюда сразу два изъяна: аккорды отставали ровно на такт,
    а в начале появлялся аккорд из ниоткуда -- это размечали тишину
    перед первой долей.
    """
    np = pytest.importorskip("numpy")
    librosa = pytest.importorskip("librosa")

    from midi2tab.audiochords import _sync

    # Две доли, между ними три промежутка -- значит, и столбцов три
    chroma = np.zeros((12, 40))
    chroma[0, 0:10] = 1.0      # до вступления
    chroma[2, 10:20] = 1.0
    chroma[5, 20:30] = 1.0
    chroma[7, 30:40] = 1.0
    boundaries = np.array([10, 20, 30, 40])

    synced = _sync(librosa, chroma, boundaries, np)
    assert synced.shape[1] == len(boundaries) - 1

    # Первый столбец -- это то, что звучит ПОСЛЕ первой границы,
    # а не вступление перед ней
    assert int(np.argmax(synced[:, 0])) == 2
    assert int(np.argmax(synced[:, 1])) == 5


def test_confidence_asks_whether_the_notes_are_sounding():
    """
    Уверенность меряет то, что и должна: слышны ли ноты аккорда.

    Прежняя мера -- отрыв от ближайшего соперника -- оказалась шумом: на
    трёх размеченных песнях она помечала сомнительными 55% ВЕРНЫХ
    подписей и ловила при этом ноль процентов настоящих ошибок. До-мажор
    и ля-минор всегда рядом по схожести, но это родство аккордов, а не
    неуверенность.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _confidence

    vectors = np.zeros((1, 12))
    for pitch in (0, 4, 7):                 # до-мажор
        vectors[0, pitch] = 1.0

    full = np.zeros((12, 1))
    for pitch in (0, 4, 7):
        full[pitch, 0] = 1.0
    assert _confidence(np, vectors, full, np.array([0]))[0] == pytest.approx(1.0)

    # Терция почти не звучит -- уверенности быть не в чем, как бы громко
    # ни звучали основной тон с квинтой
    thin = np.zeros((12, 1))
    thin[0, 0] = 1.0
    thin[7, 0] = 1.0
    thin[4, 0] = 0.02
    assert _confidence(np, vectors, thin, np.array([0]))[0] < 0.2


def test_repeated_bars_are_averaged_together():
    """
    Песня ходит по кругу -- значит, одинаковые такты должны разбираться одинаково.

    Разбирая каждое проведение припева поодиночке, мы каждый раз заново
    рискуем ошибиться из-за случайного призвука. Отсюда и брались жалобы,
    что в начале песни аккорд слышится верно, а дальше подменяется
    соседним. Усреднение по повторам гасит этот шум: чтобы сбить разбор,
    призвук должен повториться во всех проведениях сразу.
    """
    np = pytest.importorskip("numpy")
    librosa = pytest.importorskip("librosa")

    from midi2tab.audiochords import _smooth_by_repeats

    # Круг из четырёх тактов, повторённый шесть раз
    circle = np.zeros((12, 4))
    for column, pitch in enumerate((2, 0, 10, 7)):
        circle[pitch, column] = 1.0
    bars = np.tile(circle, 6)

    # В одном такте -- случайный призвук, какого в остальных повторах нет
    spoiled = bars.copy()
    spoiled[5, 10] = 0.9

    smoothed = _smooth_by_repeats(librosa, np, spoiled)
    assert smoothed.shape == bars.shape
    # Призвук ослаб относительно настоящей ноты этого такта
    assert smoothed[5, 10] < smoothed[10, 10]


def test_bass_decides_between_neighbours():
    """
    Основной тон играет бас, и это главный довод при двусмысленности.

    Си-бемоль (A# D F) и фа-мажор (F A C) в плотном миксе почти
    неразличимы: над си-бемолем продолжает звенеть ля от предыдущего
    ре-минора, и оба шаблона подходят одинаково. В басу разница слышна
    сразу. На реальной песне это подняло число верно услышанных
    си-бемолей с 5 до 11 и убрало подмены совсем.
    """
    np = pytest.importorskip("numpy")

    from midi2tab.audiochords import _decide

    vectors = np.zeros((2, 12))
    for pitch in (10, 2, 5):            # A# D F -- си-бемоль
        vectors[0, pitch] = 1.0
    for pitch in (5, 9, 0):             # F A C -- фа-мажор
        vectors[1, pitch] = 1.0
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    # Наверху звучит всё сразу -- шаблоны неразличимы
    muddy = np.zeros((12, 3))
    for pitch in (10, 2, 5, 9, 0):
        muddy[pitch, :] = 1.0

    bass = np.zeros((12, 3))
    bass[10, :] = 1.0                   # в басу си-бемоль
    roots = np.array([10, 5])

    path, _ = _decide(np, vectors, np.zeros(2), muddy, 0.05, bass=bass, roots=roots)
    assert list(path) == [0, 0, 0]

    bass_f = np.zeros((12, 3))
    bass_f[5, :] = 1.0                  # в басу фа
    path, _ = _decide(np, vectors, np.zeros(2), muddy, 0.05, bass=bass_f, roots=roots)
    assert list(path) == [1, 1, 1]


def test_btc_labels_are_translated_to_our_notation():
    """
    Скрипт сравнения с BTC должен говорить на нашем языке подписей.

    Модель пишет "C:min" и бемолями, у нас -- "Cm" и диезы. Тишина
    обозначается буквой N и в разбор попадать не должна вовсе.
    """
    import os
    import sys

    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    from btc_chords import tidy

    assert tidy("C:min") == "Cm"
    assert tidy("Bb") == "A#"
    assert tidy("Db:maj7") == "C#maj7"
    assert tidy("G:min7") == "Gm7"
    assert tidy("D") == "D"
    assert tidy("N") is None
    assert tidy("X") is None
