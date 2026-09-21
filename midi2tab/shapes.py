"""
Аппликатуры: как ставить аккорд на грифе.

Название аккорда музыканту мало что даёт, если он не помнит, как этот
аккорд берётся. Поэтому рядом с подписью нужна картинка грифа -- и не
любая, а та, которую человек действительно поставит.

Аппликатуры не хранятся списком, а ВЫВОДЯТСЯ из строя. Это принципиально:
строёв в сервисе дюжина, включая drop D, укулеле и семиструнку, а
таблицу аккордов пришлось бы заводить для каждого. Здесь же перебираются
возможные положения и отбираются по тем же соображениям, по которым
гитарист выбирает между ними: рука не растягивается больше чем на
четыре лада, основной тон лежит в басу, открытые струны предпочтительнее
зажатых, а низкие позиции -- высоких.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chords import PITCH_CLASSES, TEMPLATES
from .tuning import Fretboard

MAX_SPAN = 4          # насколько широко расставляются пальцы
MAX_FRET = 12         # выше двенадцатого лада песни под гитару не играют
MAX_FINGERS = 4


@dataclass
class Shape:
    """Одно положение аккорда на грифе."""

    frets: tuple[int | None, ...]   # по струнам снизу вверх; None -- не играем
    base: int                       # с какого лада нарисована сетка
    barre: int                      # лад баррэ, 0 -- без баррэ

    @property
    def fingers(self) -> int:
        pressed = [f for f in self.frets if f]
        if not pressed:
            return 0
        if self.barre:
            return 1 + sum(1 for f in pressed if f > self.barre)
        return len(pressed)

    def as_dict(self) -> dict:
        return {"frets": list(self.frets), "base": self.base, "barre": self.barre}


def parse(name: str) -> tuple[int, tuple[int, ...]] | None:
    """Разобрать подпись аккорда в (основной тон, интервалы)."""
    head = name[:2] if len(name) > 1 and name[1] == "#" else name[:1]
    if head not in PITCH_CLASSES:
        return None
    quality = name[len(head):]
    for label, intervals in TEMPLATES:
        if label == quality:
            return PITCH_CLASSES.index(head), intervals
    return None


def shapes_for(name: str, board: Fretboard, limit: int = 3) -> list[Shape]:
    """
    Найти аппликатуры аккорда, от самой удобной к менее удобным.

    Перебираются положения руки, а не отдельные ноты: гитарист ставит
    руку в позицию и уже внутри неё решает, какие струны зажать. Это же
    ограничение и отсекает нелепые растяжки, которые иначе выглядели бы
    законными.
    """
    parsed = parse(name)
    if not parsed:
        return []
    root, intervals = parsed
    wanted = {(root + i) % 12 for i in intervals}

    found: list[tuple[float, Shape]] = []
    for position in range(0, MAX_FRET + 1):
        shape = _best_in_position(board, root, wanted, position)
        if shape:
            found.append((_cost(shape, board, root, position), shape))

    found.sort(key=lambda pair: pair[0])
    picked: list[Shape] = []
    for _, shape in found:
        if all(shape.frets != other.frets for other in picked):
            picked.append(shape)
        if len(picked) >= limit:
            break
    return picked


def _best_in_position(board: Fretboard, root: int, wanted: set[int], position: int):
    """Лучшее положение руки на конкретной позиции грифа."""
    low = position
    high = position + MAX_SPAN
    frets: list[int | None] = []

    for string_index in range(board.string_count):
        open_pitch = board.open_pitch(string_index)
        # Открытая струна годится, только если её нота входит в аккорд
        choices = []
        if open_pitch % 12 in wanted and position <= 1:
            choices.append(0)
        for fret in range(max(1, low), high + 1):
            if (open_pitch + fret) % 12 in wanted:
                choices.append(fret)
        # Берём самую низкую подходящую -- рука тянется меньше
        frets.append(min(choices) if choices else None)

    if not any(f is not None for f in frets):
        return None

    # Заглушаем нижние струны, пока в басу не окажется основной тон.
    # У укулеле так делать нельзя: его первая струна звучит ВЫШЕ второй,
    # и понятия "нижняя струна" там попросту нет -- требование баса
    # загнало бы простое до-мажорное 0003 на пятый лад.
    ascending = all(
        board.open_pitch(i) < board.open_pitch(i + 1)
        for i in range(board.string_count - 1)
    )
    if ascending:
        for index in range(board.string_count):
            fret = frets[index]
            if fret is not None and (board.open_pitch(index) + fret) % 12 == root:
                for lower in range(index):
                    frets[lower] = None
                break
        else:
            return None

    sounding = {
        (board.open_pitch(i) + f) % 12
        for i, f in enumerate(frets) if f is not None
    }
    if sounding != wanted:
        return None

    played = [f for f in frets if f is not None]
    pressed = [f for f in played if f]
    barre = 0
    # Баррэ прижимает ВСЕ струны на своём ладу, поэтому рядом с ним не
    # может быть открытой струны: палец её всё равно прижмёт, и нота
    # выйдет другая. Прежняя проверка открытые струны не замечала и
    # рисовала баррэ там, где его физически не поставить.
    if played and 0 not in played and pressed:
        lowest = min(pressed)
        if sum(1 for f in pressed if f == lowest) >= 2:
            barre = lowest
    base = min(pressed) if pressed and min(pressed) > 1 else 1
    shape = Shape(tuple(frets), base, barre)
    return shape if shape.fingers <= MAX_FINGERS or barre else shape


def _cost(shape: Shape, board: Fretboard, root: int, position: int) -> float:
    """
    Чем аппликатура неудобнее, тем дороже.

    Порядок слагаемых и есть порядок доводов, по которым гитарист
    выбирает: сперва позиция пониже, потом поменьше пальцев, потом
    побольше открытых струн и звучащих струн.
    """
    pressed = [f for f in shape.frets if f]
    open_strings = sum(1 for f in shape.frets if f == 0)
    muted = sum(1 for f in shape.frets if f is None)
    span = (max(pressed) - min(pressed)) if pressed else 0
    return (
        position * 1.0
        + shape.fingers * 0.8
        + span * 0.5
        + muted * 0.6
        - open_strings * 0.7
        + (0.5 if shape.barre else 0.0)
    )


def diagram(name: str, board: Fretboard, limit: int = 3) -> list[dict]:
    """Аппликатуры в виде, готовом для показа на странице."""
    return [shape.as_dict() for shape in shapes_for(name, board, limit)]
