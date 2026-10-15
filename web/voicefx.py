"""
Нейросеть и «звукач» для записанного голоса: шумодав и подтяжка нот.

Шумодав -- DeepFilterNet 3 (MIT/Apache-2.0): нейросеть, обученная отделять
голос от шума комнаты, вентилятора, улицы. Работает отдельной программой
deep-filter (Rust, одна статическая сборка, модель внутри) на процессоре
сервера, ставится deploy/update.sh. Нет программы -- спектральный шумодав
ffmpeg (afftdn): слабее, но лучше, чем ничего.

Подтяжка нот (автотюн) -- своя, без новых зависимостей:
  1. высота голоса по кадрам 10 мс -- pYIN (librosa), с признаком «поётся/нет»;
  2. каждой спетой ноте -- ближайшая нота тональности песни (тональность
     сервис уже знает из разбора), неизвестна -- ближайший полутон;
  3. «Мягко»: подтягивается центр ноты, вибрато и подъезды остаются живыми,
     нота доезжает за ~80 мс. «Сильно»: каждая точка ровно в ноту и почти
     мгновенно -- эффект «автотюна» из поп-музыки;
  4. сдвиг высоты -- TD-PSOLA: голос режется на периоды основного тона и
     складывается обратно чаще или реже. Тембр (форманты) не меняется,
     голос не становится «бурундучьим».
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

DEEP_FILTER = os.environ.get("MIDI2TAB_DEEPFILTER", "/opt/nasluh/bin/deep-filter")
NOTES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
         "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "H": 11}
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)


def deep_filter_path() -> str:
    if os.path.isfile(DEEP_FILTER) and os.access(DEEP_FILTER, os.X_OK):
        return DEEP_FILTER
    return shutil.which("deep-filter") or ""


def _ffmpeg(*args: str, timeout: int = 600) -> None:
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(done.stderr.decode("utf-8", "replace")[-300:] or "ffmpeg")


def _read(path: str, rate: int) -> np.ndarray:
    """Моно float32 с нужной частотой -- через ffmpeg (любой формат)."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
                           "-f", "f32le", "-"], capture_output=True, timeout=600)
    if done.returncode:
        raise RuntimeError("не получилось прочитать запись")
    return np.frombuffer(done.stdout, dtype=np.float32).copy()


def _write_mp3(y: np.ndarray, rate: int, path: str) -> None:
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as raw:
        raw.write(np.clip(y, -1, 1).astype(np.float32).tobytes())
    try:
        _ffmpeg("-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", raw.name,
                "-ac", "2", "-ar", "44100", "-b:a", "192k", path)
    finally:
        os.remove(raw.name)


# ------------------------------------------------------------------ шумодав

def denoise(src: str, dst: str, limit_db: float = 30) -> str:
    """Убрать шум. limit_db -- насколько глубоко давить шум (30 дБ: шум
    уходит, а дыхание и хвосты нот остаются естественными). Возвращает,
    чем чистили: 'deepfilternet' или 'ffmpeg'."""
    binary = deep_filter_path()
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "voice.wav")
        if binary:
            _ffmpeg("-i", src, "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", wav)
            out = os.path.join(tmp, "out")
            done = subprocess.run([binary, "-D", "-a", str(limit_db), "-o", out, wav],
                                  capture_output=True, timeout=1800)
            cleaned = os.path.join(out, "voice.wav")
            if done.returncode == 0 and os.path.isfile(cleaned):
                _ffmpeg("-i", cleaned, "-ac", "2", "-ar", "44100", "-b:a", "192k", dst)
                return "deepfilternet"
        _ffmpeg("-i", src, "-vn", "-af", "highpass=f=70,afftdn=nr=14:nf=-42:tn=1", "-ac", "2", "-ar", "44100",
                "-b:a", "192k", dst)
        return "ffmpeg"


# ------------------------------------------------------------- подтяжка нот

def scale_of(key: str | None) -> tuple[int, ...] | None:
    """Ноты тональности (классы высоты 0..11): 'Am' -> ля минор."""
    m = re.match(r"^\s*([A-H][#b]?)\s*(m|min|minor|moll)?(?![a-z])", key or "")
    if not m or m.group(1) not in NOTES:
        return None
    tonic = NOTES[m.group(1)]
    steps = MINOR if m.group(2) else MAJOR
    return tuple(sorted((tonic + s) % 12 for s in steps))


def _snap(midi: np.ndarray, scale: tuple[int, ...] | None) -> np.ndarray:
    """Ближайшая нота тональности (или полутон)."""
    if not scale:
        return np.round(midi)
    cands = np.array([o * 12 + pc for o in range(0, 11) for pc in scale], dtype=float)
    idx = np.abs(midi[:, None] - cands[None, :]).argmin(axis=1)
    return cands[idx]


def _smooth(x: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 1:
        return x
    k = np.hanning(frames + 2)[1:-1]
    k /= k.sum()
    pad = frames // 2
    return np.convolve(np.pad(x, pad, mode="edge"), k, mode="same")[pad:pad + len(x)]


def correction(f0: np.ndarray, voiced: np.ndarray, key: str | None, strength: str, hop_s: float) -> np.ndarray:
    """Сдвиг каждого кадра в полутонах (0 там, где не поётся)."""
    n = len(f0)
    shift = np.zeros(n)
    ok = voiced & np.isfinite(f0) & (f0 > 0)
    if not ok.any():
        return shift
    midi = np.full(n, np.nan)
    midi[ok] = 69 + 12 * np.log2(f0[ok] / 440.0)
    scale = scale_of(key)
    hard = strength == "hard"
    # спетые куски по отдельности: сглаживание не перетекает через паузы
    edges = np.flatnonzero(np.diff(np.r_[0, ok.astype(int), 0]))
    for a, b in zip(edges[::2], edges[1::2]):
        seg = midi[a:b]
        if hard:
            shift[a:b] = _snap(seg, scale) - seg
            continue
        # мягко: центр ноты (сглажено ~150 мс) -- в ноту, вибрато вокруг него остаётся
        center = _smooth(seg, max(1, int(0.15 / hop_s)))
        want = _snap(center, scale) - center
        shift[a:b] = 0.85 * want
    # скорость подтяжки: «сильно» -- почти сразу, «мягко» -- за ~80 мс
    shift = _smooth(shift, max(1, int((0.012 if hard else 0.08) / hop_s)))
    shift[~ok] = 0
    return np.clip(shift, -3, 3)


def psola(y: np.ndarray, sr: int, f0: np.ndarray, voiced: np.ndarray, shift: np.ndarray, hop: int) -> np.ndarray:
    """TD-PSOLA: высота меняется на shift полутонов по кадрам, тембр -- нет."""
    n = len(y)
    frames = len(f0)
    t_frame = lambda i: min(frames - 1, max(0, int(i / hop)))
    # метки анализа: через период тона там, где поётся, через 5 мс -- где нет;
    # метка уточняется к пику сигнала рядом -- куски складываются синфазно
    marks = []
    i = 0
    while i < n:
        k = t_frame(i)
        if voiced[k] and np.isfinite(f0[k]) and f0[k] > 0:
            period = int(sr / f0[k])
            lo, hi = max(0, i - period // 4), min(n, i + period // 4 + 1)
            if marks and hi > lo:
                i = lo + int(np.argmax(y[lo:hi]))
                if i <= marks[-1]:
                    i = marks[-1] + max(1, period // 2)
            marks.append(i)
            i += max(16, period)
        else:
            marks.append(i)
            i += int(sr * 0.005)
    marks = np.array(marks)
    out = np.zeros(n + sr // 10)
    norm = np.zeros_like(out)
    t = float(marks[0]) if len(marks) else 0.0
    j = 0
    while t < n:
        while j + 1 < len(marks) and abs(marks[j + 1] - t) <= abs(marks[j] - t):
            j += 1
        a = int(marks[j])
        k = t_frame(a)
        sung = voiced[k] and np.isfinite(f0[k]) and f0[k] > 0
        exact = sr / f0[k] if sung else sr * 0.005          # период -- дробный: шаг меток точен до центов
        period = int(exact)
        ratio = 2 ** (shift[k] / 12) if sung else 1.0
        lo, hi = a - period, a + period
        grain = np.zeros(2 * period)
        s0, s1 = max(0, lo), min(n, hi)
        grain[s0 - lo:s1 - lo] = y[s0:s1]
        w = np.hanning(2 * period)
        at = int(round(t)) - period
        g0, g1 = max(0, at), min(len(out), at + 2 * period)
        if g1 > g0:
            out[g0:g1] += (grain * w)[g0 - at:g1 - at]
            norm[g0:g1] += w[g0 - at:g1 - at]
        t += exact / ratio
    norm[norm < 1e-3] = 1e-3
    return (out / norm)[:n].astype(np.float32)


def tune(src: str, dst: str, key: str | None, strength: str = "soft") -> dict:
    """Подтянуть ноты к тональности. Возвращает, на сколько в среднем
    поправили (центы) -- человеку видно, что сделано."""
    import librosa

    sr = 44100
    y = _read(src, sr)
    if not len(y):
        raise RuntimeError("пустая запись")
    det_sr = 16000
    hop_det = 160                                   # 10 мс
    yd = librosa.resample(y, orig_sr=sr, target_sr=det_sr)
    f0, voiced, _ = librosa.pyin(yd, fmin=65, fmax=1100, sr=det_sr, frame_length=1024, hop_length=hop_det)
    hop_s = hop_det / det_sr
    shift = correction(f0, voiced, key, strength, hop_s)
    hop = int(round(hop_s * sr))
    out = psola(y, sr, f0, voiced, shift, hop)
    # громкость как была
    peak_in, peak_out = float(np.abs(y).max() or 1), float(np.abs(out).max() or 1)
    out *= min(1.0, peak_in / peak_out) if peak_out > 1 else 1.0
    _write_mp3(out, sr, dst)
    sung = voiced & np.isfinite(f0)
    return {"cents": round(float(np.mean(np.abs(shift[sung])) * 100), 1) if sung.any() else 0.0,
            "scale": bool(scale_of(key))}
