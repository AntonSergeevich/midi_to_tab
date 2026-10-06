"""
Сетка долей MIDI = сетка долей записи, как её слышит сайт.

MIDI нот, снятых с песни (YuE2/SheetSage2), должен идти по тем же долям,
что метроном и такты табов на сайте. Доли считает Beat This, а он
чувствителен к тому, чем и как прочитан mp3 (на одном файле 258 долей у
одного декодера и 190 у другого, 06.10). Поэтому сетку строим здесь, на
сервере, тем же чтением, что и разбор трека (audiochords): тогда метроном
и MIDI совпадают по построению, а ноты остаются ровно там же по времени.
"""

from __future__ import annotations

SR = 22050


def audio_beats(path: str):
    """(bpm, доли, сильные доли) записи -- как в разборе трека; None -- не вышло."""
    import librosa

    from . import beatnet

    y, sr = librosa.load(path, sr=SR, mono=True)        # как audiochords.detect_from_audio
    return beatnet.track(y, sr) if y.size else None


def align_file(midi_path: str, audio_path: str) -> bool:
    """Переписать MIDI с картой темпа по долям записи. True -- переписан."""
    found = audio_beats(audio_path)
    if not found or len(found[1]) < 4:
        return False
    _, beats, downbeats = found
    with open(midi_path, "rb") as f:
        data = retime(f.read(), beats, downbeat=downbeats[0] if downbeats else None)
    with open(midi_path, "wb") as f:
        f.write(data)
    return True


def retime(data, beats, meter=4, downbeat=None):
    """MIDI -> MIDI с картой темпа по долям записи (копия -- в yue2/ss_run.py).

    SheetSage2 пишет ноты в реальном времени, но с условным темпом 120:
    доли и такты файла не совпадают с долями песни (метроном и такты табов
    мимо, 06.10). Здесь одна четверть = одна доля записи: темп меняется на
    каждой доле, а ноты остаются ровно там же по времени."""
    import io

    import mido
    import numpy as np
    import pretty_midi

    if len(beats) < 4:
        return data
    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    ppq = 480
    b = np.array(beats, dtype=float)
    first, last = b[1] - b[0], b[-1] - b[-2]
    # Доли до первой и после последней -- тем же шагом, чтобы покрыть все ноты
    end = max([n.end for i in pm.instruments for n in i.notes] + [b[-1]]) + 2 * last
    head = np.arange(b[0] - first, -first, -first)[::-1] if b[0] > 0 else np.array([])
    tail = np.arange(b[-1] + last, end + last, last)
    grid = np.concatenate([head[head >= 0], b, tail])
    if grid[0] > 0:
        grid = np.concatenate([[0.0], grid])                 # тик 0 -- в начале записи
    to_tick = lambda t: int(round(np.interp(t, grid, np.arange(len(grid))) * ppq))  # noqa: E731
    out = mido.MidiFile(ticks_per_beat=ppq)
    tempo = mido.MidiTrack()
    # Такты -- от найденной сильной доли: если до неё не целое число тактов,
    # первый такт короче (затакт), дальше -- полные такты по meter долей
    pickup = 0
    if downbeat is not None:
        first = int(np.argmin(np.abs(grid - downbeat)))
        pickup = first % meter
    events = [(0, mido.MetaMessage("time_signature", numerator=pickup or meter, denominator=4, time=0))]
    if pickup:
        events.append((pickup * ppq, mido.MetaMessage("time_signature", numerator=meter, denominator=4, time=0)))
    for i in range(len(grid) - 1):
        micro = int(round((grid[i + 1] - grid[i]) * 1_000_000))
        # без «разумных» пределов: обрезанная короткая доля в начале (0.18 с)
        # сдвигала все ноты на 20 мс; MIDI допускает темп до 0xFFFFFF мкс
        events.append((i * ppq, mido.MetaMessage("set_tempo", tempo=max(1, min(micro, 0xFFFFFF)), time=0)))
    events.sort(key=lambda e: e[0])
    now = 0
    for tick, msg in events:
        tempo.append(msg.copy(time=tick - now))
        now = tick
    out.tracks.append(tempo)
    for inst in pm.instruments:
        track = mido.MidiTrack()
        channel = 9 if inst.is_drum else min(len(out.tracks) - 1, 8)
        if not inst.is_drum:
            track.append(mido.Message("program_change", program=inst.program, channel=channel, time=0))
        notes = []
        for n in inst.notes:
            on, off = to_tick(n.start), max(to_tick(n.start) + 1, to_tick(n.end))
            notes += [(on, 1, n.pitch, n.velocity), (off, 0, n.pitch, 0)]
        now = 0
        for tick, is_on, pitch, velocity in sorted(notes):
            kind = "note_on" if is_on else "note_off"
            track.append(mido.Message(kind, note=pitch, velocity=velocity, channel=channel, time=tick - now))
            now = tick
        if inst.name:
            track.insert(0, mido.MetaMessage("track_name", name=inst.name, time=0))
        out.tracks.append(track)
    buffer = io.BytesIO()
    out.save(file=buffer)
    return buffer.getvalue()
