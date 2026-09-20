"""
Окно приложения (tkinter -- входит в стандартную поставку Python,
отдельно ставить нечего, и сборка в .exe остаётся лёгкой).

Тяжёлая работа уходит в фоновый поток, окно не подвисает.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .convert import Settings, convert
from . import audioin, midiin
from .timing import DEFAULT_GRID, GRIDS
from .tuning import DEFAULT_TUNING, TUNINGS

APP_TITLE = "MIDI / Аудио -> Гитарные табы"
MIDI_EXTENSIONS = (".mid", ".midi")
MONO_FONT = ("Consolas", 10) if sys.platform == "win32" else ("DejaVu Sans Mono", 10)


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master.title(APP_TITLE)
        self.master.minsize(940, 720)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.queue: queue.Queue = queue.Queue()
        self.doc: midiin.MidiDocument | None = None
        self.busy = False
        self.last_result = None

        self._build_source()
        self._build_tracks()
        self._build_options()
        self._build_action()
        self._build_output()

        self.rowconfigure(4, weight=1)
        self.after(120, self._drain_queue)

    # ------------------------------------------------------------- разделы

    def _build_source(self) -> None:
        box = ttk.LabelFrame(self, text="1. Что переводим", padding=8)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.var_input = tk.StringVar()
        self.var_output = tk.StringVar()

        ttk.Label(box, text="Файл:").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.var_input).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Выбрать...", command=self.pick_input).grid(row=0, column=2)

        ttk.Label(
            box,
            text="Стем инструмента (.wav .mp3 .flac .ogg .m4a) или готовый MIDI (.mid)",
            foreground="#666",
        ).grid(row=1, column=1, sticky="w", padx=6, pady=(2, 6))

        ttk.Label(box, text="Сохранить в:").grid(row=2, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.var_output).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="Обзор...", command=self.pick_output).grid(row=2, column=2)

    def _build_tracks(self) -> None:
        box = ttk.LabelFrame(self, text="2. Дорожки MIDI (пусто = все со звуком)", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)

        self.tracks_list = tk.Listbox(box, selectmode=tk.EXTENDED, height=4, exportselection=False)
        self.tracks_list.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tracks_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tracks_list.configure(yscrollcommand=scroll.set)

        row = ttk.Frame(box)
        row.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(row, text="Выбрать все", command=self.select_all_tracks).pack(side="left")
        ttk.Button(row, text="Снять выбор", command=lambda: self.tracks_list.selection_clear(0, tk.END)).pack(
            side="left", padx=6
        )
        self.lbl_tracks = ttk.Label(row, text="Файл не загружен", foreground="#666")
        self.lbl_tracks.pack(side="left", padx=10)

    def _build_options(self) -> None:
        nb = ttk.Notebook(self)
        nb.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        # --- гитара
        g = ttk.Frame(nb, padding=10)
        nb.add(g, text="Инструмент")
        self.var_tuning = tk.StringVar(value=DEFAULT_TUNING)
        self.var_capo = tk.IntVar(value=0)
        self.var_maxfret = tk.IntVar(value=17)
        self.var_stretch = tk.IntVar(value=5)
        self.var_transpose = tk.IntVar(value=0)
        self.var_auto_transpose = tk.BooleanVar(value=True)
        self.var_fold = tk.BooleanVar(value=True)

        ttk.Label(g, text="Строй:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            g, textvariable=self.var_tuning, values=list(TUNINGS), state="readonly", width=26
        ).grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(g, text="Каподастр (лад):").grid(row=0, column=2, sticky="w", padx=(18, 0))
        ttk.Spinbox(g, from_=0, to=12, textvariable=self.var_capo, width=5).grid(row=0, column=3, padx=6)

        ttk.Label(g, text="Верхний лад:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(g, from_=5, to=24, textvariable=self.var_maxfret, width=5).grid(
            row=1, column=1, sticky="w", padx=6, pady=(8, 0)
        )
        ttk.Label(g, text="Макс. растяжка (ладов):").grid(row=1, column=2, sticky="w", padx=(18, 0), pady=(8, 0))
        ttk.Spinbox(g, from_=2, to=9, textvariable=self.var_stretch, width=5).grid(
            row=1, column=3, padx=6, pady=(8, 0)
        )

        ttk.Label(g, text="Транспонировать (полутонов):").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(g, from_=-24, to=24, textvariable=self.var_transpose, width=5).grid(
            row=2, column=1, sticky="w", padx=6, pady=(8, 0)
        )
        ttk.Checkbutton(
            g, text="Подобрать октаву автоматически", variable=self.var_auto_transpose
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            g, text="Переносить непопадающие ноты на октаву (вместо потери)", variable=self.var_fold
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=(8, 0))

        # --- ритм
        r = ttk.Frame(nb, padding=10)
        nb.add(r, text="Ритм")
        self.var_grid = tk.StringVar(value=DEFAULT_GRID)
        self.var_triplets = tk.BooleanVar(value=True)
        self.var_letring = tk.BooleanVar(value=False)
        self.var_tempo = tk.IntVar(value=0)

        ttk.Label(r, text="Квантизация:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            r, textvariable=self.var_grid, values=list(GRIDS), state="readonly", width=24
        ).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(r, text="Темп (0 = из файла):").grid(row=0, column=2, sticky="w", padx=(18, 0))
        ttk.Spinbox(r, from_=0, to=300, textvariable=self.var_tempo, width=6).grid(row=0, column=3, padx=6)

        ttk.Checkbutton(r, text="Разрешить триоли", variable=self.var_triplets).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(r, text="Let ring (пусть струны звенят)", variable=self.var_letring).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )

        # --- аудио
        a = ttk.Frame(nb, padding=10)
        nb.add(a, text="Распознавание аудио")
        self.var_onset = tk.DoubleVar(value=0.5)
        self.var_frame = tk.DoubleVar(value=0.3)
        self.var_minnote = tk.DoubleVar(value=90.0)
        self.var_limit = tk.BooleanVar(value=True)

        self._slider(a, 0, "Порог атаки:", self.var_onset, 0.1, 0.9,
                     "выше -- меньше лишних нот, но можно потерять тихие")
        self._slider(a, 1, "Порог удержания:", self.var_frame, 0.1, 0.9,
                     "выше -- короче ноты, меньше хвостов")
        self._slider(a, 2, "Мин. длина ноты (мс):", self.var_minnote, 20, 400,
                     "короче этого считается шумом")
        ttk.Checkbutton(
            a, text="Искать только в диапазоне выбранного инструмента (убирает призрачные обертоны)",
            variable=self.var_limit,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))

        self.lbl_audio_state = ttk.Label(a, text="", foreground="#666")
        self.lbl_audio_state.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _slider(self, parent, row, label, var, lo, hi, hint) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", length=220)
        scale.grid(row=row, column=1, sticky="w", padx=6)
        value = ttk.Label(parent, width=6)
        value.grid(row=row, column=2, sticky="w")

        def update(*_args):
            value.configure(text=f"{var.get():.2f}" if hi <= 1 else f"{var.get():.0f}")

        var.trace_add("write", update)
        update()
        ttk.Label(parent, text=hint, foreground="#888").grid(row=row, column=3, sticky="w", padx=10)

    def _build_action(self) -> None:
        row = ttk.Frame(self)
        row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        row.columnconfigure(1, weight=1)

        self.btn_convert = ttk.Button(row, text="Сделать табы", command=self.start_convert)
        self.btn_convert.grid(row=0, column=0)
        self.progress = ttk.Progressbar(row, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        self.btn_folder = ttk.Button(row, text="Открыть папку", command=self.open_folder, state="disabled")
        self.btn_folder.grid(row=0, column=2)

    def _build_output(self) -> None:
        box = ttk.LabelFrame(self, text="3. Результат", padding=8)
        box.grid(row=4, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.text = tk.Text(box, wrap="none", font=MONO_FONT, height=18)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        ys.grid(row=0, column=1, sticky="ns")
        xs = ttk.Scrollbar(box, orient="horizontal", command=self.text.xview)
        xs.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        self.status = ttk.Label(box, text="Выберите файл и нажмите «Сделать табы».", foreground="#444")
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------ действия

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите стем или MIDI",
            filetypes=[
                ("Аудио и MIDI", "*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif *.mid *.midi"),
                ("Аудио", "*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif"),
                ("MIDI", "*.mid *.midi"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.set_input(path)

    def set_input(self, path: str) -> None:
        self.var_input.set(path)
        if not self.var_output.get():
            self.var_output.set(os.path.dirname(os.path.abspath(path)))
        self.load_tracks(path)

    def pick_output(self) -> None:
        path = filedialog.askdirectory(title="Куда сохранять")
        if path:
            self.var_output.set(path)

    def load_tracks(self, path: str) -> None:
        self.tracks_list.delete(0, tk.END)
        self.doc = None
        if path.lower().endswith(MIDI_EXTENSIONS):
            try:
                self.doc = midiin.load(path)
            except Exception as exc:
                self.lbl_tracks.configure(text=f"Не удалось прочитать: {exc}")
                return
            for tr in self.doc.tracks:
                self.tracks_list.insert(tk.END, tr.label())
            playable = [t for t in self.doc.tracks if t.note_count and not t.is_drum]
            self.lbl_tracks.configure(
                text=f"дорожек: {len(self.doc.tracks)}, со звуком: {len(playable)}"
            )
            self.select_all_tracks()
        else:
            ok, why = audioin.available()
            self.lbl_tracks.configure(
                text="Аудио: дорожка будет создана распознаванием" if ok else why.splitlines()[0]
            )
            self.lbl_audio_state.configure(text="" if ok else why)

    def select_all_tracks(self) -> None:
        if not self.doc:
            return
        self.tracks_list.selection_clear(0, tk.END)
        for tr in self.doc.tracks:
            if tr.note_count and not tr.is_drum:
                self.tracks_list.selection_set(tr.index)

    def collect_settings(self) -> Settings:
        return Settings(
            input_path=self.var_input.get().strip(),
            output_dir=self.var_output.get().strip(),
            tracks=list(self.tracks_list.curselection()) if self.doc else [],
            tuning=self.var_tuning.get(),
            capo=int(self.var_capo.get()),
            max_fret=int(self.var_maxfret.get()),
            max_stretch=int(self.var_stretch.get()),
            transpose=int(self.var_transpose.get()),
            auto_transpose=bool(self.var_auto_transpose.get()),
            fold_octaves=bool(self.var_fold.get()),
            grid=self.var_grid.get(),
            allow_triplets=bool(self.var_triplets.get()),
            let_ring=bool(self.var_letring.get()),
            tempo=int(self.var_tempo.get()),
            onset_threshold=float(self.var_onset.get()),
            frame_threshold=float(self.var_frame.get()),
            min_note_ms=float(self.var_minnote.get()),
            limit_to_range=bool(self.var_limit.get()),
        )

    def start_convert(self) -> None:
        if self.busy:
            return
        settings = self.collect_settings()
        if not settings.input_path:
            messagebox.showwarning(APP_TITLE, "Сначала выберите файл.")
            return
        if not os.path.isfile(settings.input_path):
            messagebox.showerror(APP_TITLE, "Файл не найден.")
            return

        self.busy = True
        self.btn_convert.configure(state="disabled")
        self.btn_folder.configure(state="disabled")
        self.progress.start(12)
        self.text.delete("1.0", tk.END)
        self.status.configure(text="Работаю...")

        threading.Thread(target=self._worker, args=(settings,), daemon=True).start()

    def _worker(self, settings: Settings) -> None:
        try:
            result = convert(settings, progress=lambda m: self.queue.put(("status", m)))
            self.queue.put(("done", result))
        except Exception as exc:
            self.queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self.status.configure(text=payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._fail(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _finish(self, result) -> None:
        self.busy = False
        self.progress.stop()
        self.btn_convert.configure(state="normal")
        self.btn_folder.configure(state="normal")
        self.last_result = result

        lines = [result.tab_text, "", "-" * 60]
        lines.extend(result.summary)
        files = [p for p in (result.gp5_path, result.txt_path, result.midi_path) if p]
        if files:
            lines.append("")
            lines.append("Сохранено:")
            lines.extend(f"  {p}" for p in files)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", "\n".join(lines))
        self.status.configure(text=result.summary[-1] if result.summary else "Готово.")

    def _fail(self, message: str) -> None:
        self.busy = False
        self.progress.stop()
        self.btn_convert.configure(state="normal")
        self.status.configure(text="Ошибка.")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", message)
        messagebox.showerror(APP_TITLE, message.split("\n\n")[0])

    def open_folder(self) -> None:
        target = self.var_output.get().strip()
        if not target or not os.path.isdir(target):
            return
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    app = App(root)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.set_input(sys.argv[1])
    root.mainloop()


if __name__ == "__main__":
    main()
