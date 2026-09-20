"""
Музыкальное время в целых числах.

Всё внутри движка измеряется в тиках: TPQ тиков на одну четверть.
48 выбрано так, чтобы в целые тики укладывались и обычные длительности,
и триоли: 64-я = 3, 32-я = 6, триольная 8-я = 16, 8-я = 24, четверть = 48.

Целые числа вместо float убирают накопление ошибки округления --
именно из-за неё в исходном скрипте такты "не сходились" и он сваливался
в аварийный режим, перемалывающий всю музыку в шестнадцатые.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

TPQ = 48  # тиков в четверти


@dataclass(frozen=True)
class DurationSpec:
    """Одна длительность в терминах Guitar Pro."""

    ticks: int
    value: int  # 1=целая, 2=половина, 4=четверть, 8, 16, 32, 64
    dotted: bool = False
    tuplet: bool = False  # триоль 3:2

    @property
    def is_triplet(self) -> bool:
        return self.tuplet


# От большего к меньшему -- порядок важен для жадного разбиения.
DURATIONS: tuple[DurationSpec, ...] = (
    DurationSpec(192, 1),
    DurationSpec(144, 2, dotted=True),
    DurationSpec(96, 2),
    DurationSpec(72, 4, dotted=True),
    DurationSpec(64, 2, tuplet=True),
    DurationSpec(48, 4),
    DurationSpec(36, 8, dotted=True),
    DurationSpec(32, 4, tuplet=True),
    DurationSpec(24, 8),
    DurationSpec(18, 16, dotted=True),
    DurationSpec(16, 8, tuplet=True),
    DurationSpec(12, 16),
    DurationSpec(9, 32, dotted=True),
    DurationSpec(8, 16, tuplet=True),
    DurationSpec(6, 32),
    DurationSpec(4, 32, tuplet=True),
    DurationSpec(3, 64),
)

PLAIN_DURATIONS = tuple(d for d in DURATIONS if not d.tuplet)

# Сетки квантизации: подпись -> тиков в шаге сетки
GRIDS: dict[str, int] = {
    "1/4 (четверти)": 48,
    "1/8 (восьмые)": 24,
    "1/8 триоли": 16,
    "1/16 (шестнадцатые)": 12,
    "1/16 триоли": 8,
    "1/32 (тридцать вторые)": 6,
    "без квантизации": 3,
}
DEFAULT_GRID = "1/16 (шестнадцатые)"


def quantize(ticks: int, grid: int) -> int:
    """Притянуть момент времени к ближайшему узлу сетки."""
    if grid <= 1:
        return int(round(ticks))
    return int(round(ticks / grid)) * grid


def decompose(ticks: int, allow_triplets: bool = True) -> list[DurationSpec]:
    """
    Разложить длительность на последовательность нот Guitar Pro.

    Возвращает список, сумма тиков которого в точности равна `ticks`
    (первая нота звучит, остальные -- лиги продления).

    Жадный выбор "бери самую крупную" здесь неверен: с триолями в таблице
    он заходит в тупик (66 = 64 + неразложимый остаток 2, хотя есть точное
    48 + 18). Поэтому решение ищется динамическим программированием --
    минимальным числом нот, при равенстве предпочитая более крупные.

    Если точного разложения нет (остаток короче 1/64), берётся ближайшая
    меньшая представимая длительность.
    """
    if ticks <= 0:
        return []
    return list(_decompose_cached(int(ticks), bool(allow_triplets)))


@lru_cache(maxsize=4096)
def _decompose_cached(ticks: int, allow_triplets: bool) -> tuple[DurationSpec, ...]:
    table = DURATIONS if allow_triplets else PLAIN_DURATIONS
    best: list[tuple[DurationSpec, ...] | None] = [None] * (ticks + 1)
    best[0] = ()
    for t in range(1, ticks + 1):
        chosen: tuple[DurationSpec, ...] | None = None
        for spec in table:  # таблица отсортирована по убыванию
            if spec.ticks > t:
                continue
            prefix = best[t - spec.ticks]
            if prefix is None:
                continue
            option = prefix + (spec,)
            if chosen is None or len(option) < len(chosen):
                chosen = option
        best[t] = chosen

    if best[ticks] is not None:
        return best[ticks]
    # точного разложения нет -- берём ближайшее меньшее
    for t in range(ticks - 1, 0, -1):
        if best[t] is not None:
            return best[t]
    return ()


def ticks_to_quarters(ticks: int) -> float:
    return ticks / TPQ


def quarters_to_ticks(quarters: float) -> int:
    return int(round(quarters * TPQ))
