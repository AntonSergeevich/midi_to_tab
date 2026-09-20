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


def available() -> tuple[bool, str]:
    """Установлен ли модуль распознавания."""
    try:
        import basic_pitch  # noqa: F401
    except ImportError:
        return False, (
            "Модуль распознавания аудио не установлен.\n"
            "Установите его командой:  pip install \"basic-pitch[onnx]\""
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
