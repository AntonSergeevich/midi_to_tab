"""Строи и гриф."""

from __future__ import annotations

from dataclasses import dataclass, field

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pitch_name(pitch: int) -> str:
    """MIDI-номер -> название ноты, например 40 -> 'E2'."""
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


@dataclass(frozen=True)
class Tuning:
    """Строй. open_pitches идут от самой низкой струны к самой высокой."""

    name: str
    open_pitches: tuple[int, ...]

    @property
    def string_count(self) -> int:
        return len(self.open_pitches)

    def describe(self) -> str:
        return " ".join(pitch_name(p) for p in self.open_pitches)


TUNINGS: dict[str, Tuning] = {
    t.name: t
    for t in (
        Tuning("Стандартный (EADGBE)", (40, 45, 50, 55, 59, 64)),
        Tuning("Drop D (DADGBE)", (38, 45, 50, 55, 59, 64)),
        Tuning("Полтона вниз (Eb)", (39, 44, 49, 54, 58, 63)),
        Tuning("Тон вниз (D)", (38, 43, 48, 53, 57, 62)),
        Tuning("Drop C (CGCFAD)", (36, 43, 48, 53, 57, 62)),
        Tuning("DADGAD", (38, 45, 50, 55, 57, 62)),
        Tuning("Open G (DGDGBD)", (38, 43, 50, 55, 59, 62)),
        Tuning("Open D (DADF#AD)", (38, 45, 50, 54, 57, 62)),
        Tuning("7 струн (BEADGBE)", (35, 40, 45, 50, 55, 59, 64)),
        Tuning("Бас 4 струны (EADG)", (28, 33, 38, 43)),
        Tuning("Бас 5 струн (BEADG)", (23, 28, 33, 38, 43)),
        Tuning("Укулеле (GCEA)", (67, 60, 64, 69)),
    )
}
DEFAULT_TUNING = "Стандартный (EADGBE)"


@dataclass
class Fretboard:
    """
    Гриф: строй + каподастр + предел по ладам.

    Каподастр поднимает высоту всех открытых струн; лады нумеруются
    от каподастра, как их и пишут в табах.
    """

    tuning: Tuning
    capo: int = 0
    max_fret: int = 17
    _options: dict[int, tuple[tuple[int, int], ...]] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def string_count(self) -> int:
        return self.tuning.string_count

    def open_pitch(self, string_idx: int) -> int:
        """Высота открытой струны с учётом каподастра. 0 = самая низкая струна."""
        return self.tuning.open_pitches[string_idx] + self.capo

    @property
    def lowest_pitch(self) -> int:
        return min(self.open_pitch(s) for s in range(self.string_count))

    @property
    def highest_pitch(self) -> int:
        return max(self.open_pitch(s) + self.max_fret for s in range(self.string_count))

    def options(self, pitch: int) -> tuple[tuple[int, int], ...]:
        """Все позиции (струна, лад), дающие эту ноту."""
        cached = self._options.get(pitch)
        if cached is not None:
            return cached
        found = []
        for s in range(self.string_count):
            fret = pitch - self.open_pitch(s)
            if 0 <= fret <= self.max_fret:
                found.append((s, fret))
        result = tuple(found)
        self._options[pitch] = result
        return result

    def playable(self, pitch: int) -> bool:
        return bool(self.options(pitch))

    def describe(self) -> str:
        text = f"{self.tuning.name} [{self.tuning.describe()}]"
        if self.capo:
            text += f", каподастр на {self.capo} ладу"
        return text
