"""
Определение аккордов.

Для бегущей строки в плеере нужны не отдельные ноты, а имена аккордов с
привязкой ко времени. Задача решается по классам высот: время режется на
окна, в каждом окне считается, сколько звучала каждая из двенадцати нот
(с учётом длительности и громкости), и полученный профиль сравнивается с
шаблонами аккордов.

Вес по длительности важен: проходящая шестнадцатая не должна перебивать
целую ноту, на которой аккорд и держится.
"""

from __future__ import annotations

from dataclasses import dataclass

from .midiin import NoteEvent
from .timing import TPQ

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Шаблоны: подпись -> интервалы от основного тона.
# Порядок важен: при равном совпадении выигрывает тот, что выше в списке,
# поэтому простые трезвучия стоят раньше сложных надстроек.
TEMPLATES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("", (0, 4, 7)),
    ("m", (0, 3, 7)),
    ("5", (0, 7)),
    ("7", (0, 4, 7, 10)),
    ("m7", (0, 3, 7, 10)),
    ("maj7", (0, 4, 7, 11)),
    ("6", (0, 4, 7, 9)),
    ("m6", (0, 3, 7, 9)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
    ("dim", (0, 3, 6)),
    ("m7b5", (0, 3, 6, 10)),
    ("dim7", (0, 3, 6, 9)),
    ("aug", (0, 4, 8)),
    ("add9", (0, 2, 4, 7)),
    ("9", (0, 2, 4, 7, 10)),
    ("m9", (0, 2, 3, 7, 10)),
)

# Нота вне шаблона штрафуется слабее, чем поощряется попадание:
# живая музыка полна проходящих и украшающих тонов.
MISS_PENALTY = 0.55
MIN_CONFIDENCE = 0.42
# Чем сложнее шаблон, тем охотнее он "объясняет" случайные проходящие ноты.
# Небольшой штраф за размер заставляет выбирать простое трезвучие, если
# надстройка звучала мельком: в плеере полезнее "C", чем "Cadd9" из-за
# мимолётного ре в мелодии.
SIZE_PENALTY = 0.035
# Одни и те же ноты часто читаются двояко (ля-ре-ми это и Asus4, и Dsus2).
# Решает бас: аккорд называют по той ноте, что лежит внизу.
BASS_BONUS = 0.06


@dataclass
class ChordSpan:
    """Аккорд на отрезке времени."""

    start: int          # тики
    end: int
    name: str           # например "Am" или "C/G"
    root: int           # класс высоты основного тона, 0 = до
    quality: str        # "", "m", "7", ...
    bass: int | None    # класс высоты баса, если он не основной тон
    confidence: float

    @property
    def duration(self) -> int:
        return self.end - self.start

    def seconds(self, tempo: int) -> tuple[float, float]:
        """Границы отрезка в секундах -- для синхронизации с плеером."""
        factor = 60.0 / max(1, tempo) / TPQ
        return self.start * factor, self.end * factor


def _profile(notes: list[NoteEvent], start: int, end: int) -> tuple[list[float], int | None]:
    """
    Сколько звучал каждый класс высоты в окне и какая нота была самой низкой.

    Возвращает (веса по двенадцати классам, класс высоты баса).
    """
    weights = [0.0] * 12
    lowest_pitch: int | None = None
    lowest_weight = 0.0
    for note in notes:
        overlap = min(note.end, end) - max(note.onset, start)
        if overlap <= 0:
            continue
        weight = overlap * (0.35 + 0.65 * note.velocity / 127.0)
        weights[note.pitch % 12] += weight
        if lowest_pitch is None or note.pitch < lowest_pitch:
            lowest_pitch, lowest_weight = note.pitch, weight
    if lowest_pitch is None:
        return weights, None
    # бас учитываем, только если он звучал заметно, а не мелькнул
    total = sum(weights)
    if total and lowest_weight / total < 0.12:
        return weights, None
    return weights, lowest_pitch % 12


def match(weights: list[float], bass: int | None = None) -> tuple[str, int, float]:
    """Подобрать аккорд к профилю. Возвращает (подпись, основной тон, уверенность)."""
    total = sum(weights)
    if total <= 0:
        return "", -1, 0.0

    best = ("", -1, 0.0)
    for root in range(12):
        for quality, intervals in TEMPLATES:
            members = {(root + i) % 12 for i in intervals}
            hit = sum(weights[p] for p in members)
            miss = total - hit
            # нота шаблона, которая не прозвучала, тоже ослабляет догадку
            silent = sum(1 for p in members if weights[p] <= total * 0.02)
            score = (hit - MISS_PENALTY * miss) / total
            score -= 0.12 * silent
            score -= SIZE_PENALTY * len(intervals)
            if bass is not None and root == bass:
                score += BASS_BONUS
            if score > best[2]:
                best = (quality, root, score)
    return best


def name_of(root: int, quality: str, bass: int | None) -> str:
    """Собрать подпись аккорда, при необходимости с басом через дробь."""
    if root < 0:
        return ""
    text = f"{PITCH_CLASSES[root]}{quality}"
    if bass is not None and bass != root:
        text += f"/{PITCH_CLASSES[bass]}"
    return text


def detect(
    notes: list[NoteEvent],
    *,
    window: int = TPQ * 2,
    min_length: int = TPQ,
    total_ticks: int | None = None,
) -> list[ChordSpan]:
    """
    Разметить пьесу аккордами.

    window -- шаг анализа (по умолчанию половина такта 4/4),
    min_length -- короче этого аккорды сливаются с соседними.
    """
    if not notes:
        return []

    end_tick = total_ticks or max(n.end for n in notes)
    ordered = sorted(notes, key=lambda n: n.onset)

    raw: list[ChordSpan] = []
    position = 0
    while position < end_tick:
        stop = min(position + window, end_tick)
        weights, bass = _profile(ordered, position, stop)
        quality, root, confidence = match(weights, bass)
        if root >= 0 and confidence >= MIN_CONFIDENCE:
            raw.append(
                ChordSpan(
                    start=position,
                    end=stop,
                    name=name_of(root, quality, bass),
                    root=root,
                    quality=quality,
                    bass=bass if bass != root else None,
                    confidence=confidence,
                )
            )
        position = stop

    # Склейка одинаковых соседей
    merged: list[ChordSpan] = []
    for span in raw:
        if merged and merged[-1].name == span.name and merged[-1].end == span.start:
            merged[-1].end = span.end
            merged[-1].confidence = max(merged[-1].confidence, span.confidence)
        else:
            merged.append(span)

    # Слишком короткие обрывки присоединяем к более уверенному соседу
    cleaned: list[ChordSpan] = []
    for span in merged:
        if span.duration < min_length and cleaned:
            cleaned[-1].end = span.end
        else:
            cleaned.append(span)
    return cleaned
