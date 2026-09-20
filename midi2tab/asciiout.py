"""Текстовые табы -- предпросмотр прямо в приложении и экспорт в .txt."""

from __future__ import annotations

from .gp5out import Span
from .tuning import Fretboard, NOTE_NAMES

STRING_LABELS = {
    6: ("e", "B", "G", "D", "A", "E"),
    7: ("e", "B", "G", "D", "A", "E", "B"),
    4: ("G", "D", "A", "E"),
    5: ("G", "D", "A", "E", "B"),
}


def _labels(board: Fretboard) -> list[str]:
    preset = STRING_LABELS.get(board.string_count)
    if preset:
        return list(preset)
    # запасной вариант: назвать струны по фактической высоте, сверху вниз
    return [
        NOTE_NAMES[board.open_pitch(i) % 12]
        for i in range(board.string_count - 1, -1, -1)
    ]


def render(
    spans: list[Span],
    board: Fretboard,
    measures: list[tuple[int, int, int, int]],
    grid: int,
    *,
    width: int = 92,
    title: str = "",
    header_lines: list[str] | None = None,
) -> str:
    """Собрать табулатуру в виде текста."""
    n = board.string_count
    labels = _labels(board)

    by_start: dict[int, list[tuple[int, int, int]]] = {}
    for span in spans:
        by_start.setdefault(span.start, []).extend(span.notes)

    blocks: list[list[list[str]]] = []  # такт -> строка грифа -> ячейки
    for m_start, m_end, _, _ in measures:
        columns = max(1, (m_end - m_start) // max(1, grid))
        rows: list[list[str]] = [[] for _ in range(n)]
        for c in range(columns):
            tick = m_start + c * grid
            here = by_start.get(tick, [])
            cell = {string_idx: str(fret) for string_idx, fret, _ in here}
            cell_width = max((len(v) for v in cell.values()), default=1)
            for row in range(n):
                string_idx = n - 1 - row  # сверху -- самая высокая струна
                text = cell.get(string_idx, "")
                rows[row].append(text.rjust(cell_width, "-") if text else "-" * cell_width)
        blocks.append(rows)

    # Раскладка: несколько тактов в строку, пока влезает в ширину
    out: list[str] = []
    if title:
        out.append(title)
    if header_lines:
        out.extend(header_lines)
    if out:
        out.append("")

    line_start = 0
    while line_start < len(blocks):
        used = 2  # место под подпись струны
        line_end = line_start
        while line_end < len(blocks):
            block_width = sum(len(c) + 1 for c in blocks[line_end][0]) + 1
            if line_end > line_start and used + block_width > width:
                break
            used += block_width
            line_end += 1

        for row in range(n):
            parts = [f"{labels[row]}|"]
            for b in range(line_start, line_end):
                parts.append("-".join(blocks[b][row]))
                parts.append("|")
            out.append("".join(parts))
        out.append("")
        line_start = line_end

    return "\n".join(out)
