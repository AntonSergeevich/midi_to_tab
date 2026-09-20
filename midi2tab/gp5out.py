"""
Запись Guitar Pro 5.

Такты строятся по карте размеров из MIDI, а не по жёстко зашитому 4/4.
Нота, переходящая через тактовую черту, разбивается и связывается лигой,
а не обрубается. Длительности собираются точно, в целых тиках, поэтому
аварийный пересчёт "всё в шестнадцатые" здесь просто не нужен.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import guitarpro as gp

from .arrange import Placement
from .timing import TPQ, decompose
from .tuning import Fretboard

# Формат GP5 хранит текст в 8-битной кодировке, а не в юникоде.
# PyGuitarPro по умолчанию берёт cp1252 -- в неё не влезает кириллица,
# и запись падает с "'charmap' codec can't encode characters".
# Поэтому кодировка подбирается по фактическому тексту.
GP_ENCODINGS = ("cp1252", "cp1251", "cp1250", "cp1254", "iso8859-7", "latin-1")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    """Кириллица -> латиница. Последняя мера, если текст не лезет ни в одну кодировку."""
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            swapped = _TRANSLIT[low]
            out.append(swapped.upper() if ch.isupper() else swapped)
        elif ch.isascii():
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def pick_encoding(texts: list[str]) -> str | None:
    """Первая 8-битная кодировка, в которую влезает весь текст."""
    for encoding in GP_ENCODINGS:
        try:
            for text in texts:
                text.encode(encoding)
            return encoding
        except (UnicodeEncodeError, LookupError):
            continue
    return None


@dataclass
class Span:
    """Отрезок звучания: что играем и сколько по времени."""

    start: int
    end: int
    notes: list[tuple[int, int, int]]  # (струна 0=низ, лад, громкость)

    @property
    def is_rest(self) -> bool:
        return not self.notes


def measure_plan(
    time_signatures: list[tuple[int, int, int]], total_ticks: int
) -> list[tuple[int, int, int, int]]:
    """
    Разметка тактов: (начало, конец, числитель, знаменатель).

    Размер может меняться посреди пьесы -- карта берётся из MIDI.
    """
    if not time_signatures:
        time_signatures = [(0, 4, 4)]
    plan: list[tuple[int, int, int, int]] = []
    cursor = 0
    for i, (start, num, den) in enumerate(time_signatures):
        next_start = (
            time_signatures[i + 1][0] if i + 1 < len(time_signatures) else max(total_ticks, 1)
        )
        bar = max(1, int(num * TPQ * 4 / den))
        cursor = max(cursor, start)
        while cursor < next_start or (i == len(time_signatures) - 1 and cursor < total_ticks):
            plan.append((cursor, cursor + bar, num, den))
            cursor += bar
            if cursor >= max(total_ticks, next_start):
                break
    if not plan:
        plan.append((0, TPQ * 4, 4, 4))
    return plan


def build_spans(placements: list[Placement], grid: int) -> list[Span]:
    """Превратить аппликатуры в непересекающиеся отрезки на общей шкале."""
    from .timing import quantize

    raw: list[Span] = []
    for p in placements:
        played = p.played
        if not played:
            continue
        start = quantize(p.chord.onset, grid)
        length = max(grid, quantize(p.chord.duration, grid))
        raw.append(Span(start=start, end=start + length, notes=played))

    raw.sort(key=lambda s: s.start)

    # Слить совпавшие по времени и обрезать наложения: на одной дорожке
    # гитары ноты не могут перекрывать друг друга по времени.
    merged: list[Span] = []
    for span in raw:
        if merged and span.start == merged[-1].start:
            busy = {s for s, _, _ in merged[-1].notes}
            merged[-1].notes.extend([n for n in span.notes if n[0] not in busy])
            merged[-1].end = max(merged[-1].end, span.end)
            continue
        merged.append(span)

    for i in range(len(merged) - 1):
        merged[i].end = min(merged[i].end, merged[i + 1].start)
    return [s for s in merged if s.end > s.start]


def _duration(spec) -> gp.Duration:
    dur = gp.Duration(value=spec.value, isDotted=spec.dotted)
    if spec.tuplet:
        dur.tuplet = gp.Tuplet(enters=3, times=2)
    return dur


def _emit_rest(voice: gp.Voice, ticks: int, allow_triplets: bool) -> None:
    for spec in decompose(ticks, allow_triplets):
        beat = gp.Beat(voice=voice, status=gp.BeatStatus.rest)
        beat.duration = _duration(spec)
        voice.beats.append(beat)


def _emit_notes(
    voice: gp.Voice,
    span_notes: list[tuple[int, int, int]],
    ticks: int,
    string_count: int,
    *,
    tied_from_before: bool,
    let_ring: bool,
    allow_triplets: bool,
) -> None:
    specs = decompose(ticks, allow_triplets)
    for i, spec in enumerate(specs):
        beat = gp.Beat(voice=voice, status=gp.BeatStatus.normal)
        beat.duration = _duration(spec)
        continuation = tied_from_before or i > 0
        for string_idx, fret, velocity in span_notes:
            note = gp.Note(
                beat=beat,
                value=fret,
                string=string_count - string_idx,  # в GP струна 1 -- самая высокая
                velocity=max(1, min(127, velocity)),
            )
            if continuation:
                note.type = gp.NoteType.tie
            if let_ring:
                note.effect.letRing = True
            beat.notes.append(note)
        voice.beats.append(beat)


def write_gp5(
    placements: list[Placement],
    board: Fretboard,
    output_path: str,
    *,
    title: str = "",
    artist: str = "",
    tempo: int = 100,
    time_signatures: list[tuple[int, int, int]] | None = None,
    grid: int = 12,
    allow_triplets: bool = True,
    let_ring: bool = False,
    track_name: str = "Guitar",
    midi_program: int = 25,
    on_note=None,
) -> str:
    """Собрать и сохранить файл Guitar Pro 5."""
    spans = build_spans(placements, grid)
    total = max((s.end for s in spans), default=TPQ * 4)
    plan = measure_plan(time_signatures or [(0, 4, 4)], total)

    song = gp.Song()
    song.title = title or Path(output_path).stem
    song.artist = artist
    song.tempo = int(tempo)

    track = song.tracks[0]
    track.name = track_name
    track.isPercussionTrack = False
    track.channel.instrument = midi_program
    track.offset = board.capo
    track.fretCount = max(board.max_fret, 22)
    # В GP струна 1 -- самая высокая; внутри движка 0 -- самая низкая.
    track.strings = [
        gp.GuitarString(number=i + 1, value=board.open_pitch(board.string_count - 1 - i))
        for i in range(board.string_count)
    ]

    # Заголовки тактов
    song.measureHeaders[0].timeSignature.numerator = plan[0][2]
    song.measureHeaders[0].timeSignature.denominator.value = plan[0][3]
    for idx, (_, _, num, den) in enumerate(plan[1:], start=2):
        header = gp.MeasureHeader()
        header.number = idx
        header.timeSignature.numerator = num
        header.timeSignature.denominator.value = den
        song.measureHeaders.append(header)
        for t in song.tracks:
            t.measures.append(gp.Measure(t, header))

    # Наполнение тактов
    span_idx = 0
    for m_index, (m_start, m_end, _, _) in enumerate(plan):
        measure = track.measures[m_index]
        voice = measure.voices[0]
        voice.beats.clear()
        cursor = m_start

        i = span_idx
        while i < len(spans) and spans[i].start < m_end:
            span = spans[i]
            if span.end <= m_start:
                i += 1
                span_idx = i
                continue
            seg_start = max(span.start, m_start)
            seg_end = min(span.end, m_end)
            if seg_end <= seg_start:
                i += 1
                continue
            if seg_start > cursor:
                _emit_rest(voice, seg_start - cursor, allow_triplets)
            _emit_notes(
                voice,
                span.notes,
                seg_end - seg_start,
                board.string_count,
                tied_from_before=span.start < m_start,
                let_ring=let_ring,
                allow_triplets=allow_triplets,
            )
            cursor = seg_end
            if span.end <= m_end:
                i += 1
                span_idx = i
            else:
                break  # нота продолжается в следующем такте

        if cursor < m_end:
            _emit_rest(voice, m_end - cursor, allow_triplets)
        if not voice.beats:
            _emit_rest(voice, m_end - m_start, allow_triplets)

    # Подбор кодировки под фактический текст (название файла бывает кириллицей)
    texts = [song.title or "", song.artist or "", track.name or ""]
    encoding = pick_encoding(texts)
    if encoding is None:
        song.title = transliterate(song.title or "")
        song.artist = transliterate(song.artist or "")
        track.name = transliterate(track.name or "")
        encoding = "cp1252"
        if on_note:
            on_note("Название содержит символы вне 8-битных кодировок -- записано латиницей.")
    elif encoding != "cp1252" and on_note:
        on_note(f"Текст записан в кодировке {encoding} (в ней есть нужные символы).")

    try:
        gp.write(song, output_path, encoding=encoding)
    except UnicodeEncodeError:
        # страховка: что-то не учли -- пишем заведомо безопасной латиницей
        song.title = transliterate(song.title or "")
        song.artist = transliterate(song.artist or "")
        track.name = transliterate(track.name or "")
        gp.write(song, output_path, encoding="cp1252")
        if on_note:
            on_note("Название записано латиницей: исходное не поддерживается форматом GP5.")
    return output_path
