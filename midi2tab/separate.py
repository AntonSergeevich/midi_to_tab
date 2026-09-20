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

    if progress:
        progress(f"Разделяю трек моделью {model_name}. Это самый долгий шаг...")

    import demucs.separate

    argv = [
        "--out", out_dir,
        "-n", model_name,
        "--device", _device(),
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

    if progress:
        names = ", ".join(STEM_NAMES.get(k, k) for k in stems)
        progress(f"Готово, получено дорожек: {len(stems)} ({names})")

    return SeparateResult(stems=stems, model=model_name, out_dir=str(stem_dir))


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
