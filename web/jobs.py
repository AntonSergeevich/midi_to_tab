"""
Очередь заданий.

Обработка песни занимает минуты (разделение дорожек, распознавание), а
HTTP-запрос столько ждать не может. Поэтому загрузка сразу возвращает
номер задания, работа идёт в пуле потоков, а страница опрашивает статус.

Пул намеренно небольшой: разделение и распознавание упираются в процессор,
и десяток параллельных задач не ускорит ничего, а память съест.
"""

from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from midi2tab import arrange, audioin, chords, cleanup, gp5out, midiin, separate
from midi2tab.convert import Settings, convert
from midi2tab.timing import TPQ

from .storage import Storage


def build_player_data(placements, board, tempo, notes, time_signatures) -> dict:
    """
    Подготовить данные для плеера: аккорды и колонки табулатуры со временем.

    Всё время переводится в секунды -- фронтенд сравнивает их напрямую с
    currentTime аудио, без пересчётов на своей стороне.
    """
    factor = 60.0 / max(1, tempo) / TPQ

    chord_spans = chords.detect(notes)
    chord_list = [
        {
            "name": span.name,
            "start": round(span.start * factor, 3),
            "end": round(span.end * factor, 3),
            "confidence": round(span.confidence, 2),
        }
        for span in chord_spans
    ]

    spans = gp5out.build_spans(placements, 12)
    # pitch передаём явно: браузер по нему синтезирует звук, когда
    # исходник -- MIDI (проиграть .mid встроенными средствами он не умеет)
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
        {
            "number": i + 1,
            "start": round(start * factor, 3),
            "end": round(end * factor, 3),
            "sig": f"{num}/{den}",
        }
        for i, (start, end, num, den) in enumerate(plan)
    ]

    return {
        "tempo": tempo,
        "strings": board.string_count,
        "tuning": board.describe(),
        "duration": round(total * factor, 3),
        "chords": chord_list,
        "columns": columns,
        "measures": measures,
    }


class JobRunner:
    """Выполняет задания и складывает результат в хранилище."""

    def __init__(self, storage: Storage, data_dir: str, workers: int = 2) -> None:
        self.storage = storage
        self.data_dir = data_dir
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(self, job_id: str, source_path: str) -> None:
        self.pool.submit(self._run, job_id, source_path)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ работа

    def _stage(self, job_id: str, text: str) -> None:
        self.storage.update_job(job_id, stage=text)

    def _run(self, job_id: str, source_path: str) -> None:
        job = self.storage.job(job_id)
        if job is None:
            return
        out_dir = os.path.join(self.data_dir, "results", job_id)
        os.makedirs(out_dir, exist_ok=True)
        self.storage.update_job(job_id, status="running", stage="Начинаю...")

        try:
            options = job.settings or {}
            working = source_path

            # 1. Разделение на дорожки -- если попросили и если оно установлено
            stem_url = None
            if options.get("separate") and not working.lower().endswith((".mid", ".midi")):
                ok, why = separate.available()
                if not ok:
                    raise RuntimeError(why)
                self._stage(job_id, "Разделяю трек на дорожки...")
                result = separate.separate(
                    working,
                    os.path.join(out_dir, "stems"),
                    options.get("model", separate.DEFAULT_MODEL),
                    progress=lambda m: self._stage(job_id, m),
                )
                picked = result.guitar or next(iter(result.stems.values()))
                working = picked
                stem_url = f"/api/file/{job_id}/stem"

            # 2. Основной конвейер
            settings = Settings(
                input_path=working,
                output_dir=out_dir,
                tuning=options.get("tuning", Settings.tuning),
                capo=int(options.get("capo", 0)),
                tempo=int(options.get("tempo", 0)),
                grid=options.get("grid", Settings.grid),
                limit_to_range=bool(options.get("limitRange", True)),
                remove_ghosts=bool(options.get("removeGhosts", True)),
                max_polyphony=int(options.get("maxPolyphony", 0)),
                let_ring=bool(options.get("letRing", False)),
            )
            self._stage(job_id, "Раскладываю по грифу...")
            converted = convert(settings, progress=lambda m: self._stage(job_id, m))

            # 3. Данные для плеера
            self._stage(job_id, "Размечаю аккорды...")
            midi_path = converted.midi_path or working
            doc = midiin.load(midi_path)
            notes = doc.merged_notes(
                [t.index for t in doc.tracks if t.note_count and not t.is_drum]
            )
            if options.get("removeGhosts", True) and audioin.is_audio(working):
                notes, _ = cleanup.clean(notes)

            board = settings.fretboard()
            player = build_player_data(
                converted.placements, board, converted.tempo, notes, doc.time_signatures
            )

            has_audio = not working.lower().endswith((".mid", ".midi"))
            result_data = {
                "player": player,
                "hasAudio": has_audio,
                "summary": converted.summary,
                "tab": converted.tab_text,
                "audio": stem_url or f"/api/file/{job_id}/source",
                "files": {
                    "gp5": bool(converted.gp5_path),
                    "txt": bool(converted.txt_path),
                    "mid": bool(converted.midi_path),
                },
                "paths": {
                    "gp5": converted.gp5_path,
                    "txt": converted.txt_path,
                    "mid": converted.midi_path,
                    "source": source_path,
                    "stem": working if stem_url else None,
                },
            }
            self.storage.update_job(
                job_id, status="done", stage="Готово", result=result_data, error=None
            )
        except Exception as exc:
            self.storage.update_job(
                job_id,
                status="error",
                stage="",
                error=f"{exc}",
            )
            print(f"[job {job_id}] {exc}\n{traceback.format_exc()}")
