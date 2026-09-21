"""
Разделение готового трека на дорожки инструментов.

Работает Demucs (Meta, лицензия MIT). Модель htdemucs_6s вытаскивает
шесть дорожек, включая ОТДЕЛЬНУЮ ГИТАРУ -- это важнее всего остального
для качества: распознавать чистый гитарный стем несравнимо точнее, чем
угадывать гитару внутри микса, где её перекрывают вокал и барабаны.

Разделение тяжёлое: на обычном процессоре минута музыки обрабатывается
примерно минуту. Поэтому оно вынесено в отдельный шаг с возможностью
сохранить стемы и переиспользовать их.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Дорожки моделей: внутреннее имя -> как показывать
STEM_NAMES: dict[str, str] = {
    "guitar": "Гитара",
    "bass": "Бас",
    "drums": "Барабаны",
    "vocals": "Вокал",
    "piano": "Фортепиано",
    "other": "Остальное",
}

MODELS: dict[str, str] = {
    "htdemucs_6s (с отдельной гитарой)": "htdemucs_6s",
    "htdemucs (4 дорожки, быстрее)": "htdemucs",
    "htdemucs_ft (4 дорожки, точнее и дольше)": "htdemucs_ft",
}
DEFAULT_MODEL = "htdemucs_6s (с отдельной гитарой)"

# Качество разделения покупается временем, и честнее показать эту цену,
# чем решать за человека. Числа -- это (перекрытие кусков, число сдвигов,
# во сколько раз дольше самой музыки на двух ядрах).
QUALITY: dict[str, tuple[float, int, float]] = {
    "быстро": (0.25, 1, 1.2),
    "точнее": (0.50, 2, 3.6),
}
DEFAULT_QUALITY = "точнее"


@dataclass
class SeparateResult:
    stems: dict[str, str]  # внутреннее имя -> путь к wav
    model: str
    out_dir: str

    def label_for(self, key: str) -> str:
        return STEM_NAMES.get(key, key)

    @property
    def guitar(self) -> str | None:
        return self.stems.get("guitar")


def available() -> tuple[bool, str]:
    try:
        import demucs.separate  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False, (
            "Разделение на дорожки не установлено.\n"
            "Запустите install_separation.bat или выполните:\n"
            "    pip install demucs\n"
            "Тянет PyTorch (около 300 МБ), ставится один раз."
        )
    return True, ""


def separate(
    audio_path: str,
    out_dir: str,
    model: str = DEFAULT_MODEL,
    quality: str = DEFAULT_QUALITY,
    progress=None,
) -> SeparateResult:
    """
    Разложить трек на дорожки. Возвращает пути к полученным wav.

    Файлы кладутся в <out_dir>/<модель>/<имя трека>/<дорожка>.wav --
    так их раскладывает сам Demucs.
    """
    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    model_name = MODELS.get(model, model)
    os.makedirs(out_dir, exist_ok=True)

    overlap, shifts, _ = QUALITY.get(quality, QUALITY[DEFAULT_QUALITY])

    if progress:
        progress(f"Разделяю трек моделью {model_name}. Это самый долгий шаг...")

    import demucs.separate

    argv = [
        "--out", out_dir,
        "-n", model_name,
        "--device", _device(),
        # Модель слушает трек кусками, и на стыках кусков она ошибается
        # сильнее всего. Увеличенное перекрытие даёт каждому мгновению
        # попасть в середину куска хотя бы раз, а усреднение по сдвигам
        # (shifts) гасит то, что зависит от случайной фазы нарезки. Для
        # гитары это заметнее, чем для остальных дорожек: её отделяют от
        # клавиш и подпевок, а не от баса с барабанами, и остаток вылезает
        # именно призвуками на стыках.
        "--overlap", str(overlap),
        "--shifts", str(shifts),
        audio_path,
    ]
    demucs.separate.main(argv)

    stem_dir = Path(out_dir) / model_name / Path(audio_path).stem
    if not stem_dir.is_dir():
        raise RuntimeError(
            f"Demucs отработал, но папка с дорожками не найдена: {stem_dir}"
        )

    stems = {p.stem: str(p) for p in sorted(stem_dir.glob("*.wav"))}
    if not stems:
        raise RuntimeError(f"В папке {stem_dir} нет ни одной дорожки.")

    for key, file_path in stems.items():
        if key in BANDS:
            if progress:
                progress(f"Дочищаю дорожку: {STEM_NAMES.get(key, key)}...")
            polish(file_path, key)

    if progress:
        names = ", ".join(STEM_NAMES.get(k, k) for k in stems)
        progress(f"Готово, получено дорожек: {len(stems)} ({names})")

    return SeparateResult(stems=stems, model=model_name, out_dir=str(stem_dir))


# Что мешает слышать гармонию. Барабаны размазывают спектр, а голос --
# худший враг разбора аккордов: певец тянет ноту поверх аккорда, и эта
# нота читается как надстройка. Так трезвучие превращается в maj7, а
# круг песни -- в свалку из двух десятков подписей.
NOT_HARMONY = ("drums", "vocals")


def harmonic_mix(stems: dict[str, str], out_path: str) -> str | None:
    """
    Собрать дорожку без барабанов и голоса -- только гармония.

    Это самый дешёвый способ поднять качество разбора аккордов из всех,
    что у нас есть: модель отделять ничего не нужно, стемы уже посчитаны.
    Слушать гармонию в такой дорожке -- совсем не то же самое, что в
    полном миксе, где её перекрывает всё остальное.
    """
    keep = [path for key, path in stems.items() if key not in NOT_HARMONY]
    if not keep:
        return None
    try:
        import numpy as np
        import soundfile
    except ImportError:
        return None

    try:
        total = None
        rate = None
        for path in keep:
            audio, sample_rate = soundfile.read(path, always_2d=True)
            mono = audio.mean(axis=1)
            if total is None:
                total, rate = mono, sample_rate
            elif sample_rate == rate:
                length = min(len(total), len(mono))
                total = total[:length] + mono[:length]
        if total is None:
            return None
        peak = float(np.max(np.abs(total))) or 1.0
        soundfile.write(out_path, (total / peak * 0.9).astype("float32"), rate)
    except Exception:
        return None
    return out_path


def polish(path: str, kind: str) -> str:
    """
    Дочистить дорожку по диапазону инструмента.

    Demucs оставляет в гитаре следы соседей: низ от баса и бочки, верх от
    тарелок и шипения. Для слуха это мелочь, а для распознавания нот --
    нет: Basic Pitch принимает низкий гул за басовую ноту и рисует её в
    табы. Режем всё, чего у инструмента быть не может.

    Дорисовывать недостающие призвуки нейросетью -- соблазнительно, но
    вредно: она додумает ноты, которых в записи не было, и они окажутся в
    табах как настоящие. Здесь лучше убрать лишнее, чем добавить своё.
    """
    band = BANDS.get(kind)
    if not band:
        return path
    try:
        import numpy as np
        import soundfile
        from scipy.signal import butter, sosfiltfilt
    except ImportError:
        return path

    try:
        audio, rate = soundfile.read(path, always_2d=True)
        low, high = band
        high = min(high, rate / 2 * 0.98)
        if low >= high:
            return path
        sos = butter(4, [low / (rate / 2), high / (rate / 2)], btype="band", output="sos")
        cleaned = sosfiltfilt(sos, audio, axis=0)
        peak = float(np.max(np.abs(cleaned))) or 1.0
        if peak > 1.0:
            cleaned = cleaned / peak
        soundfile.write(path, cleaned.astype("float32"), rate)
    except Exception:
        return path
    return path


# Рабочий диапазон инструмента в герцах: ниже и выше -- заведомо не он.
# Гитара: нижняя ми шестой струны 82 Гц, верхние обертоны до 6 кГц.
BANDS: dict[str, tuple[float, float]] = {
    "guitar": (75.0, 6000.0),
    "bass": (30.0, 1200.0),
    "piano": (27.0, 8000.0),
}


def _device() -> str:
    """Видеокарта ускоряет разделение в разы, но не обязательна."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def describe_device() -> str:
    device = _device()
    return "видеокарта (быстро)" if device == "cuda" else "процессор (медленно)"
