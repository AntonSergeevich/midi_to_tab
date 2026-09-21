"""
Очередь заданий.

Задания двух родов, и это отражает то, как человек на самом деле
работает с песней:

  analyze -- сразу после загрузки: трек делится на партии и размечается
             аккордами. Быстро, и результат уже можно играть в плеере.
  tabs    -- по кнопке под выбранной партией: распознавание нот и
             раскладка по грифу. Дорого, поэтому только когда попросили,
             и только для той партии, которая нужна.

Раньше распознавание запускалось сразу для всего -- и человек ждал
минуты, даже если хотел только подпевать по аккордам.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from midi2tab import (audiochords, audioin, cleanup, gp5out,
                     lyrics as lyrics_mod, midiin, separate, shapes)
from midi2tab.convert import Settings, convert
from midi2tab.timing import TPQ

from .storage import Storage

MIDI_SUFFIXES = (".mid", ".midi")


def tab_payload(placements, board, tempo, time_signatures) -> dict:
    """Колонки табулатуры и такты со временем в секундах."""
    factor = 60.0 / max(1, tempo) / TPQ
    spans = gp5out.build_spans(placements, 12)
    columns = [
        {
            "t": round(span.start * factor, 3),
            "end": round(span.end * factor, 3),
            "notes": [
                {
                    "string": string_idx,
                    "fret": fret,
                    "pitch": board.open_pitch(string_idx) + fret,
                    "vel": velocity,
                }
                for string_idx, fret, velocity in span.notes
            ],
        }
        for span in spans
    ]
    total = max((s.end for s in spans), default=TPQ * 4)
    plan = gp5out.measure_plan(time_signatures, total)
    measures = [
        {"number": i + 1, "start": round(start * factor, 3), "end": round(end * factor, 3)}
        for i, (start, end, _, _) in enumerate(plan)
    ]
    return {
        "strings": board.string_count,
        "tuning": board.describe(),
        "duration": round(total * factor, 3),
        "columns": columns,
        "measures": measures,
    }


# Во сколько раз обработка минуты музыки дольше самой музыки. Замерено на
# сервере с двумя ядрами. Числа нужны только для оценки процента: если
# реальность окажется медленнее, полоса не встанет намертво, а замедлится.
SPEED = {
    "separate": 1.2,    # Demucs на процессоре -- самый долгий шаг
    "chords": 0.10,     # хромаграмма и Витерби
    "notes": 0.06,      # Basic Pitch плюс раскладка по грифу
    "lyrics": 0.40,     # Whisper small с квантизацией int8
}


class Progress:
    """
    Оценка доли выполненного.

    Честного процента взять неоткуда: библиотеки внутри не отчитываются о
    ходе работы. Зато известно другое -- длительность записи и во сколько
    раз обработка её дольше. Этого хватает: каждому шагу отводится своя
    полоса процентов, а внутри полосы доля считается по прошедшему
    времени. Если шаг затянулся сверх ожидаемого, полоса не упирается в
    потолок и не замирает, а подползает к концу своего отрезка всё
    медленнее -- человек видит, что работа идёт.

    Обновление вынесено в отдельный поток: тяжёлый шаг -- это один
    блокирующий вызов, и без тикера процент стоял бы всё время, пока он
    считается.
    """

    INTERVAL = 2.0

    def __init__(self, storage: Storage, job_id: str, duration: float = 0.0) -> None:
        self.storage = storage
        self.job_id = job_id
        self.duration = max(0.0, duration)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._text = ""
        self._low = 0.0
        self._high = 0.0
        self._expected = 1.0
        self._started = time.time()

    # ------------------------------------------------------------- шаги

    def begin(self, text: str, low: float, high: float, kind: str,
              factor: float | None = None) -> None:
        """Начать шаг: полоса пойдёт от low до high за ожидаемое время."""
        expected = max(3.0, self.duration * (factor or SPEED.get(kind, 0.2)))
        with self._lock:
            self._text, self._low, self._high = text, low, high
            self._expected = expected
            self._started = time.time()
        self._push()
        self._start_thread()

    def note(self, text: str) -> None:
        """Сменить подпись, не трогая отсчёт шага."""
        with self._lock:
            self._text = text
        self._push()

    def done(self, text: str = "Готово") -> None:
        self.stop()
        self.storage.update_job(self.job_id, stage=text, progress=100.0)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self.INTERVAL + 1)
        self._thread = None

    # --------------------------------------------------------- механика

    def _start_thread(self) -> None:
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()

    def _tick(self) -> None:
        while not self._stop.wait(self.INTERVAL):
            self._push()

    def _push(self) -> None:
        with self._lock:
            text, low, high = self._text, self._low, self._high
            share = _share(time.time() - self._started, self._expected)
        self.storage.update_job(
            self.job_id, stage=text, progress=round(low + (high - low) * share, 1)
        )


def _share(elapsed: float, expected: float) -> float:
    """
    Доля шага, пройденная за elapsed секунд при ожидаемых expected.

    До ожидаемого времени -- просто линейно, до 0.92. Дальше остаток
    расходуется всё медленнее и никогда не заканчивается: лучше показать
    93%, которые ползут, чем 100%, после которых человек ещё минуту ждёт.
    """
    if expected <= 0:
        return 0.92
    ratio = elapsed / expected
    if ratio <= 1.0:
        return 0.92 * ratio
    return 0.92 + 0.08 * (1.0 - 1.0 / (1.0 + (ratio - 1.0)))


def _shapes_for(chords, options) -> dict:
    """
    Картинки аппликатур для каждого аккорда песни.

    Название аккорда мало что даёт, если человек не помнит, как этот
    аккорд берётся, -- а именно за этим сервис и открывают. Считаются
    аппликатуры для того строя, который выбран: у drop D и укулеле они
    свои.
    """
    from midi2tab.tuning import DEFAULT_TUNING, TUNINGS, Fretboard

    tuning = TUNINGS.get(options.get("tuning") or DEFAULT_TUNING)
    board = Fretboard(tuning or TUNINGS[DEFAULT_TUNING], capo=int(options.get("capo", 0)))
    return {
        name: shapes.diagram(name, board)
        for name in dict.fromkeys(c["name"] for c in chords)
    }


class JobRunner:
    def __init__(self, storage: Storage, data_dir: str, workers: int = 2) -> None:
        self.storage = storage
        self.data_dir = data_dir
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit_analysis(self, job_id: str, source_path: str) -> None:
        self.pool.submit(self._analyze, job_id, source_path)

    def submit_separation(self, job_id: str) -> None:
        self.pool.submit(self._separate_later, job_id)

    def submit_tabs(self, job_id: str, parent_id: str, stem_key: str) -> None:
        self.pool.submit(self._tabs, job_id, parent_id, stem_key)

    def submit_lyrics(self, job_id: str, model: str) -> None:
        self.pool.submit(self._lyrics, job_id, model)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    # --------------------------------------------------------------- разбор

    def _analyze(self, job_id: str, source_path: str) -> None:
        job = self.storage.job(job_id)
        if job is None:
            return
        out_dir = os.path.join(self.data_dir, "results", job_id)
        os.makedirs(out_dir, exist_ok=True)
        self.storage.update_job(
            job_id, status="running", stage="Начинаю разбор...", progress=1.0
        )

        options = job.settings or {}
        is_midi = source_path.lower().endswith(MIDI_SUFFIXES)
        seconds = 0.0 if is_midi else audioin.duration_seconds(source_path)
        bar = Progress(self.storage, job_id, seconds)

        try:
            parts: list[dict] = []
            chords: list[dict] = []
            source_note = ""
            key = ""
            beats: list[float] = []
            downbeats: list[float] = []
            tempo = int(options.get("tempo") or 0)

            # 1. Разделение на партии
            if not is_midi and options.get("separate"):
                ok, why = separate.available()
                if not ok:
                    raise RuntimeError(why)
                # Разделение занимает примерно столько же, сколько длится
                # сама музыка, поэтому ему отдана большая часть полосы.
                quality = options.get("quality") or separate.DEFAULT_QUALITY
                factor = separate.QUALITY.get(
                    quality, separate.QUALITY[separate.DEFAULT_QUALITY]
                )[2]
                bar.begin("Делю трек на партии...", 2, 72, "separate", factor)
                result = separate.separate(
                    source_path,
                    os.path.join(out_dir, "stems"),
                    options.get("model", separate.DEFAULT_MODEL),
                    quality=quality,
                    progress=bar.note,
                )
                parts = [
                    {"key": key, "label": result.label_for(key), "path": path}
                    for key, path in result.stems.items()
                ]
            elif not is_midi:
                parts = [{"key": "full", "label": "Весь трек", "path": source_path}]
            else:
                parts = [{"key": "full", "label": "MIDI целиком", "path": source_path}]

            # 2. Аккорды прямо из звука -- без нейросети и в разы быстрее,
            #    чем через распознавание отдельных нот
            if not is_midi:
                # Если партии посчитаны, слушаем гармонию без барабанов и
                # голоса: певец тянет ноту поверх аккорда, и она читается
                # как надстройка -- трезвучие становится септаккордом.
                listen_to = source_path
                source_note = ""
                if parts and options.get("separate"):
                    mixed = separate.harmonic_mix(
                        {p["key"]: p["path"] for p in parts},
                        os.path.join(out_dir, "harmony.wav"),
                    )
                    if mixed:
                        listen_to = mixed
                        source_note = "без барабанов и голоса"

                low = 72 if options.get("separate") else 2
                bar.begin(
                    "Слушаю аккорды" + (f" {source_note}" if source_note else "") + "...",
                    low, 97, "chords",
                )
                analysis = audiochords.detect_from_audio(
                    listen_to,
                    min_duration=float(options.get("minChord", audiochords.MIN_DURATION)),
                    vocabulary=int(options.get("vocabulary", audiochords.DEFAULT_VOCABULARY)),
                    allowed=options.get("allowed") or None,
                    progress=bar.note,
                )
                chords = [
                    {
                        "name": c.name,
                        "start": round(c.start, 3),
                        "end": round(c.end, 3),
                        "confidence": round(c.confidence, 2),
                    }
                    for c in analysis.chords
                ]
                beats = [round(b, 3) for b in analysis.beats]
                downbeats = [round(b, 3) for b in analysis.downbeats]
                key = analysis.key
                if not tempo and analysis.tempo:
                    tempo = int(round(analysis.tempo))

            result_data = {
                "kind": "analysis",
                "isMidi": is_midi,
                "tempo": tempo or 120,
                "tempoDetected": bool(not options.get("tempo") and tempo),
                "chords": chords,
                "chordSource": source_note,
                "key": key,
                "shapes": _shapes_for(chords, options),
                "beats": beats,
                "downbeats": downbeats,
                "parts": [
                    {"key": p["key"], "label": p["label"], "audio":
                     f"/api/file/{job_id}/part/{p['key']}"}
                    for p in parts
                ],
                "audio": f"/api/file/{job_id}/source",
                "paths": {
                    "source": source_path,
                    "parts": {p["key"]: p["path"] for p in parts},
                },
            }
            bar.stop()
            self.storage.update_job(
                job_id, status="done", stage="Готово", progress=100.0,
                result=result_data, error=None,
            )
        except Exception as exc:
            bar.stop()
            self.storage.update_job(
                job_id, status="error", stage="", progress=0.0, error=str(exc)
            )
            print(f"[analyze {job_id}] {exc}\n{traceback.format_exc()}")

    def _separate_later(self, job_id: str) -> None:
        """
        Разделить на партии уже разобранный трек.

        Человек сначала берёт аккорды -- это быстро, -- а партии ему
        нужны потом, когда дошло до конкретной гитары. Заставлять его
        грузить тот же файл заново и терять сделанные табы -- нелепо:
        исходник лежит на диске, разделение просто дописывается к
        существующему разбору.
        """
        job = self.storage.job(job_id)
        if job is None or not job.result:
            return
        source = (job.result.get("paths") or {}).get("source")
        if not source or not os.path.isfile(source):
            self.storage.update_job(job_id, error="Исходный файл не найден")
            return

        ok, why = separate.available()
        if not ok:
            self.storage.update_job(job_id, error=why)
            return

        out_dir = os.path.join(self.data_dir, "results", job_id)
        os.makedirs(out_dir, exist_ok=True)
        options = job.settings or {}
        bar = Progress(self.storage, job_id, audioin.duration_seconds(source))
        self.storage.update_job(job_id, status="running", progress=1.0, error=None)

        try:
            quality = options.get("quality") or separate.DEFAULT_QUALITY
            factor = separate.QUALITY.get(
                quality, separate.QUALITY[separate.DEFAULT_QUALITY]
            )[2]
            bar.begin("Делю трек на партии...", 2, 72, "separate", factor)
            result = separate.separate(
                source, os.path.join(out_dir, "stems"),
                options.get("model", separate.DEFAULT_MODEL),
                quality=quality, progress=bar.note,
            )
            payload = dict(job.result)
            payload["parts"] = [
                {"key": key, "label": result.label_for(key),
                 "audio": f"/api/file/{job_id}/part/{key}"}
                for key in result.stems
            ]
            paths = dict(payload.get("paths") or {})
            paths["parts"] = dict(result.stems)
            payload["paths"] = paths

            # Партии есть -- значит, аккорды можно услышать заново и чище.
            self._chords_from_stems(job_id, payload, out_dir, options, bar)

            bar.stop()
            self.storage.update_job(
                job_id, status="done", stage="Готово", progress=100.0,
                result=payload, error=None,
            )
        except Exception as exc:
            bar.stop()
            self.storage.update_job(
                job_id, status="done", stage="Разделить не удалось",
                progress=100.0, error=str(exc),
            )
            print(f"[separate {job_id}] {exc}\n{traceback.format_exc()}")

    def _chords_from_stems(self, job_id, payload, out_dir, options, bar) -> None:
        """
        Переслушать аккорды по дорожке без барабанов и голоса.

        Голос -- худший враг разбора гармонии: певец тянет ноту поверх
        аккорда, и она читается как надстройка, превращая трезвучие в
        септаккорд. Барабаны размазывают спектр. Когда партии уже
        посчитаны, убрать и то и другое ничего не стоит.
        """
        stems = (payload.get("paths") or {}).get("parts") or {}
        mix = separate.harmonic_mix(stems, os.path.join(out_dir, "harmony.wav"))
        if not mix:
            return
        bar.begin("Слушаю аккорды без барабанов и голоса...", 72, 97, "chords")
        analysis = audiochords.detect_from_audio(
            mix,
            min_duration=float(options.get("minChord", audiochords.MIN_DURATION)),
            vocabulary=int(options.get("vocabulary", audiochords.DEFAULT_VOCABULARY)),
            allowed=options.get("allowed") or None,
            progress=bar.note,
        )
        if not analysis.chords:
            return
        payload["chords"] = [
            {"name": c.name, "start": round(c.start, 3), "end": round(c.end, 3),
             "confidence": round(c.confidence, 2)}
            for c in analysis.chords
        ]
        payload["beats"] = [round(b, 3) for b in analysis.beats]
        payload["downbeats"] = [round(b, 3) for b in analysis.downbeats]
        payload["chordSource"] = "без барабанов и голоса"
        payload["key"] = analysis.key
        payload["shapes"] = _shapes_for(payload["chords"], options)

    def _lyrics(self, job_id: str, model: str) -> None:
        """
        Распознать текст по вокальной дорожке.

        Если трек разделён, берём именно вокал: распознавать чистый вокал
        вместо полного микса -- это другой уровень точности, музыка не
        забивает речь.
        """
        job = self.storage.job(job_id)
        if job is None or not job.result:
            return
        paths = (job.result.get("paths") or {}).get("parts", {})
        options = job.settings or {}
        full = (job.result.get("paths") or {}).get("source")

        # Разделить трек ПЕРЕД распознаванием, если это ещё не сделано.
        # Разница не в процентах: на полном миксе Whisper слышит гитару и
        # барабаны наравне с голосом и выдумывает слова там, где их нет.
        # Ради текста стоит подождать разделение -- иначе результат всё
        # равно негодный, и ожидание потрачено впустую.
        if "vocals" not in paths and full and os.path.isfile(full):
            ok, _why = separate.available()
            if ok:
                try:
                    self._separate_later(job_id)
                    job = self.storage.job(job_id)
                    paths = ((job.result or {}).get("paths") or {}).get("parts", {})
                except Exception as exc:
                    print(f"[lyrics {job_id}] разделение не удалось: {exc}")

        source = paths.get("vocals") or full
        if not source or not os.path.isfile(source):
            self.storage.update_job(job_id, error="Нет дорожки для распознавания текста")
            return

        used_vocals = "vocals" in paths
        bar = Progress(self.storage, job_id, audioin.duration_seconds(source))
        bar.begin("Распознаю текст...", 2, 97, "lyrics")
        try:
            result = lyrics_mod.transcribe(
                source,
                model=model,
                language=lyrics_mod.language_code(options.get("language")),
                cache_dir=os.path.join(self.data_dir, "models"),
                progress=bar.note,
            )
            payload = dict(job.result)
            payload["lyrics"] = lyrics_mod.to_dict(result)
            payload["lyricsSource"] = "вокальная дорожка" if used_vocals else "весь трек"
            bar.stop()
            self.storage.update_job(
                job_id, result=payload, stage="Текст готов", progress=100.0
            )
        except Exception as exc:
            bar.stop()
            self.storage.update_job(job_id, stage=f"Текст не распознан: {exc}")
            print(f"[lyrics {job_id}] {exc}\n{traceback.format_exc()}")

    # ----------------------------------------------------------------- табы

    def _tabs(self, job_id: str, parent_id: str, stem_key: str) -> None:
        parent = self.storage.job(parent_id)
        if parent is None or not parent.result:
            self.storage.update_job(job_id, status="error", error="Разбор не найден")
            return
        source = (parent.result.get("paths") or {}).get("parts", {}).get(stem_key)
        if not source or not os.path.isfile(source):
            self.storage.update_job(job_id, status="error", error="Партия не найдена")
            return

        out_dir = os.path.join(self.data_dir, "results", job_id)
        os.makedirs(out_dir, exist_ok=True)
        bar = Progress(self.storage, job_id, audioin.duration_seconds(source))
        self.storage.update_job(job_id, status="running", progress=1.0)
        bar.begin("Распознаю ноты...", 2, 97, "notes")

        try:
            options = parent.settings or {}
            settings = Settings(
                input_path=source,
                output_dir=out_dir,
                tuning=options.get("tuning") or Settings.tuning,
                capo=int(options.get("capo", 0)),
                tempo=int(parent.result.get("tempo") or 0),
                grid=options.get("grid") or Settings.grid,
                limit_to_range=bool(options.get("limitRange", True)),
                remove_ghosts=bool(options.get("removeGhosts", True)),
                max_polyphony=int(options.get("maxPolyphony", 0)),
            )
            converted = convert(settings, progress=bar.note)
            board = settings.fretboard()

            result_data = {
                "kind": "tabs",
                "stem": stem_key,
                "tab": tab_payload(
                    converted.placements,
                    board,
                    converted.tempo,
                    midiin.load(converted.midi_path or source).time_signatures
                    if (converted.midi_path or source).lower().endswith(MIDI_SUFFIXES)
                    else [(0, 4, 4)],
                ),
                "tabText": converted.tab_text,
                "summary": converted.summary,
                "tempo": converted.tempo,
                "files": {
                    "gp5": bool(converted.gp5_path),
                    "txt": bool(converted.txt_path),
                    "mid": bool(converted.midi_path),
                },
                "paths": {
                    "gp5": converted.gp5_path,
                    "txt": converted.txt_path,
                    "mid": converted.midi_path,
                },
            }
            bar.stop()
            self.storage.update_job(
                job_id, status="done", stage="Готово", progress=100.0,
                result=result_data, error=None,
            )
        except Exception as exc:
            bar.stop()
            self.storage.update_job(
                job_id, status="error", stage="", progress=0.0, error=str(exc)
            )
            print(f"[tabs {job_id}] {exc}\n{traceback.format_exc()}")
