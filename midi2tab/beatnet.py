"""
Доли, сильные доли и темп -- нейросетью Beat This (CPJKU, MIT, ISMIR 2024).

Прежний способ -- librosa.beat.beat_track -- ищет темп около 120 и на
«качающихся» песнях цепляется за соседнюю пульсацию: 81 читался как 108
или 123, ровные 81 -- как 161. Beat This слушает саму музыку и отдельно
отмечает «раз» такта.

Модель переведена в ONNX и сжата до int8 (scripts/beatnet/export.py,
beat-eval.yml), поэтому на сервере хватает onnxruntime, как и для
аккордов; torch не нужен. Замер 29.09.2026 (темп в пределах 4 %):
GuitarSet 0.57 -> 0.91, AAM 0.63 -> 0.78, синтетика с шаффлом 0.47 ->
0.91; int8 на синтетике совпал с полной моделью. ~2.5 с на минуту звука.
Лицензия весов -- MIT, models/beatthis.LICENSE. Нет файла модели
-- track() возвращает None, и разбор идёт по-старому, через librosa.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

MODEL = Path(__file__).resolve().parent / "models" / "beatthis.onnx"

# Предобработка -- как у Beat This (beat_this/preprocessing.py, LogMelSpect)
SR = 22050
N_FFT = 1024
HOP = 441            # 50 кадров в секунду
FPS = SR / HOP
N_MELS = 128
F_MIN, F_MAX = 30, 11000
# Кусок, на котором училась модель, и поля, которые она не видит толком
CHUNK = 1500
BORDER = 6


@lru_cache(maxsize=1)
def _session():
    if os.environ.get("NASLUX_BEATS", "net") != "net" or not MODEL.is_file():
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    return ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])


def available() -> bool:
    return _session() is not None


def spectrogram(y, sr: int = SR):
    """Лог-мел спектрограмма (кадры x 128), совпадает с torchaudio
    MelSpectrogram(power=1, normalized="frame_length", mel_scale="slaney")."""
    import librosa
    import numpy as np

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    stft = np.abs(librosa.stft(np.asarray(y, dtype=np.float32), n_fft=N_FFT, hop_length=HOP,
                               window="hann", center=True, pad_mode="reflect")) / np.sqrt(N_FFT)
    mel = librosa.filters.mel(sr=SR, n_fft=N_FFT, n_mels=N_MELS, fmin=F_MIN, fmax=F_MAX,
                              htk=False, norm=None) @ stft
    return np.log1p(1000.0 * mel).T.astype(np.float32)


def logits(spect):
    """Логиты «доля» и «сильная доля» на каждый кадр. Длинная запись --
    кусками по 30 секунд с перекрытием в поля, как в beat_this.inference."""
    import numpy as np

    session = _session()
    total = len(spect)
    step = CHUNK - 2 * BORDER
    starts = list(range(-BORDER, total - BORDER, step))
    if total > step:
        starts[-1] = total - (CHUNK - BORDER)
    beat = np.full(total, -1000.0, dtype=np.float32)
    down = np.full(total, -1000.0, dtype=np.float32)
    # Раньше начатый кусок главнее: идём с конца, ранние перезаписывают.
    # Поля по BORDER кадров с каждой стороны отбрасываем.
    for start in reversed(starts):
        lo, hi = max(start, 0), min(start + CHUNK, total)
        chunk = np.pad(spect[lo:hi], ((max(0, -start), max(0, min(BORDER, start + CHUNK - total))), (0, 0)))
        b, d = session.run(None, {"spect": chunk[None]})
        at = start + BORDER
        n = min(len(chunk) - 2 * BORDER, total - at)
        beat[at:at + n] = b[0][BORDER:BORDER + n]
        down[at:at + n] = d[0][BORDER:BORDER + n]
    return beat, down


def _peaks(values):
    """Максимумы в окне ±70 мс с вероятностью выше половины; соседние
    кадры -- одна доля (как postp_minimal у Beat This)."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d

    frames = np.flatnonzero((values == maximum_filter1d(values, 7, mode="constant", cval=-1000.0))
                            & (values > 0))
    merged: list[float] = []
    count = 0
    for frame in frames:
        if merged and frame - merged[-1] <= 1:
            count += 1
            merged[-1] += (frame - merged[-1]) / count
        else:
            merged.append(float(frame))
            count = 1
    return np.array(merged) / FPS


def track(y, sr: int = SR):
    """(темп, доли, сильные доли) в секундах или None, если модели нет или
    ритм не слышен (меньше четырёх долей)."""
    import numpy as np

    if _session() is None:
        return None
    beat, down = logits(spectrogram(y, sr))
    beats = _peaks(beat)
    if len(beats) < 4:
        return None
    bpm = 60.0 / float(np.median(np.diff(beats)))
    return bpm, [float(b) for b in beats], [float(d) for d in bars(beats, _peaks(down))]


def bars(beats, downbeats):
    """Ровные такты из «раз», которые отметила сеть. Сама по себе она на
    плотном шаффле ставит «раз» то тут, то там -- метроному и линейке
    нужен порядок: такт из 4 долей (3 -- только если сеть явно слышит
    вальс), начало -- там, где за него больше всего голосов."""
    import numpy as np

    if not len(downbeats):
        return beats[::4]
    votes = np.unique([int(np.argmin(np.abs(beats - d))) for d in downbeats])

    def best(meter):
        counts = np.bincount(votes % meter, minlength=meter)
        return counts.max() / len(votes), int(counts.argmax())

    (share4, phase4), (share3, phase3) = best(4), best(3)
    meter, phase = (3, phase3) if share3 > share4 + 0.15 else (4, phase4)
    return beats[phase::meter]
