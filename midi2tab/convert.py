"""Конвейер целиком: аудио-стем или MIDI на входе -- табы на выходе."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import arrange, asciiout, audioin, gp5out, midiin
from .timing import DEFAULT_GRID, GRIDS
from .tuning import DEFAULT_TUNING, TUNINGS, Fretboard, pitch_name


@dataclass
class Settings:
    """Всё, что настраивает пользователь."""

    input_path: str = ""
    output_dir: str = ""
    tracks: list[int] = field(default_factory=list)  # пусто -- взять все со звуком

    tuning: str = DEFAULT_TUNING
    capo: int = 0
    max_fret: int = 17
    max_stretch: int = 5

    transpose: int = 0
    auto_transpose: bool = True
    fold_octaves: bool = True

    grid: str = DEFAULT_GRID
    allow_triplets: bool = True
    let_ring: bool = False
    tempo: int = 0  # 0 -- взять из файла

    make_gp5: bool = True
    make_txt: bool = True
    keep_midi: bool = True  # для аудио-входа: сохранить распознанный MIDI

    # распознавание аудио
    onset_threshold: float = 0.5
    frame_threshold: float = 0.3
    min_note_ms: float = 90.0
    limit_to_range: bool = True

    def fretboard(self) -> Fretboard:
        return Fretboard(
            tuning=TUNINGS.get(self.tuning, TUNINGS[DEFAULT_TUNING]),
            capo=self.capo,
            max_fret=self.max_fret,
        )

    def grid_ticks(self) -> int:
        return GRIDS.get(self.grid, GRIDS[DEFAULT_GRID])


@dataclass
class Result:
    """Что получилось."""

    gp5_path: str = ""
    txt_path: str = ""
    midi_path: str = ""
    tab_text: str = ""
    report: arrange.ArrangeReport | None = None
    summary: list[str] = field(default_factory=list)


def _noop(_message: str) -> None:
    pass


def convert(settings: Settings, progress=None) -> Result:
    """Выполнить преобразование. progress(текст) вызывается по ходу работы."""
    say = progress or _noop
    src = settings.input_path
    if not src or not os.path.isfile(src):
        raise FileNotFoundError("Файл не выбран или не найден.")

    out_dir = settings.output_dir or os.path.dirname(os.path.abspath(src))
    os.makedirs(out_dir, exist_ok=True)
    stem = Path(src).stem
    board = settings.fretboard()
    result = Result()

    # 1. Аудио -> MIDI
    midi_path = src
    if audioin.is_audio(src):
        say("Распознаю ноты из аудио...")
        cfg = audioin.TranscribeSettings(
            onset_threshold=settings.onset_threshold,
            frame_threshold=settings.frame_threshold,
            min_note_ms=settings.min_note_ms,
            min_pitch=board.lowest_pitch if settings.limit_to_range else None,
            max_pitch=board.highest_pitch if settings.limit_to_range else None,
        )
        midi_path = os.path.join(out_dir, f"{stem}.mid")
        midi_path, note_count = audioin.transcribe(src, midi_path, cfg, progress=say)
        result.midi_path = midi_path
        result.summary.append(f"Из аудио распознано нот: {note_count}")
        if settings.limit_to_range:
            result.summary.append(
                f"Поиск ограничен диапазоном инструмента "
                f"{pitch_name(board.lowest_pitch)}-{pitch_name(board.highest_pitch)}"
            )

    # 2. Чтение MIDI
    say("Читаю MIDI...")
    doc = midiin.load(midi_path)
    indices = settings.tracks or [t.index for t in doc.tracks if t.note_count and not t.is_drum]
    if not indices:
        raise ValueError("В файле нет ни одной дорожки с нотами (ударные не считаются).")
    notes = doc.merged_notes(indices)
    if not notes:
        raise ValueError("В выбранных дорожках нет нот.")

    # 3. Раскладка по грифу
    say("Раскладываю по грифу...")
    placements, report = arrange.arrange(
        notes,
        board,
        transpose=settings.transpose,
        auto_transpose=settings.auto_transpose,
        fold_octaves=settings.fold_octaves,
        max_stretch=settings.max_stretch,
    )
    result.report = report

    grid = settings.grid_ticks()
    spans = gp5out.build_spans(placements, grid)
    if not spans:
        raise ValueError("После раскладки не осталось ни одной играбельной ноты.")

    total = max(s.end for s in spans)
    plan = gp5out.measure_plan(doc.time_signatures, total)
    tempo = settings.tempo or int(round(doc.tempo_bpm))

    # 4. Сохранение
    if settings.make_gp5:
        say("Собираю файл Guitar Pro...")
        result.gp5_path = gp5out.write_gp5(
            placements,
            board,
            os.path.join(out_dir, f"{stem}.gp5"),
            title=stem,
            tempo=tempo,
            time_signatures=doc.time_signatures,
            grid=grid,
            allow_triplets=settings.allow_triplets,
            let_ring=settings.let_ring,
        )

    header = [
        f"Строй: {board.describe()}",
        f"Темп: {tempo} | Размер: {plan[0][2]}/{plan[0][3]} | Тактов: {len(plan)}",
        f"Нот: {report.total_notes} | Созвучий: {report.chords} | Верхний лад: {report.max_fret_used}",
    ]
    result.tab_text = asciiout.render(
        spans, board, plan, grid, title=stem, header_lines=header
    )
    if settings.make_txt:
        result.txt_path = os.path.join(out_dir, f"{stem}.txt")
        Path(result.txt_path).write_text(result.tab_text, encoding="utf-8")

    if result.midi_path and not settings.keep_midi:
        try:
            os.remove(result.midi_path)
            result.midi_path = ""
        except OSError:
            pass

    result.summary.extend(report.messages)
    result.summary.append(
        f"Готово: {len(plan)} тактов, верхний лад {report.max_fret_used}, темп {tempo}"
    )
    say("Готово.")
    return result
