"""
Прослушивание разложенной табулатуры.

Ноты отправляются на системный MIDI-синтезатор: в Windows это
"Microsoft GS Wavetable Synth", он есть всегда и ничего ставить не надо.
Инструмент выбирается стандартной командой смены программы, поэтому
одну и ту же партию можно послушать нейлоном, стилом, чистым электро
или перегрузом, не переделывая файл.

Если порт недоступен, остаётся запасной путь -- выгрузить .mid и
открыть его любым плеером.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .arrange import Placement
from .timing import TPQ
from .tuning import Fretboard

# Звуки General MIDI, осмысленные для гитариста (номер программы -> название)
INSTRUMENTS: dict[str, int] = {
    "Акустика нейлон": 24,
    "Акустика сталь": 25,
    "Джазовая электро": 26,
    "Чистая электро": 27,
    "Приглушённая электро": 28,
    "Овердрайв": 29,
    "Дисторшн": 30,
    "Флажолеты": 31,
    "Бас акустический": 32,
    "Бас пальцами": 33,
    "Бас медиатором": 34,
    "Банджо": 105,
    "Фортепиано": 0,
}
DEFAULT_INSTRUMENT = "Акустика сталь"


@dataclass
class NoteOnOff:
    """Событие для синтезатора: момент в секундах, нота, включение/выключение."""

    when: float
    pitch: int
    velocity: int
    on: bool


def _pygame_ready() -> bool:
    try:
        import pygame.midi  # noqa: F401
    except Exception:
        return False
    return True


def _rtmidi_ready() -> bool:
    try:
        import mido  # noqa: F401
        import rtmidi  # noqa: F401
    except Exception:
        return False
    return True


def backend() -> str:
    """Какой способ отправки нот доступен: pygame, rtmidi или никакой."""
    if _pygame_ready():
        return "pygame"
    if _rtmidi_ready():
        return "rtmidi"
    return ""


def available() -> tuple[bool, str]:
    if backend():
        return True, ""
    return False, (
        "Для прослушивания нужен MIDI-выход. Установите:\n"
        "    pip install pygame\n"
        "Под Python 3.13 это единственный вариант без компилятора: у\n"
        "python-rtmidi готовых сборок под 3.13 нет, только исходники."
    )


def output_ports() -> list[str]:
    """Доступные MIDI-выходы системы."""
    kind = backend()
    if kind == "pygame":
        try:
            import pygame.midi

            pygame.midi.init()
            names = []
            for i in range(pygame.midi.get_count()):
                info = pygame.midi.get_device_info(i)
                if info and info[3]:  # is_output
                    names.append(info[1].decode("utf-8", "replace"))
            pygame.midi.quit()
            return names
        except Exception:
            return []
    if kind == "rtmidi":
        try:
            import mido

            return list(mido.get_output_names())
        except Exception:
            return []
    return []


def build_events(
    placements: list[Placement], board: Fretboard, tempo: int
) -> list[NoteOnOff]:
    """Развернуть аппликатуры в поток событий с временем в секундах."""
    seconds_per_tick = 60.0 / max(1, tempo) / TPQ
    events: list[NoteOnOff] = []
    for placement in placements:
        chord = placement.chord
        start = chord.onset * seconds_per_tick
        for note, position in zip(chord.notes, placement.positions):
            if position is None:
                continue
            string_idx, fret = position
            pitch = board.open_pitch(string_idx) + fret
            end = (chord.onset + max(1, note.duration)) * seconds_per_tick
            velocity = max(1, min(127, note.velocity))
            events.append(NoteOnOff(start, pitch, velocity, True))
            events.append(NoteOnOff(end, pitch, 0, False))
    events.sort(key=lambda e: (e.when, e.on))
    return events


def write_midi(
    placements: list[Placement],
    board: Fretboard,
    output_path: str,
    tempo: int,
    instrument: str = DEFAULT_INSTRUMENT,
) -> str:
    """Выгрузить разложенную партию в .mid -- запасной путь для любого плеера."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    inst = pretty_midi.Instrument(program=INSTRUMENTS.get(instrument, 25))
    seconds_per_tick = 60.0 / max(1, tempo) / TPQ
    for placement in placements:
        chord = placement.chord
        for note, position in zip(chord.notes, placement.positions):
            if position is None:
                continue
            string_idx, fret = position
            inst.notes.append(
                pretty_midi.Note(
                    velocity=max(1, min(127, note.velocity)),
                    pitch=board.open_pitch(string_idx) + fret,
                    start=chord.onset * seconds_per_tick,
                    end=(chord.onset + max(1, note.duration)) * seconds_per_tick,
                )
            )
    pm.instruments.append(inst)
    pm.write(output_path)
    return output_path


class Player:
    """Проигрывание в отдельном потоке, с возможностью остановить."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._port = None
        self._backend = ""

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(
        self,
        placements: list[Placement],
        board: Fretboard,
        tempo: int,
        instrument: str = DEFAULT_INSTRUMENT,
        port_name: str | None = None,
        on_finish=None,
        on_error=None,
    ) -> None:
        ok, why = available()
        if not ok:
            raise RuntimeError(why)
        self.stop()
        events = build_events(placements, board, tempo)
        if not events:
            raise RuntimeError("Нечего играть: не осталось ни одной ноты.")
        program = INSTRUMENTS.get(instrument, 25)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(events, program, port_name, on_finish, on_error),
            daemon=True,
        )
        self._thread.start()

    def _run(self, events, program, port_name, on_finish, on_error) -> None:
        # Всё происходит в фоновом потоке: если просто дать исключению
        # всплыть, пользователь получит тишину без единого слова о причине.
        try:
            if backend() == "pygame":
                self._run_pygame(events, program)
            else:
                self._run_rtmidi(events, program, port_name)
        except Exception as exc:
            if on_error:
                on_error(str(exc))
        finally:
            self._silence()
            if on_finish:
                on_finish()

    def _schedule(self, events, send_on, send_off) -> None:
        """Общий разбор расписания: ждём до нужного момента и шлём событие."""
        started = time.perf_counter()
        for event in events:
            if self._stop.is_set():
                break
            delay = event.when - (time.perf_counter() - started)
            if delay > 0 and self._stop.wait(delay):
                break
            if event.on:
                send_on(event.pitch, event.velocity)
            else:
                send_off(event.pitch)

    def _run_pygame(self, events, program) -> None:
        import pygame.midi

        pygame.midi.init()
        device = pygame.midi.get_default_output_id()
        if device < 0:
            raise RuntimeError(
                "Системный MIDI-выход не найден. Сохраните .mid и откройте плеером."
            )
        self._port = pygame.midi.Output(device)
        self._backend = "pygame"
        self._port.set_instrument(program)
        self._schedule(
            events,
            lambda pitch, vel: self._port.note_on(pitch, vel),
            lambda pitch: self._port.note_off(pitch, 0),
        )

    def _run_rtmidi(self, events, program, port_name) -> None:
        import mido

        names = mido.get_output_names()
        if not names:
            raise RuntimeError(
                "Системный MIDI-выход не найден. Сохраните .mid и откройте плеером."
            )
        chosen = port_name if port_name in names else names[0]
        self._port = mido.open_output(chosen)
        self._backend = "rtmidi"
        self._port.send(mido.Message("program_change", program=program))
        self._schedule(
            events,
            lambda pitch, vel: self._port.send(
                mido.Message("note_on", note=pitch, velocity=vel)
            ),
            lambda pitch: self._port.send(
                mido.Message("note_off", note=pitch, velocity=0)
            ),
        )

    def _silence(self) -> None:
        """Погасить все ноты и закрыть порт -- иначе остаётся висеть гудящий звук."""
        port, self._port = self._port, None
        if port is None:
            return
        try:
            if self._backend == "pygame":
                import pygame.midi

                port.write_short(0xB0, 123, 0)  # all notes off
                port.close()
                pygame.midi.quit()
            else:
                import mido

                port.send(mido.Message("control_change", control=123, value=0))
                port.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
