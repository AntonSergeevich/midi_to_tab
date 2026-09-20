"""
Чистка распознанных нот.

Модель распознавания слышит не только сыгранные ноты, но и их обертоны:
поверх настоящего ми появляется призрачное ми октавой выше, квинта,
дуодецима. На слух это грязь, в табах -- лишние цифры.

Обертон отличим от настоящей ноты по трём признакам сразу: он начинается
почти одновременно с более низкой нотой, отстоит от неё на гармонический
интервал (октава, квинта, большая терция -- то есть 12, 19, 24, 28, 31
полутонов) и тише её. Ни один признак сам по себе не улика -- настоящая
октава в аккорде тоже бывает, -- поэтому решение принимается по
совокупности.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .midiin import NoteEvent

# Интервалы натурального звукоряда, на которых садятся обертоны
HARMONIC_INTERVALS = (12, 19, 24, 28, 31, 36)


@dataclass
class CleanupSettings:
    """Насколько агрессивно чистить."""

    remove_ghosts: bool = True
    ghost_velocity_ratio: float = 0.6   # тише основной ноты во столько раз
    ghost_onset_window: int = 6         # тиков: насколько одновременно
    merge_repeats: bool = True
    merge_gap: int = 3                  # тиков между одинаковыми нотами
    drop_shorter_than: int = 0          # тиков; 0 -- не отбрасывать
    max_polyphony: int = 0              # 0 -- не ограничивать


@dataclass
class CleanupReport:
    removed_ghosts: int = 0
    merged_repeats: int = 0
    removed_short: int = 0
    removed_excess: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def total_removed(self) -> int:
        return self.removed_ghosts + self.removed_short + self.removed_excess


def _is_ghost(note: NoteEvent, base: NoteEvent, cfg: CleanupSettings) -> bool:
    """Похожа ли нота на обертон более низкой ноты."""
    if note.pitch <= base.pitch:
        return False
    if abs(note.onset - base.onset) > cfg.ghost_onset_window:
        return False
    if (note.pitch - base.pitch) not in HARMONIC_INTERVALS:
        return False
    return note.velocity < base.velocity * cfg.ghost_velocity_ratio


def clean(
    notes: list[NoteEvent], cfg: CleanupSettings | None = None
) -> tuple[list[NoteEvent], CleanupReport]:
    """Убрать из распознанного то, чего гитарист не играл."""
    cfg = cfg or CleanupSettings()
    report = CleanupReport()
    if not notes:
        return [], report

    result = sorted(notes, key=lambda n: (n.onset, n.pitch))

    # 1. Призрачные обертоны
    if cfg.remove_ghosts:
        keep: list[NoteEvent] = []
        for i, note in enumerate(result):
            ghost = False
            for j in range(i - 1, -1, -1):
                base = result[j]
                if note.onset - base.onset > cfg.ghost_onset_window:
                    break
                if _is_ghost(note, base, cfg):
                    ghost = True
                    break
            if ghost:
                report.removed_ghosts += 1
            else:
                keep.append(note)
        result = keep

    # 2. Слипшиеся повторы одной и той же ноты
    if cfg.merge_repeats:
        by_pitch: dict[int, list[NoteEvent]] = {}
        for note in result:
            by_pitch.setdefault(note.pitch, []).append(note)
        merged: list[NoteEvent] = []
        for pitch, group in by_pitch.items():
            group.sort(key=lambda n: n.onset)
            current = group[0]
            for nxt in group[1:]:
                if nxt.onset - current.end <= cfg.merge_gap:
                    current = NoteEvent(
                        current.onset,
                        max(current.duration, nxt.end - current.onset),
                        pitch,
                        max(current.velocity, nxt.velocity),
                    )
                    report.merged_repeats += 1
                else:
                    merged.append(current)
                    current = nxt
            merged.append(current)
        result = sorted(merged, key=lambda n: (n.onset, n.pitch))

    # 3. Слишком короткие обрывки
    if cfg.drop_shorter_than > 0:
        before = len(result)
        result = [n for n in result if n.duration >= cfg.drop_shorter_than]
        report.removed_short = before - len(result)

    # 4. Ограничение одновременных голосов: оставляем самые громкие
    if cfg.max_polyphony > 0:
        by_onset: dict[int, list[NoteEvent]] = {}
        for note in result:
            by_onset.setdefault(note.onset // max(1, cfg.ghost_onset_window), []).append(note)
        trimmed: list[NoteEvent] = []
        for group in by_onset.values():
            if len(group) > cfg.max_polyphony:
                group.sort(key=lambda n: -n.velocity)
                report.removed_excess += len(group) - cfg.max_polyphony
                group = group[: cfg.max_polyphony]
            trimmed.extend(group)
        result = sorted(trimmed, key=lambda n: (n.onset, n.pitch))

    if report.removed_ghosts:
        report.messages.append(
            f"Убрано призрачных обертонов: {report.removed_ghosts}"
        )
    if report.merged_repeats:
        report.messages.append(f"Склеено разорванных нот: {report.merged_repeats}")
    if report.removed_short:
        report.messages.append(f"Отброшено коротких обрывков: {report.removed_short}")
    if report.removed_excess:
        report.messages.append(f"Убрано лишних одновременных голосов: {report.removed_excess}")
    return result, report
