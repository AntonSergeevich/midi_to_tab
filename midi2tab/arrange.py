"""
Расстановка нот по грифу.

Исходный скрипт выбирал позицию для каждой ноты независимо -- отсюда
соседние ноты арпеджио на одной струне и прыжки руки через весь гриф.

Здесь задача решается целиком: для каждого созвучия строится набор
вариантов аппликатуры, а затем алгоритм Витерби (динамическое
программирование) выбирает такую цепочку вариантов по всей пьесе,
которая минимизирует суммарную стоимость -- перемещения руки, растяжки
и повторные удары по одной струне.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .midiin import NoteEvent
from .timing import TPQ
from .tuning import Fretboard

# Веса стоимости. Подобраны так, чтобы рука держалась в позиции,
# а арпеджио не сваливалось на одну струну.
W_MOVE = 2.5          # смещение руки на лад
W_SAME_STRING = 14.0  # повторный удар по той же струне (глушит предыдущую)
W_STRETCH = 3.0       # растяжка внутри аккорда
W_HIGH = 1.6          # уход вверх по грифу
W_OPEN_BONUS = -1.2   # открытая струна удобна
REFERENCE_GAP = 24    # восьмая: эталон "обычной" скорости смены позиции
MAX_CANDIDATES = 24   # вариантов аппликатуры на созвучие
BEAM = 24             # ширина луча в ДП


@dataclass
class Chord:
    """Созвучие: ноты с общим (в пределах допуска) моментом атаки."""

    onset: int
    notes: list[NoteEvent]

    @property
    def duration(self) -> int:
        return max(n.duration for n in self.notes)

    @property
    def pitches(self) -> list[int]:
        return [n.pitch for n in self.notes]


@dataclass
class Placement:
    """Готовая аппликатура созвучия."""

    chord: Chord
    positions: list[tuple[int, int] | None]  # (струна, лад) по порядку нот

    @property
    def played(self) -> list[tuple[int, int, int]]:
        """Реально звучащие ноты: (струна, лад, громкость)."""
        out = []
        for note, pos in zip(self.chord.notes, self.positions):
            if pos is not None:
                out.append((pos[0], pos[1], note.velocity))
        return out


@dataclass
class ArrangeReport:
    """Что пришлось изменить -- показывается пользователю, ничего не молчит."""

    transposed_semitones: int = 0
    dropped_out_of_range: int = 0
    dropped_excess_voices: int = 0
    folded_into_range: int = 0
    total_notes: int = 0
    chords: int = 0
    max_fret_used: int = 0
    messages: list[str] = field(default_factory=list)


def group_chords(notes: list[NoteEvent], tolerance: int = 3) -> list[Chord]:
    """Собрать ноты с близкими атаками в созвучия."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.onset, n.pitch))
    chords: list[Chord] = []
    bucket = [ordered[0]]
    start = ordered[0].onset
    for note in ordered[1:]:
        if note.onset - start <= tolerance:
            bucket.append(note)
        else:
            chords.append(Chord(onset=start, notes=bucket))
            bucket = [note]
            start = note.onset
    chords.append(Chord(onset=start, notes=bucket))
    return chords


def best_transposition(pitches: list[int], board: Fretboard) -> int:
    """Подобрать сдвиг в октавах, при котором в диапазон попадает больше нот."""
    if not pitches:
        return 0
    best_shift, best_fit = 0, -1
    for octaves in range(-3, 4):
        shift = octaves * 12
        fit = sum(1 for p in pitches if board.playable(p + shift))
        # при равенстве предпочитаем меньший сдвиг
        if fit > best_fit or (fit == best_fit and abs(shift) < abs(best_shift)):
            best_shift, best_fit = shift, fit
    return best_shift


def _thin_voices(pitches: list[int], limit: int) -> list[int]:
    """
    Если нот больше, чем струн, оставить мелодию и бас.

    Верхний голос несёт мелодию, нижний -- гармоническую опору;
    режутся средние голоса, они на гитаре и так наименее слышны.
    """
    if len(pitches) <= limit:
        return list(range(len(pitches)))
    order = sorted(range(len(pitches)), key=lambda i: pitches[i])
    keep = {order[0], order[-1]}
    for idx in reversed(order[1:-1]):  # сверху вниз
        if len(keep) >= limit:
            break
        keep.add(idx)
    return sorted(keep)


def _candidates(pitches: list[int], board: Fretboard, max_stretch: int) -> list[tuple]:
    """Варианты аппликатуры созвучия, отсортированные по внутреннему удобству."""
    per_note = [board.options(p) for p in pitches]
    if any(not opts for opts in per_note):
        per_note = [opts if opts else ((-1, -1),) for opts in per_note]

    scored: list[tuple[float, tuple]] = []
    total = 1
    for opts in per_note:
        total *= len(opts)
        if total > 20000:  # слишком густое созвучие -- идём жадно
            return [_greedy_candidate(pitches, board)]

    for combo in product(*per_note):
        used = [s for s, _ in combo if s >= 0]
        if len(used) != len(set(used)):
            continue  # две ноты на одной струне одновременно не сыграть
        frets = [f for s, f in combo if s >= 0 and f > 0]
        if frets and max(frets) - min(frets) > max_stretch:
            continue
        cost = 0.0
        if frets:
            cost += W_STRETCH * (max(frets) - min(frets))
            cost += W_HIGH * (sum(frets) / len(frets))
        cost += W_OPEN_BONUS * sum(1 for s, f in combo if s >= 0 and f == 0)
        scored.append((cost, combo))

    if not scored:
        return [_greedy_candidate(pitches, board)]
    scored.sort(key=lambda x: x[0])
    return [combo for _, combo in scored[:MAX_CANDIDATES]]


def _greedy_candidate(pitches: list[int], board: Fretboard) -> tuple:
    """Запасной вариант для очень густых созвучий."""
    order = sorted(range(len(pitches)), key=lambda i: -pitches[i])
    result: list[tuple[int, int]] = [(-1, -1)] * len(pitches)
    busy: set[int] = set()
    for idx in order:
        free = [(s, f) for s, f in board.options(pitches[idx]) if s not in busy]
        if free:
            free.sort(key=lambda x: x[1])
            result[idx] = free[0]
            busy.add(free[0][0])
    return tuple(result)


def _hand_position(combo: tuple) -> int | None:
    """Позиция руки = самый низкий зажатый лад (открытые струны не считаются)."""
    frets = [f for s, f in combo if s >= 0 and f > 0]
    return min(frets) if frets else None


def _speed_factor(gap: int) -> float:
    """
    Насколько срочен перенос руки.

    Во время длинной ноты рука успевает переставиться почти бесплатно,
    а в быстром пассаже тот же скачок практически неиграем.
    """
    if gap <= 0:
        return 3.0
    return min(3.0, max(0.25, REFERENCE_GAP / gap))


def _transition_cost(prev: tuple, cur: tuple, anchor: int | None, gap: int) -> float:
    """Стоимость перехода между двумя аппликатурами."""
    cost = 0.0
    pos_cur = _hand_position(cur)
    if pos_cur is not None and anchor is not None:
        cost += W_MOVE * abs(pos_cur - anchor) * _speed_factor(gap)

    prev_strings = {s for s, _ in prev if s >= 0}
    cur_strings = {s for s, _ in cur if s >= 0}
    repeats = len(prev_strings & cur_strings)
    if repeats:
        # чем быстрее повтор, тем сильнее он рвёт звучание арпеджио
        weight = W_SAME_STRING if gap <= TPQ // 2 else W_SAME_STRING * 0.35
        cost += weight * repeats
    return cost


def arrange(
    notes: list[NoteEvent],
    board: Fretboard,
    *,
    transpose: int = 0,
    auto_transpose: bool = True,
    fold_octaves: bool = False,
    max_stretch: int = 5,
    chord_tolerance: int = 3,
) -> tuple[list[Placement], ArrangeReport]:
    """Разложить ноты по грифу целиком, минимизируя усилия левой руки."""
    report = ArrangeReport(total_notes=len(notes))
    if not notes:
        report.messages.append("В выбранной дорожке нет нот.")
        return [], report

    shift = transpose
    if auto_transpose:
        shift += best_transposition([n.pitch + transpose for n in notes], board)
    report.transposed_semitones = shift
    if shift:
        report.messages.append(
            f"Партия транспонирована на {shift:+d} полутонов, чтобы попасть в диапазон инструмента."
        )

    working: list[NoteEvent] = []
    for n in notes:
        pitch = n.pitch + shift
        if not board.playable(pitch) and fold_octaves:
            for octaves in (1, -1, 2, -2, 3, -3):
                if board.playable(pitch + octaves * 12):
                    pitch += octaves * 12
                    report.folded_into_range += 1
                    break
        working.append(NoteEvent(n.onset, n.duration, pitch, n.velocity))

    if report.folded_into_range:
        report.messages.append(
            f"{report.folded_into_range} нот перенесено на октаву, чтобы уместиться на грифе."
        )

    chords = group_chords(working, tolerance=chord_tolerance)
    report.chords = len(chords)

    # Подготовка: отсев непригодных нот и лишних голосов
    prepared: list[tuple[Chord, list[int]]] = []
    for ch in chords:
        alive = [i for i, n in enumerate(ch.notes) if board.playable(n.pitch)]
        report.dropped_out_of_range += len(ch.notes) - len(alive)
        if len(alive) > board.string_count:
            kept_rel = _thin_voices([ch.notes[i].pitch for i in alive], board.string_count)
            report.dropped_excess_voices += len(alive) - len(kept_rel)
            alive = [alive[i] for i in kept_rel]
        prepared.append((ch, alive))

    if report.dropped_out_of_range:
        report.messages.append(
            f"{report.dropped_out_of_range} нот вне диапазона инструмента -- не попали в табы."
        )
    if report.dropped_excess_voices:
        report.messages.append(
            f"{report.dropped_excess_voices} нот убрано из слишком густых аккордов "
            f"(на {board.string_count} струнах больше не сыграть)."
        )

    # Витерби по созвучиям
    back: list[list[tuple[tuple, int | None, int]]] = []
    placements: list[Placement] = []

    prev_layer: list[tuple[tuple, float, int | None, int]] = []
    prev_onset = None

    for step, (ch, alive) in enumerate(prepared):
        pitches = [ch.notes[i].pitch for i in alive]
        if not pitches:
            back.append([])
            continue

        cands = _candidates(pitches, board, max_stretch)
        gap = 0 if prev_onset is None else ch.onset - prev_onset

        layer: list[tuple[tuple, float, int | None, int]] = []
        for combo in cands:
            inner = 0.0
            frets = [f for s, f in combo if s >= 0 and f > 0]
            if frets:
                inner += W_STRETCH * (max(frets) - min(frets)) + W_HIGH * (sum(frets) / len(frets))
            inner += W_OPEN_BONUS * sum(1 for s, f in combo if s >= 0 and f == 0)

            if not prev_layer:
                layer.append((combo, inner, _hand_position(combo), -1))
                continue

            best_cost, best_idx, best_anchor = float("inf"), 0, None
            for idx, (pcombo, pcost, panchor, _) in enumerate(prev_layer):
                cost = pcost + inner + _transition_cost(pcombo, combo, panchor, gap)
                if cost < best_cost:
                    best_cost, best_idx, best_anchor = cost, idx, panchor
            own = _hand_position(combo)
            layer.append((combo, best_cost, own if own is not None else best_anchor, best_idx))

        layer.sort(key=lambda x: x[1])
        layer = layer[:BEAM]
        back.append([(c, a, prev) for c, _, a, prev in layer])
        prev_layer = layer
        prev_onset = ch.onset

    # Обратный проход
    chosen: list[tuple | None] = [None] * len(prepared)
    if prev_layer:
        idx = 0
        for step in range(len(back) - 1, -1, -1):
            if not back[step]:
                chosen[step] = None
                continue
            idx = min(idx, len(back[step]) - 1)
            combo, _, prev_idx = back[step][idx]
            chosen[step] = combo
            idx = max(0, prev_idx)

    for (ch, alive), combo in zip(prepared, chosen):
        positions: list[tuple[int, int] | None] = [None] * len(ch.notes)
        if combo is not None:
            for slot, note_idx in enumerate(alive):
                s, f = combo[slot]
                if s >= 0:
                    positions[note_idx] = (s, f)
                    report.max_fret_used = max(report.max_fret_used, f)
        placements.append(Placement(chord=ch, positions=positions))

    return placements, report
