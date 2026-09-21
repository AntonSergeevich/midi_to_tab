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
# которых в песне не было, исчезли совсем. Значение подобрано по песне,
# круг которой назвал человек: 0.12 дало 98.7% против 89.9% у 0.09 --
# при 0.09 вместо Gm стабильно выигрывал Gm7.
SIZE_PENALTY = 0.12
# Отдельно придерживаем квинт-аккорд: из двух нот он подходит почти
# всюду, и без этого весь разбор превращается в частокол из D5, C5, G5.
# Оставшиеся квинт-аккорды честны -- там, где в миксе правда нет терции.
# Штраф считается пропорционально числу нот в шаблоне, и из-за этого
# двухнотный квинт-аккорд выходил ДЕШЕВЛЕ трезвучия (0.18 против 0.27) --
# при том, что его две ноты входят в трезвучие целиком и подходят везде,
# где подходит оно. Отсюда и брался частокол из D5, C5, G5 вместо Dm, C,
# Gm. Штраф должен перекрывать эту фору: 0.14 сверх 0.18 даёт 0.32 --
# дороже трезвучия, и квинт-аккорд выигрывает только там, где терции в
# звуке действительно нет.
FIFTH_PENALTY = 0.14
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

# Сколько разных аккордов оставлять на песню. В песне их обычно четыре-шесть:
# куплет и припев ходят по одному кругу. Всё, что сверх этого, -- почти всегда
# не гармония, а мусор от распознавания: случайный maj7 там, где в мелодии
# задержалась одна нота. Второй проход отбирает самые "весомые" аккорды и
# пересобирает разбор только из них.
DEFAULT_VOCABULARY = 6

# Профили Крумхансл: насколько каждая ступень характерна для тональности.
# Получены в слуховых опытах -- люди оценивали, насколько нота "подходит"
# прозвучавшему ладу. Сравнение хромаграммы со всеми 24 поворотами и даёт
# тональность песни.
KRUMHANSL_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KRUMHANSL_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# Штраф за каждую ноту аккорда вне тональности. Песня почти целиком
# состоит из своих семи ступеней, и чужая нота -- сильный довод против.
# Но не запрет: отклонения в музыке бывают, и на них штраф тратится
# честно -- аккорд с чужой нотой должен подойти заметно лучше своего.
KEY_PENALTY = 0.10
# Смена аккорда на втором проходе штрафуется сильнее: здесь шаг -- целый
# такт, а гармония редко меняется каждый такт подряд.
BAR_CHANGE_COST = 0.08


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
    key: str = ""                # тональность, например "Dm"

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.chords]


def as_float(value, default: float = 0.0) -> float:
    """
    Число из того, что вернула librosa.

    Темп приходит то числом, то массивом из одного элемента -- зависит от
    версии librosa. В NumPy 1.x float() от такого массива молча работал, в
    NumPy 2.x он падает: "only 0-dimensional arrays can be converted to
    Python scalars". На сервере стоит NumPy 2, поэтому разбор любого трека
    обрывался на самом входе. Разворачиваем вручную и не полагаемся на то,
    что именно вернёт библиотека.
    """
    import numpy as np

    array = np.asarray(value, dtype=float).ravel()
    if array.size == 0:
        return default
    number = float(array[0])
    if number != number:  # NaN
        return default
    return number


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


def guess_key(chroma) -> tuple[int, bool]:
    """
    Тональность песни: (основной тон, мажор ли).

    Сравниваем усреднённую хромаграмму со всеми 24 профилями и берём
    самый похожий. Точность здесь не критична: тональность нужна лишь
    как довод при выборе аккорда, а не как окончательный приговор.
    """
    import numpy as np

    profile = np.asarray(chroma).mean(axis=1)
    norm = np.linalg.norm(profile)
    if not norm:
        return 0, True
    profile = profile / norm

    best, best_score = (0, True), -2.0
    for tonic in range(12):
        for is_major, weights in ((True, KRUMHANSL_MAJOR), (False, KRUMHANSL_MINOR)):
            reference = np.roll(np.asarray(weights), tonic)
            reference = reference / np.linalg.norm(reference)
            score = float(profile @ reference)
            if score > best_score:
                best, best_score = (tonic, is_major), score
    return best


def key_penalties(names, key: tuple[int, bool], chroma=None,
                  strength: float = KEY_PENALTY):
    """Штраф каждому шаблону за ноты, которых в тональности нет."""
    import numpy as np

    tonic, is_major = key
    scale = {(tonic + step) % 12 for step in (MAJOR_SCALE if is_major else MINOR_SCALE)}
    if not is_major and chroma is not None:
        # Минор бывает натуральный, а бывает гармонический -- с поднятой
        # седьмой ступенью и мажорной доминантой. Решать это за песню
        # нельзя: у одной доминанта мажорная, у другой минорная, и
        # ошибка стоит целого аккорда в круге. Смотрим, что в записи
        # громче: поднятая седьмая или натуральная.
        energy = np.asarray(chroma).mean(axis=1)
        raised, natural = energy[(tonic + 11) % 12], energy[(tonic + 10) % 12]
        if raised > natural * 0.8:
            scale.add((tonic + 11) % 12)

    penalties = []
    for name in names:
        root, quality = _split(name)
        intervals = dict(TEMPLATES).get(quality, (0, 4, 7))
        outside = sum(1 for i in intervals if (root + i) % 12 not in scale)
        penalties.append(strength * outside)
    return np.array(penalties)


def _split(name: str) -> tuple[int, str]:
    """Разобрать подпись на основной тон и качество."""
    head = name[:2] if len(name) > 1 and name[1] == "#" else name[:1]
    return PITCH_CLASSES.index(head), name[len(head):]


def key_name(key: tuple[int, bool]) -> str:
    tonic, is_major = key
    return f"{PITCH_CLASSES[tonic]}{'' if is_major else 'm'}"


def _templates():
    """
    Шаблоны аккордов и штрафы к ним.

    Возвращает (названия, нормированные векторы, штрафы, основные тоны,
    качества). Штраф вычитается из схожести, поэтому сложный аккорд должен
    подойти заметно лучше простого, чтобы его выбрали.
    """
    import numpy as np

    names, vectors, penalties, roots, qualities = [], [], [], [], []
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
            roots.append(root)
            qualities.append(quality)
    return (names, np.array(vectors), np.array(penalties),
            np.array(roots), qualities)


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
    vocabulary: int = DEFAULT_VOCABULARY,
    allowed: list[str] | str | None = None,
    progress=None,
) -> ChordAnalysis:
    """
    Разметить запись аккордами.

    Разбор идёт в два прохода, и это главное, что отличает результат от
    свалки из двух десятков подписей.

    Первый проход слушает каждую долю всеми шаблонами сразу. Он полезен не
    сам по себе, а тем, что показывает, ЧЕМ песня вообще играется: какие
    аккорды набрали больше всего звучащего времени. Их и оставляем --
    столько, сколько задано vocabulary. В песне круг обычно из четырёх-шести
    аккордов, а всё остальное, что выдаёт первый проход, -- это не гармония,
    а задержавшаяся в мелодии нота, которую шаблон посложнее объяснил лучше
    трезвучия.

    Второй проход пересобирает разбор только из отобранных аккордов и уже
    не по долям, а по тактам: гармония держится такт, а не четверть. Сетка
    тактов берётся от того же места, где первый проход увидел смены.

    Если аккорды известны заранее, их можно передать в allowed -- тогда
    отбор не нужен и разбор идёт сразу по ним.

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
    bpm = as_float(tempo)
    beats = np.asarray(beats).ravel()
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, bins_per_octave=36)

    if len(beats) < 2:
        # Ритм не нашёлся -- режем на равные отрезки по полсекунды
        step = max(1, int(0.5 * sr / 512))
        beats = np.arange(0, chroma.shape[1], step)

    times = _edges(librosa.frames_to_time(beats, sr=sr), librosa.get_duration(y=y, sr=sr))
    names, vectors, penalties, roots, qualities = _templates()

    if progress:
        progress("Выбираю последовательность аккордов...")
    beat_chroma = _sync(librosa, chroma, beats, np)
    first = _decide(np, vectors, penalties, beat_chroma, CHANGE_COST)
    rough = _segments(names, first, times, min_duration)

    beat_times = [float(t) for t in librosa.frames_to_time(beats, sr=sr)]
    duration = librosa.get_duration(y=y, sr=sr)

    # Сетка тактов: первый проход показал, где меняется гармония, а смена
    # почти всегда попадает на начало такта -- по этому и выбирается сдвиг.
    offset = _bar_offset(beat_times, rough, beats_per_bar)
    groups = _bar_groups(beats, offset, beats_per_bar)
    bar_chroma = _sync(librosa, chroma, groups, np)
    bar_times = _edges(librosa.frames_to_time(groups, sr=sr), duration)

    # Тональность -- сильный довод при выборе аккорда. Песня почти целиком
    # состоит из своих семи ступеней, и чужая нота в аккорде означает либо
    # отклонение (редко), либо ошибку разбора (обычно).
    key = guess_key(bar_chroma)
    penalties = penalties + key_penalties(names, key, bar_chroma)
    if progress:
        progress(f"Тональность: {key_name(key)}")

    picked = _pick_names(names, allowed)
    if picked is None:
        picked = _vocabulary(
            np, names, vectors, penalties, roots, qualities, bar_chroma, vocabulary
        )
    if not picked:
        chords = rough
    else:
        if progress:
            progress("Круг аккордов песни: " + ", ".join(names[i] for i in picked))
        keep = np.array(picked)
        path, sure = _decide(
            np, vectors[keep], penalties[keep], bar_chroma, BAR_CHANGE_COST
        )
        chords = _segments(names, (keep[path], sure), bar_times, min_duration)

    downbeats = _guess_downbeats(beat_times, chords, beats_per_bar)

    if progress:
        progress(
            f"Аккордов найдено: {len(chords)}, разных "
            f"{len({c.name for c in chords})}, темп {bpm:.0f}"
        )
    return ChordAnalysis(
        chords=chords,
        tempo=bpm,
        beats=beat_times,
        downbeats=downbeats,
        key=key_name(key),
    )


def _sync(librosa, chroma, boundaries, np):
    """Усреднить хромаграмму по отрезкам и нормировать каждый столбец."""
    synced = librosa.util.sync(chroma, boundaries, aggregate=np.median)
    norms = np.linalg.norm(synced, axis=0, keepdims=True)
    norms[norms == 0] = 1.0
    return synced / norms


def _edges(times, duration: float):
    """Границы отрезков: к началам добавляется конец последнего."""
    import numpy as np

    values = [float(t) for t in np.asarray(times).ravel()]
    if not values:
        return [0.0, duration]
    if values[-1] < duration:
        values.append(float(duration))
    return values


def _decide(np, vectors, penalties, synced, change_cost: float):
    """
    Разобрать последовательность и оценить уверенность.

    Уверенность = насколько выбранный аккорд оторвался от ближайшего
    соперника. Абсолютная схожесть тут обманывает: на плотном миксе она
    низкая у всех подряд, но если один вариант ушёл далеко вперёд --
    сомневаться не в чем. И наоборот: два почти равных варианта это
    настоящая неоднозначность, даже когда оба похожи.

    Штраф влияет на то, КАКОЙ аккорд выбрать, но не на то, насколько мы в
    нём уверены: иначе все подписи выглядели бы сомнительными просто
    из-за способа отбора.
    """
    scores = (vectors @ synced) - penalties[:, None]
    if scores.shape[0] < 2:
        path = np.zeros(scores.shape[1], dtype=int)
        return path, np.ones(scores.shape[1])

    ordered = np.sort(scores, axis=0)
    margin = ordered[-1] - ordered[-2]
    spread = as_float(np.percentile(margin, 90)) or 1.0
    confidence = np.minimum(1.0, margin / spread)
    return _viterbi(scores, change_cost), confidence


def _segments(names, decision, times, min_duration: float) -> list[AudioChord]:
    """Собрать отрезки из выбранной цепочки, слив слишком короткие."""
    path, confidence = decision
    chords: list[AudioChord] = []
    for index, chord_index in enumerate(path):
        if index + 1 >= len(times):
            break
        start, end = float(times[index]), float(times[index + 1])
        if end <= start:
            continue
        sure = float(confidence[index])
        if sure < MIN_CONFIDENCE:
            continue
        name = names[chord_index]
        if chords and chords[-1].name == name and abs(chords[-1].end - start) < 0.05:
            chords[-1].end = end
            chords[-1].confidence = max(chords[-1].confidence, sure)
        else:
            chords.append(AudioChord(name, start, end, sure))
    return _merge_short(chords, min_duration)


def _vocabulary(np, names, vectors, penalties, roots, qualities, bars, limit):
    """
    Собрать круг аккордов песни: сначала основные тоны, потом качества.

    Порядок именно такой, и это главное. Пробовать все шаблоны сразу
    бесполезно: четырёхзвучие всегда "объясняет" больше энергии, чем
    трезвучие, потому что содержит его целиком. В ре миноре ля-бемоль-мажор
    с большой септимой (A# D F A) накрывает собой и Dm, и B-бемоль, и
    побеждает оба -- хотя в песне его нет.

    А вот ОСНОВНОЙ ТОН такой подмены не боится: квинта от корня однозначна.
    Поэтому сначала двенадцатью квинт-аккордами выясняется, вокруг каких
    нот ходит песня, и берутся самые весомые. И только потом для каждой
    выбирается качество -- по усреднённому звучанию тех тактов, где этот
    тон и звучал. Мажор или минор решается там, где мешать уже некому.
    """
    if limit <= 0 or bars.shape[1] == 0:
        return []

    # 1. Основные тоны. Двенадцать состояний -- по одному на ноту.
    fifths = [i for i, q in enumerate(qualities) if q == "5"]
    if not fifths:
        return []
    fifth_idx = np.array(fifths)
    root_path = _viterbi(vectors[fifth_idx] @ bars, BAR_CHANGE_COST)

    weight = np.bincount(root_path, minlength=len(fifths)).astype(float)
    order = np.argsort(-weight)
    chosen = [fifths[i] for i in order[:limit] if weight[i] > 0]
    if not chosen:
        return []

    # 2. Качество для каждого тона -- по тактам, где он и звучал.
    picked: list[int] = []
    for state, template in zip(order[: len(chosen)], chosen):
        mask = root_path == state
        if not mask.any():
            continue
        profile = bars[:, mask].mean(axis=1)
        norm = np.linalg.norm(profile) or 1.0
        profile = profile / norm
        family = np.where(roots == roots[template])[0]
        scores = (vectors[family] @ profile) - penalties[family]
        picked.append(int(family[int(np.argmax(scores))]))
    return sorted(set(picked))


def _pick_names(names, allowed) -> list[int] | None:
    """
    Разобрать список аккордов, заданный человеком.

    Принимается "Dm, Bb, F, C" или "Dm Bb F C". Бемоли переводятся в
    диезы, потому что шаблоны названы диезами; регистр качества сохраняем
    (m -- минор, M -- нота), поэтому сравнение не по lower().
    """
    if not allowed:
        return None
    if isinstance(allowed, str):
        parts = [p for p in allowed.replace(",", " ").split() if p]
    else:
        parts = [str(p).strip() for p in allowed if str(p).strip()]

    flats = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B",
             "Fb": "E", "E#": "F", "B#": "C"}
    table = {name.lower(): i for i, name in enumerate(names)}
    picked: list[int] = []
    for part in parts:
        text = part[0].upper() + part[1:]
        for flat, sharp in flats.items():
            if text.startswith(flat):
                text = sharp + text[len(flat):]
                break
        index = table.get(text.lower())
        if index is not None and index not in picked:
            picked.append(index)
    return sorted(picked) or None


def _bar_offset(beats: list[float], chords: list[AudioChord], beats_per_bar: int) -> int:
    """
    С какой доли начинается такт.

    Зацепка та же, что и у сильных долей: смена гармонии почти всегда
    попадает на начало такта.
    """
    if not beats or not chords:
        return 0
    starts = [c.start for c in chords]
    best_offset, best_hits = 0, -1
    for offset in range(beats_per_bar):
        candidates = beats[offset::beats_per_bar]
        hits = sum(1 for s in starts if any(abs(s - b) < 0.12 for b in candidates))
        if hits > best_hits:
            best_offset, best_hits = offset, hits
    return best_offset


def _bar_groups(beats, offset: int, beats_per_bar: int):
    """Границы тактов в кадрах: каждая beats_per_bar-я доля, начиная со сдвига."""
    import numpy as np

    grouped = np.asarray(beats).ravel()[offset::beats_per_bar]
    if len(grouped) < 2:
        return np.asarray(beats).ravel()
    return grouped


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
    return beats[_bar_offset(beats, chords, beats_per_bar)::beats_per_bar]


def _viterbi(scores, change_cost: float = CHANGE_COST):
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
        switch = best.max() - change_cost
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
