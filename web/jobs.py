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
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from midi2tab import audiochords, audioin, cleanup, gp5out, lyrics as lyrics_mod, midiin, separate
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


class JobRunner:
    def __init__(self, storage: Storage, data_dir: str, workers: int = 2) -> None:
        self.storage = storage
        self.data_dir = data_dir
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit_analysis(self, job_id: str, source_path: str) -> None:
        self.pool.submit(self._analyze, job_id, source_path)

    def submit_tabs(self, job_id: str, parent_id: str, stem_key: str) -> None:
        self.pool.submit(self._tabs, job_id, parent_id, stem_key)

    def submit_lyrics(self, job_id: str, model: str) -> None:
        self.pool.submit(self._lyrics, job_id, model)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    # --------------------------------------------------------------- разбор

    def _stage(self, job_id: str, text: str) -> None:
        self.storage.update_job(job_id, stage=text)

    def _analyze(self, job_id: str, source_path: str) -> None:
        job = self.storage.job(job_id)
        if job is None:
            return
        out_dir = os.path.join(self.data_dir, "results", job_id)
        os.makedirs(out_dir, exist_ok=True)
        self.storage.update_job(job_id, status="running", stage="Начинаю разбор...")

        try:
            options = job.settings or {}
            is_midi = source_path.lower().endswith(MIDI_SUFFIXES)
            parts: list[dict] = []
            chords: list[dict] = []
            beats: list[float] = []
            downbeats: list[float] = []
            tempo = int(options.get("tempo") or 0)

            # 1. Разделение на партии
            if not is_midi and options.get("separate"):
                ok, why = separate.available()
                if not ok:
                    raise RuntimeError(why)
                self._stage(job_id, "Делю трек на партии...")
                result = separate.separate(
                    source_path,
                    os.path.join(out_dir, "stems"),
                    options.get("model", separate.DEFAULT_MODEL),
                    progress=lambda m: self._stage(job_id, m),
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
                self._stage(job_id, "Слушаю аккорды...")
                analysis = audiochords.detect_from_audio(
                    source_path,
                    min_duration=float(options.get("minChord", audiochords.MIN_DURATION)),
                    progress=lambda m: self._stage(job_id, m),
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
                if not tempo and analysis.tempo:
                    tempo = int(round(analysis.tempo))

            result_data = {
                "kind": "analysis",
                "isMidi": is_midi,
                "tempo": tempo or 120,
                "tempoDetected": bool(not options.get("tempo") and tempo),
                "chords": chords,
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
            self.storage.update_job(
                job_id, status="done", stage="Готово", result=result_data, error=None
            )
        except Exception as exc:
            self.storage.update_job(job_id, status="error", stage="", error=str(exc))
            print(f"[analyze {job_id}] {exc}\n{traceback.format_exc()}")

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
        source = paths.get("vocals") or job.result.get("paths", {}).get("source")
        if not source or not os.path.isfile(source):
            self.storage.update_job(job_id, error="Нет дорожки для распознавания текста")
            return

        used_vocals = "vocals" in paths
        self._stage(job_id, "Распознаю текст...")
        try:
            result = lyrics_mod.transcribe(
                source,
                model=model,
                cache_dir=os.path.join(self.data_dir, "models"),
                progress=lambda m: self._stage(job_id, m),
            )
            payload = dict(job.result)
            payload["lyrics"] = lyrics_mod.to_dict(result)
            payload["lyricsSource"] = "вокальная дорожка" if used_vocals else "весь трек"
            self.storage.update_job(job_id, result=payload, stage="Текст готов")
        except Exception as exc:
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
        self.storage.update_job(job_id, status="running", stage="Распознаю ноты...")

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
            converted = convert(settings, progress=lambda m: self._stage(job_id, m))
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
            self.storage.update_job(
                job_id, status="done", stage="Готово", result=result_data, error=None
            )
        except Exception as exc:
            self.storage.update_job(job_id, status="error", stage="", error=str(exc))
            print(f"[tabs {job_id}] {exc}\n{traceback.format_exc()}")
