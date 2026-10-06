"""SheetSage2 в своём окружении (/venv-ss): запись -> JSON на stdout.

    /venv-ss/bin/python ss_run.py song.wav [--melody-only]

Выход: abc (ноты; без аккордов при --melody-only), chords/structure/key --
отрезки [начало, конец, метка] из LAB-файлов, длительность.
"""
import json
import sys

import torch
from transformers import AutoModel


def rows(text):
    out = []
    for line in (text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            try:
                out.append([round(float(parts[0]), 2), round(float(parts[1]), 2), parts[2].strip()])
            except ValueError:
                continue
    return out


def load_model():
    """SheetSage2 -- сначала как обычный пакет (/models/SheetSage2 с
    __init__.py), иначе через AutoModel. transformers 4.45 при
    trust_remote_code копирует в свой кэш модулей не все файлы SheetSage2 и
    падает на chord_spelling_sheetsage2.py (пробы 06.10) -- поэтому кэш
    модулей заполняем сами: все .py модели лежат там заранее."""
    import os
    import shutil

    source = "/models/SheetSage2"
    cache = os.path.join(os.environ.get("HF_HOME", "/models/hf"), "modules", "transformers_modules", "SheetSage2")
    os.makedirs(cache, exist_ok=True)
    for name in os.listdir(source):
        if name.endswith(".py"):
            shutil.copy2(os.path.join(source, name), os.path.join(cache, name))
    errors = []
    sys.path.insert(0, "/models")
    try:
        from SheetSage2.modeling_sheetsage2 import SheetSage2Model
        model = SheetSage2Model.from_pretrained(source, local_files_only=True)
    except Exception as error:  # noqa: BLE001 -- запасной путь, как в README
        errors.append(f"пакетом: {error!r}"[:400])
        print(f"[ss] {errors[-1]}", file=sys.stderr, flush=True)
        try:
            model = AutoModel.from_pretrained(source, trust_remote_code=True, local_files_only=True)
        except Exception as second:  # noqa: BLE001
            raise RuntimeError(" || ".join(errors + [f"AutoModel: {second!r}"[:600]])) from second
    return model.eval().to("cuda")


def read_wav(path):
    """Звук -- массивом [каналы, отсчёты]: читать файлы сама SheetSage2 хочет
    FFmpeg 6.1 с общими библиотеками, а в образе Ubuntu 22.04 -- 4.4."""
    import numpy as np
    from scipy.io import wavfile

    rate, data = wavfile.read(path)
    data = data.astype(np.float32) / (32768.0 if data.dtype == np.int16 else 1.0)
    return (data.T if data.ndim == 2 else data), rate


def beat_times(text):
    """Времена долей из beat.lab (первая колонка каждой строки)."""
    out = []
    for line in (text or "").splitlines():
        try:
            out.append(float(line.split()[0]))
        except (ValueError, IndexError):
            continue
    return sorted(set(round(t, 4) for t in out))


def retime(data, beats, meter=4, downbeat=None):
    """MIDI SheetSage2 -> MIDI с картой темпа по найденным долям записи.

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


def save_midis(result, out, given=None):
    """MIDI частей -- файлами в папку out: мелодия вокала, мелодия
    инструментов, аккорды и всё вместе -- с долями и тактами записи."""
    import os

    os.makedirs(out, exist_ok=True)
    labs = result.get("labs") or {}
    beats = beat_times(labs.get("beat"))
    downbeats = beat_times(labs.get("downbeat"))
    if given:          # доли Beat This -- точнее и совпадают с сайтом
        beats, downbeats = given.get("beats") or beats, given.get("downbeats") or downbeats
    saved = []
    parts = dict(result.get("midis") or {})
    if result.get("midi"):
        parts["transcription"] = result["midi"]
    for name, data in parts.items():
        if isinstance(data, (bytes, bytearray)) and data:
            safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name).rsplit(".", 1)[0])
            try:
                data = retime(bytes(data), beats, downbeat=downbeats[0] if downbeats else None)
            except Exception as error:  # noqa: BLE001 -- без карты темпа, но с нотами
                print(f"[ss] {safe}: карта темпа не вышла ({error!r})", file=sys.stderr, flush=True)
            with open(os.path.join(out, f"{safe}.mid"), "wb") as f:
                f.write(data)
            saved.append(f"{safe}.mid")
    return saved


def main():
    path, melody_only = sys.argv[1], "--melody-only" in sys.argv
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    given = None
    if "--beats" in sys.argv:
        with open(sys.argv[sys.argv.index("--beats") + 1]) as f:
            given = json.load(f)
    model = load_model()
    waveform, rate = read_wav(path)
    result = model.transcribe(waveform, sampling_rate=rate, melody_only=melody_only)
    labs = result.get("labs") or {}
    print(json.dumps({"abc": result.get("abc") or "", "abc_error": result.get("abc_error"),
                      "chords": rows(labs.get("chord")), "structure": rows(labs.get("structure")),
                      "key": rows(labs.get("key")), "labs": sorted(labs),
                      "midis": save_midis(result, out, given) if out else [],
                      "duration": result.get("duration_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
