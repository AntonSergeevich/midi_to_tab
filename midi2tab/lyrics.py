"""
Текст песни из записи.

Работает Whisper (OpenAI, MIT) в реализации faster-whisper: та же модель,
но на CTranslate2 -- в несколько раз быстрее и заметно экономнее по
памяти, с квантизацией int8 на обычном процессоре.

Главное преимущество здесь не в модели, а в том, что ей дают на вход.
Demucs уже выделяет вокал отдельной дорожкой, и распознавать чистый
вокал вместо полного микса -- это другой уровень точности: музыка не
забивает речь, и Whisper перестаёт выдумывать слова там, где играет
гитара.

Возвращаются и строки, и отдельные слова со временем -- чтобы текст в
плеере подсвечивался по ходу песни, а не просто лежал полотном.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Размер модели против качества. Для пения на русском small -- разумный
# минимум; tiny и base на вокале путают слова слишком часто.
MODELS: dict[str, str] = {
    "tiny (75 МБ, черновик)": "tiny",
    "base (145 МБ)": "base",
    "small (480 МБ, быстро)": "small",
    "medium (1.5 ГБ, рекомендуется)": "medium",
    "large-v3 (3 ГБ, лучшее качество)": "large-v3",
}
DEFAULT_MODEL = "medium (1.5 ГБ, рекомендуется)"

# Языки, на которых чаще всего поют у нас. Указать язык прямо -- не
# мелочь: на пении автоопределение ошибается заметно чаще, чем на речи,
# и, приняв русский за украинский или болгарский, Whisper выдаёт
# правдоподобную бессмыслицу вместо текста.
LANGUAGES: dict[str, str | None] = {
    "русский": "ru",
    "английский": "en",
    "определить самому": None,
}
DEFAULT_LANGUAGE = "русский"


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float = 1.0


@dataclass
class Line:
    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Lyrics:
    lines: list[Line]
    language: str
    duration: float

    @property
    def text(self) -> str:
        return "\n".join(line.text.strip() for line in self.lines if line.text.strip())


def available() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False, (
            "Распознавание текста не установлено.\n"
            "Запустите install_lyrics.bat или выполните:\n"
            "    pip install faster-whisper"
        )
    return True, ""


def _device_and_type() -> tuple[str, str]:
    """На видеокарте float16, на процессоре int8 -- иначе неприемлемо медленно."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def language_code(name: str | None) -> str | None:
    """Название языка из настроек -> код для Whisper."""
    if not name:
        return LANGUAGES[DEFAULT_LANGUAGE]
    if name in LANGUAGES:
        return LANGUAGES[name]
    return name if len(name) == 2 else None


def transcribe(
    audio_path: str,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = "ru",
    cache_dir: str | None = None,
    progress=None,
) -> Lyrics:
    """
    Распознать текст. language=None -- определить язык самостоятельно.

    На вход лучше подавать выделенный вокал, а не весь трек.
    """
    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    from faster_whisper import WhisperModel

    size = MODELS.get(model, model)
    device, compute_type = _device_and_type()
    if progress:
        progress(f"Загружаю модель {size} ({device})...")

    engine = WhisperModel(
        size,
        device=device,
        compute_type=compute_type,
        download_root=cache_dir or os.environ.get("WHISPER_CACHE") or None,
    )

    if progress:
        progress("Разбираю вокал...")

    segments, info = engine.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,     # нужны, чтобы подсвечивать слова по ходу
        vad_filter=True,          # тишину и проигрыши не «расшифровываем»
        beam_size=5,
        # Ключевая настройка для песен. По умолчанию Whisper при разборе
        # каждого куска опирается на уже распознанный текст -- для речи
        # это помогает, а в песне губит: припев повторяется, модель
        # цепляется за собственный предыдущий вывод и уходит в петлю,
        # повторяя одну строчку до конца записи. На песнях это надо
        # выключать.
        condition_on_previous_text=False,
        # Если кусок всё же выродился в повтор -- перебрать с другой
        # температурой, а не оставлять мусор в тексте.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.2,
        no_speech_threshold=0.5,
        vad_parameters={
            # В пении паузы длиннее, чем в речи: между строчками легко
            # проходит секунда, и при коротком пороге VAD режет фразу
            # посередине слова.
            "min_silence_duration_ms": 700,
            "speech_pad_ms": 300,
        },
    )

    lines: list[Line] = []
    for segment in segments:
        words = [
            Word(
                text=w.word.strip(),
                start=round(w.start, 3),
                end=round(w.end, 3),
                probability=round(getattr(w, "probability", 1.0), 3),
            )
            for w in (segment.words or [])
        ]
        lines.append(
            Line(
                text=segment.text.strip(),
                start=round(segment.start, 3),
                end=round(segment.end, 3),
                words=words,
            )
        )
        if progress and len(lines) % 10 == 0:
            progress(f"Строк распознано: {len(lines)}")

    return Lyrics(
        lines=lines,
        language=getattr(info, "language", language or ""),
        duration=round(getattr(info, "duration", 0.0), 3),
    )


def to_dict(lyrics: Lyrics) -> dict:
    return {
        "language": lyrics.language,
        "duration": lyrics.duration,
        "lines": [
            {
                "text": line.text,
                "start": line.start,
                "end": line.end,
                "words": [
                    {"t": w.text, "s": w.start, "e": w.end, "p": w.probability}
                    for w in line.words
                ],
            }
            for line in lyrics.lines
        ],
    }
