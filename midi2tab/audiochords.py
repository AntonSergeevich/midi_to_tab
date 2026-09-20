"""
Аккорды прямо из звука, без нейросети.

Путь "аудио -> ноты -> аккорды" плох тем, что ошибки распознавания нот
складываются: потеряли терцию -- и мажор стал квинт-аккордом. Аккорд же
слышен в спектре напрямую, и брать его оттуда и точнее, и несравнимо
дешевле: здесь нет ни одной нейросети, только обработка сигнала.

Три шага:
  1. Гармоническая составляющая отделяется от ударной (HPSS) -- барабаны
     размазывают спектр и мешают больше всего.
  2. Считается хромаграмма CQT: сколько энергии приходится на каждую из
     двенадцати ступеней. Усреднение по долям, а не по кадрам: гармония
     меняется на долях, и это заодно убирает дребезг.
  3. Последовательность аккордов выбирается Витерби с дороговизной смены:
     без неё аккорд скачет туда-сюда на каждой доле.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chords import PITCH_CLASSES, TEMPLATES

# Сменить аккорд дороже, чем удержать: гармония меняется реже, чем
# мелькают отдельные ноты. Значение подобрано так, чтобы настоящие смены
# проходили, а дребезг на проходящих тонах гасился.
CHANGE_COST = 0.15
MIN_CONFIDENCE = 0.12

# Штраф за сложность шаблона. Без него разбор плотного микса съезжает в
# нонаккорды: пятизвучие "объясняет" больше энергии, чем трезвучие, и
# выигрывает всегда. На реальном треке в ре миноре штраф поднял долю
# аккордов, принадлежащих тональности, с 71% до 84%, а нонаккорды,
# которых в песне не было, исчезли совсем.
SIZE_PENALTY = 0.09
# Отдельно придерживаем квинт-аккорд: из двух нот он подходит почти
# всюду, и без этого весь разбор превращается в частокол из D5, C5, G5.
# Оставшиеся квинт-аккорды честны -- там, где в миксе правда нет терции.
FIFTH_PENALTY = 0.06
# Sus-аккорды складываются из тех же трёх нот, что и трезвучия, поэтому
# размер их не придерживает. А возникают они чаще всего не потому, что
# их сыграли, а потому что голос задержался на секунде поверх выдержанной
# гармонии. Небольшой штраф оставляет их там, где они настоящие.
SUS_PENALTY = 0.04
# Уменьшённые и увеличенные в песнях редки, а в мутной хромаграмме
# возникают легко: их ступени равномерно раскиданы по октаве и ложатся
# почти на любой шум. Придерживаем, чтобы не выдавать артефакт за гармонию.
ODD_PENALTY = 0.07
# Гармония в песне держится тактами, а не долями. Без нижней границы
# длительности список превращается в частокол из десятков подписей, в
# котором ничего не разобрать на ходу.
MIN_DURATION = 0.9


@dataclass
class AudioChord:
    name: str
    start: float          # секунды
    end: float
    confidence: float


@dataclass
class ChordAnalysis:
    """Что удалось услышать в записи."""

    chords: list[AudioChord]
    tempo: float                 # ударов в минуту
    beats: list[float]           # моменты долей в секундах
    downbeats: list[float]       # предполагаемые сильные доли

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.chords]


def available() -> tuple[bool, str]:
    try:
        import librosa  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False, (
            "Для разбора аккордов из аудио нужен librosa:\n"
            "    pip install -r requirements-audio.txt"
        )
    return True, ""


def _templates():
    """
    Шаблоны аккордов и штрафы к ним.

    Возвращает (названия, нормированные векторы, штрафы). Штраф вычитается
    из схожести, поэтому сложный аккорд должен подойти заметно лучше
    простого, чтобы его выбрали.
    """
    import numpy as np

    names, vectors, penalties = [], [], []
    for root in range(12):
        for quality, intervals in TEMPLATES:
            vector = np.zeros(12)
            for interval in intervals:
                vector[(root + interval) % 12] = 1.0
            vectors.append(vector / np.linalg.norm(vector))
            penalty = SIZE_PENALTY * len(intervals)
            if quality == "5":
                penalty += FIFTH_PENALTY
            elif quality.startswith("sus"):
                penalty += SUS_PENALTY
            elif quality in ("dim", "aug", "dim7", "m7b5"):
                penalty += ODD_PENALTY
            penalties.append(penalty)
            names.append(f"{PITCH_CLASSES[root]}{quality}")
    return names, np.array(vectors), np.array(penalties)


def prewarm() -> None:
    """
    Прогреть numba внутри librosa.

    Первый разбор иначе занимает в десятки раз дольше остальных: librosa
    компилирует numba-функции на лету. На сервере это лучше сделать при
    запуске, а не на первом пользователе.
    """
    try:
        import librosa
        import numpy as np

        y = np.zeros(22050, dtype=np.float32)
        librosa.feature.chroma_cqt(y=y, sr=22050, bins_per_octave=36)
        librosa.beat.beat_track(y=y, sr=22050, units="frames")
    except Exception:
        pass


def _merge_short(chords: list[AudioChord], min_duration: float) -> list[AudioChord]:
    """
    Слить слишком короткие аккорды с соседями.

    Короткий отрезок почти всегда не смена гармонии, а проходящий бас или
    мелодическая фигура. Присоединяем его к соседу, в котором больше
    уверенности.
    """
    if not chords:
        return []
    result = [chords[0]]
    for chord in chords[1:]:
        previous = result[-1]
        if chord.end - chord.start < min_duration:
            previous.end = chord.end
            if chord.confidence > previous.confidence:
                previous.name = chord.name
                previous.confidence = chord.confidence
        elif previous.end - previous.start < min_duration and previous.confidence < chord.confidence:
            previous.name = chord.name
            previous.confidence = chord.confidence
            previous.end = chord.end
        elif chord.name == previous.name:
            previous.end = chord.end
        else:
            result.append(chord)
    return result


def detect_from_audio(
    audio_path: str,
    *,
    sample_rate: int = 22050,
    min_duration: float = MIN_DURATION,
    beats_per_bar: int = 4,
    progress=None,
) -> ChordAnalysis:
    """
    Разметить запись аккордами.

    Возвращает (аккорды, определённый темп в ударах в минуту).
    """
    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    import librosa
    import numpy as np

    if progress:
        progress("Читаю запись...")
    y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
    if y.size == 0:
        return ChordAnalysis([], 0.0, [], [])

    if progress:
        progress("Отделяю гармонию от ударных...")
    harmonic = librosa.effects.harmonic(y, margin=3.0)

    if progress:
        progress("Ищу доли и строю хромаграмму...")
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, bins_per_octave=36)

    if len(beats) < 2:
        # Ритм не нашёлся -- режем на равные отрезки по полсекунды
        step = max(1, int(0.5 * sr / 512))
        beats = np.arange(0, chroma.shape[1], step)

    # Усреднение по долям: гармония держится долю, а не отдельный кадр
    synced = librosa.util.sync(chroma, beats, aggregate=np.median)
    times = librosa.frames_to_time(beats, sr=sr)
    if len(times) < synced.shape[1] + 1:
        duration = librosa.get_duration(y=y, sr=sr)
        times = np.append(times, duration)

    # Нормируем каждый столбец: важны пропорции ступеней, не громкость
    norms = np.linalg.norm(synced, axis=0, keepdims=True)
    norms[norms == 0] = 1.0
    synced = synced / norms

    names, vectors, penalties = _templates()
    # Схожесть и отдельно -- оценка для выбора. Штраф влияет на то, какой
    # аккорд выбрать, но не на то, насколько мы в нём уверены: иначе все
    # подписи выглядели бы сомнительными просто из-за способа отбора.
    similarity = vectors @ synced
    scores = similarity - penalties[:, None]

    # Уверенность = насколько выбранный аккорд оторвался от ближайшего
    # соперника. Абсолютная схожесть тут обманывает: на плотном миксе она
    # низкая у всех подряд, но если один вариант ушёл далеко вперёд --
    # сомневаться не в чем. И наоборот: два почти равных варианта это
    # настоящая неоднозначность, даже когда оба похожи.
    ordered = np.sort(scores, axis=0)
    margin = ordered[-1] - ordered[-2]
    spread = float(np.percentile(margin, 90)) or 1.0

    if progress:
        progress("Выбираю последовательность аккордов...")
    path = _viterbi(scores)

    chords: list[AudioChord] = []
    for index, chord_index in enumerate(path):
        start = float(times[index])
        end = float(times[min(index + 1, len(times) - 1)])
        if end <= start:
            continue
        confidence = float(min(1.0, margin[index] / spread))
        if confidence < MIN_CONFIDENCE:
            continue
        name = names[chord_index]
        if chords and chords[-1].name == name and abs(chords[-1].end - start) < 0.05:
            chords[-1].end = end
            chords[-1].confidence = max(chords[-1].confidence, confidence)
        else:
            chords.append(AudioChord(name, start, end, confidence))

    chords = _merge_short(chords, min_duration)

    # Доли нужны метроному: щёлкать по найденным долям точнее, чем
    # отсчитывать от среднего темпа -- живая игра всегда чуть плывёт.
    beat_times = [float(t) for t in librosa.frames_to_time(beats, sr=sr)]
    downbeats = _guess_downbeats(beat_times, chords, beats_per_bar)

    if progress:
        progress(f"Аккордов найдено: {len(chords)}, темп {float(tempo):.0f}")
    return ChordAnalysis(
        chords=chords,
        tempo=float(tempo),
        beats=beat_times,
        downbeats=downbeats,
    )


def _guess_downbeats(
    beats: list[float], chords: list[AudioChord], beats_per_bar: int
) -> list[float]:
    """
    Определить сильные доли.

    Точное определение размера -- отдельная большая задача, поэтому здесь
    используется надёжная зацепка: смена гармонии почти всегда попадает на
    сильную долю. По ней и выбирается сдвиг внутри такта.
    """
    if not beats:
        return []
    if not chords:
        return beats[::beats_per_bar]

    starts = [c.start for c in chords]
    best_offset, best_hits = 0, -1
    for offset in range(beats_per_bar):
        candidates = beats[offset::beats_per_bar]
        hits = sum(
            1 for s in starts if any(abs(s - b) < 0.12 for b in candidates)
        )
        if hits > best_hits:
            best_offset, best_hits = offset, hits
    return beats[best_offset::beats_per_bar]


def _viterbi(scores):
    """
    Выбрать цепочку аккордов, а не по отдельности самый похожий на каждой доле.

    Ровно та же идея, что и в раскладке по грифу: решение принимается
    для всей последовательности, а смена аккорда штрафуется.
    """
    import numpy as np

    n_states, n_steps = scores.shape
    best = scores[:, 0].copy()
    back = np.zeros((n_states, n_steps), dtype=int)

    for step in range(1, n_steps):
        stay = best
        switch = best.max() - CHANGE_COST
        source = best.argmax()
        improved = stay < switch
        candidate = np.where(improved, switch, stay)
        back[:, step] = np.where(improved, source, np.arange(n_states))
        best = candidate + scores[:, step]

    path = np.zeros(n_steps, dtype=int)
    path[-1] = int(best.argmax())
    for step in range(n_steps - 1, 0, -1):
        path[step - 1] = back[path[step], step]
    return path
