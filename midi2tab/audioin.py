"""
Аудио -> MIDI.

Распознавание нот из записи инструмента моделью Basic Pitch (Spotify,
Apache-2.0, работает офлайн). Используется лёгкий ONNX-бэкенд: модель
весит 225 КБ, тяжёлый TensorFlow не нужен.

Ключевая настройка -- ограничение по частоте диапазоном выбранного
инструмента. Это заметно чище, чем распознавать всё подряд: модель не
выдумывает призрачные обертоны выше грифа и гул ниже самой низкой струны.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif")

# Заглушаем болтовню бэкендов до импорта
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# Сколько ядер отдавать одному распознаванию. onnxruntime по умолчанию
# забирает все, и на двухъядерном сервере два одновременных задания
# начинают драться за процессор: каждое ставит столько потоков, сколько
# ядер всего, и вместо работы они переключаются между собой. Двум
# заданиям на двух ядрах правильнее взять по одному и не мешать друг
# другу. Переменные читает и сам onnxruntime, и numpy с OpenMP внутри.
THREADS = os.environ.get("MIDI2TAB_THREADS", "1")
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "ORT_INTRA_OP_NUM_THREADS"):
    os.environ.setdefault(_name, THREADS)


@dataclass
class TranscribeSettings:
    """Настройки распознавания."""

    onset_threshold: float = 0.5      # порог атаки: выше -> меньше ложных нот
    frame_threshold: float = 0.3      # порог удержания ноты
    min_note_ms: float = 90.0         # короче -- считается шумом
    min_pitch: int | None = None      # ограничение снизу (MIDI)
    max_pitch: int | None = None      # ограничение сверху (MIDI)
    melodia_trick: bool = True        # сглаживание обрывов ноты
    tempo: float = 120.0


def _midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def is_audio(path: str) -> bool:
    return path.lower().endswith(AUDIO_EXTENSIONS)


def duration_seconds(path: str) -> float:
    """
    Длительность записи в секундах -- нужна для оценки времени обработки.

    Спрашиваем заголовок файла, а не читаем его целиком: на десятиминутном
    треке разница между "прочитать заголовок" и "распаковать всё" -- это
    миллисекунды против секунд. Если формат не опознан, прикидываем по
    размеру при 192 кбит/с: грубо, но лучше, чем ничего, ведь число идёт
    только в оценку процента выполнения.
    """
    try:
        import soundfile

        info = soundfile.info(path)
        if info.frames and info.samplerate:
            return info.frames / float(info.samplerate)
    except Exception:
        pass
    try:
        import librosa

        value = float(librosa.get_duration(path=path))
        if value > 0:
            return value
    except Exception:
        pass
    try:
        return os.path.getsize(path) / (192_000 / 8)
    except OSError:
        return 0.0


def available() -> tuple[bool, str]:
    """Установлен ли модуль распознавания."""
    try:
        import basic_pitch  # noqa: F401
    except ImportError:
        return False, (
            "Модуль распознавания аудио не установлен.\n"
            "Запустите install_audio.bat или выполните две команды:\n"
            "    pip install -r requirements-audio.txt\n"
            "    pip install --no-deps basic-pitch\n"
            "Ключ --no-deps обязателен: в метаданных basic-pitch прописан "
            "tensorflow, которого нет под Python 3.13, хотя на ONNX он не нужен."
        )
    return True, ""


def _model_path():
    """Путь к ONNX-модели, с откатом на любой доступный бэкенд."""
    import basic_pitch

    if getattr(basic_pitch, "ONNX_PRESENT", False):
        return basic_pitch.build_icassp_2022_model_path(basic_pitch.FilenameSuffix.onnx)
    return basic_pitch.ICASSP_2022_MODEL_PATH


def transcribe(
    audio_path: str,
    midi_out: str,
    settings: TranscribeSettings | None = None,
    progress=None,
) -> tuple[str, int]:
    """
    Распознать аудио и сохранить MIDI.

    Возвращает (путь к MIDI, число распознанных нот).
    """
    cfg = settings or TranscribeSettings()
    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    if progress:
        progress("Загружаю модель распознавания...")

    from basic_pitch.inference import predict

    if progress:
        progress("Анализирую аудио (это может занять минуту)...")

    _, midi_data, _ = predict(
        audio_path,
        model_or_model_path=_model_path(),
        onset_threshold=cfg.onset_threshold,
        frame_threshold=cfg.frame_threshold,
        minimum_note_length=cfg.min_note_ms,
        minimum_frequency=_midi_to_hz(cfg.min_pitch) if cfg.min_pitch else None,
        maximum_frequency=_midi_to_hz(cfg.max_pitch) if cfg.max_pitch else None,
        melodia_trick=cfg.melodia_trick,
        midi_tempo=cfg.tempo,
    )

    note_count = sum(len(inst.notes) for inst in midi_data.instruments)
    midi_data.write(midi_out)
    if progress:
        progress(f"Распознано нот: {note_count}")
    return midi_out, note_count
