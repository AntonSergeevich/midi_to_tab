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
