"""
Чтение MIDI в музыкальное время.

В отличие от наивного подхода "секунды / (60/BPM)", здесь позиции нот
переводятся в доли через тиковую шкалу самого файла, поэтому смена темпа
внутри пьесы не сдвигает ноты. Размер такта тоже берётся из файла, а не
предполагается 4/4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pretty_midi

from .timing import TPQ
from .tuning import pitch_name


@dataclass
class NoteEvent:
    """Нота в тиках движка."""

    onset: int
    duration: int
    pitch: int
    velocity: int

    @property
    def end(self) -> int:
        return self.onset + self.duration


@dataclass
class TrackInfo:
    """Сводка по одной дорожке MIDI -- то, что показывается в списке."""

    index: int
    name: str
    program: int
    is_drum: bool
    notes: list[NoteEvent] = field(default_factory=list)

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def pitch_range(self) -> tuple[int, int]:
        if not self.notes:
            return (0, 0)
        pitches = [n.pitch for n in self.notes]
        return (min(pitches), max(pitches))

    @property
    def polyphony(self) -> int:
        """Максимум одновременно звучащих нот."""
        if not self.notes:
            return 0
        edges: list[tuple[int, int]] = []
        for n in self.notes:
            edges.append((n.onset, 1))
            edges.append((n.end, -1))
        edges.sort()
        cur = peak = 0
        for _, delta in edges:
            cur += delta
            peak = max(peak, cur)
        return peak

    def label(self) -> str:
        if not self.notes:
            return f"[{self.index}] {self.name} -- пусто"
        lo, hi = self.pitch_range
        kind = "УДАРНЫЕ" if self.is_drum else pretty_midi.program_to_instrument_name(self.program)
        return (
            f"[{self.index}] {self.name or kind} | нот: {self.note_count} | "
            f"{pitch_name(lo)}-{pitch_name(hi)} | полифония: {self.polyphony}"
        )


@dataclass
class MidiDocument:
    """Разобранный MIDI-файл."""

    path: str
    tracks: list[TrackInfo]
    tempo_bpm: float
    time_signatures: list[tuple[int, int, int]]  # (тик начала, числитель, знаменатель)
    total_ticks: int

    def track(self, index: int) -> TrackInfo:
        return self.tracks[index]

    def merged_notes(self, indices: list[int]) -> list[NoteEvent]:
        notes: list[NoteEvent] = []
        for i in indices:
            notes.extend(self.tracks[i].notes)
        notes.sort(key=lambda n: (n.onset, n.pitch))
        return notes


def _seconds_to_ticks(pm: pretty_midi.PrettyMIDI, seconds: float) -> int:
    """Секунды -> тики движка через тиковую шкалу файла (учитывает смену темпа)."""
    return int(round(pm.time_to_tick(seconds) / pm.resolution * TPQ))


def load(path: str) -> MidiDocument:
    pm = pretty_midi.PrettyMIDI(path)

    tracks: list[TrackInfo] = []
    for i, inst in enumerate(pm.instruments):
        notes = []
        for n in sorted(inst.notes, key=lambda x: (x.start, x.pitch)):
            onset = _seconds_to_ticks(pm, n.start)
            end = _seconds_to_ticks(pm, n.end)
            notes.append(
                NoteEvent(
                    onset=onset,
                    duration=max(1, end - onset),
                    pitch=n.pitch,
                    velocity=n.velocity,
                )
            )
        tracks.append(
            TrackInfo(
                index=i,
                name=(inst.name or "").strip(),
                program=inst.program,
                is_drum=inst.is_drum,
                notes=notes,
            )
        )

    tempos = pm.get_tempo_changes()[1]
    tempo_bpm = float(tempos[0]) if len(tempos) else 120.0

    sigs: list[tuple[int, int, int]] = []
    for ts in pm.time_signature_changes:
        sigs.append((_seconds_to_ticks(pm, ts.time), ts.numerator, ts.denominator))
    if not sigs or sigs[0][0] > 0:
        sigs.insert(0, (0, 4, 4))
    sigs.sort()

    total = 0
    for tr in tracks:
        for n in tr.notes:
            total = max(total, n.end)

    return MidiDocument(
        path=path,
        tracks=tracks,
        tempo_bpm=tempo_bpm,
        time_signatures=sigs,
        total_ticks=total,
    )
