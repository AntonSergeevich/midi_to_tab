#!/usr/bin/env python3
"""
Замер: даёт ли каскад Roformer -> Demucs лучшую гитару, чем один Demucs.

Готовой модели, которая выделяет гитару отдельным стемом, кроме
htdemucs_6s, в природе нет -- это выяснилось при разборе рынка. Но есть
другой путь: RoFormer'ы (BS-RoFormer, Mel-Band RoFormer) выделяют вокал
заметно чище Demucs (11-12 дБ SDR против ~9.7). Если сначала снять вокал
таким способом, а потом отдать ОСТАВШИЙСЯ инструментал в htdemucs_6s за
гитарой -- у него меньше повода спутать высокий вокал с высокими нотами
гитары. Это не гарантия, а гипотеза, и проверить её можно только ушами
на конкретной записи -- поэтому скрипт не выставляет оценку, а готовит
два файла для сравнения и говорит, сколько это стоило времени и памяти.

Способ (А) -- то, что уже стоит в проде, сам по себе:
    трек -> htdemucs_6s -> гитара

Способ (Б) -- каскад:
    трек -> RoFormer (вокал/инструментал) -> инструментал -> htdemucs_6s -> гитара

Каждый шаг считается ОТДЕЛЬНЫМ процессом: так у каждого свой честный
пик памяти (resource.getrusage внутри процесса, а не общий на всю
программу), и загрузка модели одним шагом не искажает время другого.

Запуск (на сервере, из venv):
    /opt/nasluh/venv/bin/python scripts/compare_separation.py песня.mp3

Результат ложится в отдельную папку рядом с записью:
    <имя>_сравнение/a_htdemucs/guitar.wav
    <имя>_сравнение/b_roformer_cascade/guitar.wav
    <имя>_сравнение/отчёт.json

Слушайте оба guitar.wav подряд и решайте на слух -- разница, которую
не слышно, не стоит ни времени, ни памяти, которых каскад просит больше.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Модель по умолчанию для вокала -- BS-RoFormer, 12.97 дБ SDR на
# MUSDB18HQ (для сравнения: вокал у htdemucs_6s -- около 9.7 дБ). Это
# буквальный дефолт самого audio-separator внутри load_model(), то есть
# самая обкатанная связка в самом пакете. Имена файлов моделей в UVR
# меняются между версиями, поэтому скрипт при старте СВЕРЯЕТ это имя со
# списком, который пакет знает прямо сейчас, а не верит ему вслепую --
# иначе несовпадение вскрылось бы через десять минут скачивания, а не сразу.
DEFAULT_VOCAL_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

STAGE_FLAG = "--_run-stage"


@dataclass
class StageResult:
    """Что стоил один шаг: время, память, куда лёг результат."""

    name: str
    seconds: float
    peak_mb: float
    output: str
    ok: bool
    error: str = ""

    def line(self) -> str:
        if not self.ok:
            return f"  {self.name:<28} ОШИБКА: {self.error}"
        return f"  {self.name:<28} {self.seconds:7.1f} с   пик памяти {self.peak_mb:6.0f} МБ"


def _peak_mb() -> float:
    """
    Пик памяти ЭТОГО процесса с его старта, в мегабайтах.

    resource -- часть стандартной библиотеки, ничего доставлять не
    нужно. На Linux ru_maxrss дан в килобайтах (на macOS -- в байтах,
    но сервер линуксовый, и лишняя ветка тут не нужна).
    """
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _run_stage_subprocess(stage: str, args: list[str]) -> StageResult:
    """
    Запустить один шаг ОТДЕЛЬНЫМ процессом и забрать у него честный отчёт.

    Так пик памяти каждого шага измеряется сам по себе, а не как общий
    максимум на всю программу -- иначе каскад из трёх шагов выглядел бы
    втрое прожорливее, чем любой из них по отдельности на самом деле.
    """
    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), STAGE_FLAG, stage, *args],
        capture_output=True, text=True,
    )
    elapsed = time.time() - started
    # Последняя строка stdout -- JSON-отчёт самого шага; всё, что раньше,
    # это его собственный прогресс. Шаг печатает отчёт ДО того, как может
    # упасть кодом возврата (см. _run_stage_entrypoint) -- поэтому сперва
    # ищем валидный JSON и только если его нет вовсе, берём сырой stderr
    # с трейсбеком. Иначе понятное "модели нет в списке" пряталось бы
    # под полным трейсбеком только потому, что процесс вышел не нулём.
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    payload = None
    if lines:
        try:
            payload = json.loads(lines[-1])
        except ValueError:
            payload = None

    if payload is not None:
        if payload.get("ok"):
            return StageResult(stage, elapsed, payload["peak_mb"], payload["output"], True)
        return StageResult(stage, elapsed, payload.get("peak_mb", 0.0), "",
                           False, payload.get("error", "неизвестная ошибка"))

    detail = (proc.stderr or proc.stdout or "процесс не вернул ничего").strip()
    return StageResult(stage, elapsed, 0.0, "", False, detail[-500:])


# --------------------------------------------------------------- сами шаги
# Каждая функция ниже запускается как отдельный процесс (см. main() и
# ветку "--_run-stage") и печатает СВОЙ отчёт последней строкой в stdout.


def _stage_demucs(audio_path: str, out_dir: str) -> dict:
    """
    Способ (А): трек напрямую в htdemucs_6s, взять гитару.

    Параметры берутся из midi2tab.separate -- НЕ повторены вручную,
    а именно импортированы. Иначе замер незаметно разъехался бы с тем,
    что реально крутится у людей: поменяй кто-то потом качество или
    длину куска в проде, этот скрипт продолжал бы сравнивать со старыми
    числами и врал бы о том, что улучшилось на самом деле.
    """
    import demucs.separate

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from midi2tab.separate import DEFAULT_QUALITY, QUALITY, SEGMENT

    overlap, shifts, _ = QUALITY[DEFAULT_QUALITY]

    os.makedirs(out_dir, exist_ok=True)
    demucs.separate.main([
        "--out", out_dir, "-n", "htdemucs_6s", "--device", "cpu",
        "--overlap", str(overlap), "--shifts", str(shifts),
        "--segment", str(SEGMENT),
        audio_path,
    ])
    guitar = Path(out_dir) / "htdemucs_6s" / Path(audio_path).stem / "guitar.wav"
    if not guitar.is_file():
        raise RuntimeError(f"Demucs отработал, но файла нет: {guitar}")
    return {"output": str(guitar)}


def _model_cache_dir() -> str:
    """
    Куда класть скачанные веса моделей.

    По умолчанию пакет использует /tmp, а его на многих серверах чистят
    при перезагрузке или по расписанию. Замер обычно гоняют не один раз
    -- на разных песнях, с разными моделями для сравнения, -- и качать
    заново вес в 200-400 МБ на каждый такой запуск обидно. Кладём в
    домашний кэш, он переживёт перезагрузку.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "audio-separator-models")
    os.makedirs(path, exist_ok=True)
    return path


def _disarm_beartype() -> None:
    """
    Обезвредить рантайм-проверки beartype ПЕРЕД импортом audio_separator.

    В установленной версии пакета (0.47.0) конструктор BSRoformer
    размечен как `stft_window_fn: Optional[Callable] = None` через
    `beartype.typing` -- по спецификации всё верно, но конкретная связка
    версий beartype/Python в этом окружении не умеет проверить именно
    этот тип и падает уже на создании модели:
    "type hint collections.abc.Callable | None either PEP-noncompliant
    or currently unsupported by @beartype". Баг в самой связке версий,
    не в коде -- апстрим ещё не выпустил тег с починкой (она есть только
    в main, не в 0.47.0), а понижать/поднимать beartype на сервере
    рискованно: от него зависят и другие уже установленные пакеты.

    Подменяем `beartype.beartype` на пустой декоратор ДО первого импорта
    любого модуля audio_separator -- `from beartype import beartype`
    внутри него подхватит уже подмененное имя. Это только отключает
    проверку типов (которая тут просто мешает), саму модель не трогает.
    Замер -- разовый диагностический скрипт, не часть сервиса, поэтому
    такой обход здесь уместен.
    """
    try:
        import beartype
    except ImportError:
        # Настоящий audio_separator тянет beartype обязательной
        # зависимостью, так что на сервере он есть всегда. Отсутствует
        # он только в тестах с поддельным audio_separator -- там
        # подменять нечего, и это не ошибка.
        return

    def _noop(func=None, **_kwargs):
        return func if func is not None else (lambda f: f)

    beartype.beartype = _noop


def _stage_roformer_vocal_split(audio_path: str, out_dir: str, model: str) -> dict:
    """Первый шаг каскада: вычленить чистый инструментал без вокала."""
    _disarm_beartype()
    from audio_separator.separator import Separator

    os.makedirs(out_dir, exist_ok=True)
    separator = Separator(
        output_dir=out_dir, output_format="WAV", model_file_dir=_model_cache_dir()
    )

    # Список моделей, которые пакет знает ПРЯМО СЕЙЧАС -- сверяемся с
    # ним, а не верим переданному имени вслепую: имена файлов в UVR
    # меняются между версиями, и без проверки несовпадение вскрылось бы
    # только через десять минут неудачной попытки скачать файл, а не сразу.
    # Форма ответа -- {архитектура: {человеческое имя: {"filename": ..., ...}}}.
    available = separator.list_supported_model_files()
    known_files = {
        info.get("filename")
        for arch in available.values() if isinstance(arch, dict)
        for info in arch.values() if isinstance(info, dict)
    }
    known_files.discard(None)
    if known_files and model not in known_files:
        sample = ", ".join(sorted(known_files)[:8])
        raise RuntimeError(
            f"Модели {model!r} в списке audio-separator нет. "
            f"Первые доступные: {sample}. Передайте верное имя через --vocal-model."
        )

    separator.load_model(model_filename=model)
    # separate() возвращает имена файлов ОТНОСИТЕЛЬНО output_dir, а не
    # готовые пути -- сам файл при записи склеивается с output_dir внутри
    # пакета отдельно.
    produced = separator.separate(audio_path)
    instrumental = next((n for n in produced if "instrumental" in n.lower()), None)
    if instrumental is None:
        # Не угадали по имени -- берём тот, что явно не вокал
        instrumental = next((n for n in produced if "vocal" not in n.lower()), None)
    if instrumental is None:
        raise RuntimeError(f"Не нашёл инструментал среди выданных файлов: {produced}")
    path = Path(out_dir) / instrumental
    if not path.is_file():
        raise RuntimeError(f"Файл не записался: {path}")
    return {"output": str(path)}


def _run_stage_entrypoint(stage: str, argv: list[str]) -> None:
    """Точка входа шага, вызванного как подпроцесс: печатает отчёт и выходит."""
    try:
        if stage == "demucs":
            audio_path, out_dir = argv
            result = _stage_demucs(audio_path, out_dir)
        elif stage == "roformer":
            audio_path, out_dir, model = argv
            result = _stage_roformer_vocal_split(audio_path, out_dir, model)
        else:
            raise ValueError(f"неизвестный шаг: {stage}")
        print(json.dumps({"ok": True, "peak_mb": _peak_mb(), **result}))
    except Exception as exc:  # noqa: BLE001 -- отчёт нужен любой ценой
        print(json.dumps({"ok": False, "peak_mb": _peak_mb(), "error": str(exc)}))
        raise


# ------------------------------------------------------------------ оркестр


def compare(audio_path: str, work_dir: Path, vocal_model: str) -> dict:
    print(f"Файл: {audio_path}")
    print(f"Модель для вокала: {vocal_model}\n")

    print("(А) htdemucs_6s напрямую...")
    a_dir = work_dir / "a_htdemucs"
    a = _run_stage_subprocess("demucs", [audio_path, str(a_dir)])
    print(a.line())

    print("\n(Б) каскад: RoFormer -> htdemucs_6s...")
    b_dir = work_dir / "b_roformer_cascade"
    step1 = _run_stage_subprocess(
        "roformer", [audio_path, str(b_dir / "1_vocal_split"), vocal_model]
    )
    print("  " + step1.line().strip())
    if not step1.ok:
        b_guitar = StageResult("демукс по инструменталу", 0.0, 0.0, "", False,
                               "пропущено -- не вышло снять вокал")
    else:
        b_guitar = _run_stage_subprocess(
            "demucs", [step1.output, str(b_dir / "2_demucs")]
        )
        print("  " + b_guitar.line().strip())

    report = {
        "файл": audio_path,
        "модель_вокала": vocal_model,
        "а_htdemucs": asdict(a),
        "б_снятие_вокала": asdict(step1),
        "б_демукс_по_остатку": asdict(b_guitar),
    }

    print("\n" + "-" * 60)
    if a.ok and b_guitar.ok:
        total_b = step1.seconds + b_guitar.seconds
        peak_b = max(step1.peak_mb, b_guitar.peak_mb)
        print(f"(А) htdemucs_6s:     {a.seconds:7.1f} с   пик {a.peak_mb:6.0f} МБ")
        print(f"(Б) каскад целиком:  {total_b:7.1f} с   пик {peak_b:6.0f} МБ  "
              f"(дороже в {total_b / max(a.seconds, 0.01):.1f} раза по времени)")
        print(f"\nГитара (А): {a.output}")
        print(f"Гитара (Б): {b_guitar.output}")
        print("\nПослушайте оба файла подряд. Если разницы на слух нет -- каскад")
        print("того не стоит: он честно дороже по времени и памяти, а выигрыш")
        print("не гарантирован самой природой метода.")
    else:
        print("Сравнить не вышло -- смотрите ошибки выше.")
        if not a.ok:
            print(f"  (А): {a.error}")
        if not step1.ok:
            print(f"  (Б, шаг 1): {step1.error}")
        elif not b_guitar.ok:
            print(f"  (Б, шаг 2): {b_guitar.error}")

    return report


def main() -> None:
    if STAGE_FLAG in sys.argv:
        index = sys.argv.index(STAGE_FLAG)
        _run_stage_entrypoint(sys.argv[index + 1], sys.argv[index + 2:])
        return

    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio", help="mp3/wav/flac -- запись для сравнения")
    parser.add_argument("--vocal-model", default=DEFAULT_VOCAL_MODEL,
                        help="имя модели audio-separator для первого шага каскада")
    parser.add_argument("--out-dir", default=None,
                        help="куда положить результаты (по умолчанию рядом с записью)")
    args = parser.parse_args()

    audio = Path(args.audio).resolve()
    if not audio.is_file():
        sys.exit(f"Файла нет: {audio}")

    work_dir = Path(args.out_dir).resolve() if args.out_dir else (
        audio.parent / f"{audio.stem}_сравнение"
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    report = compare(str(audio), work_dir, args.vocal_model)
    report_path = work_dir / "отчёт.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт сохранён: {report_path}")


if __name__ == "__main__":
    main()
