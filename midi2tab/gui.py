"""
Окно приложения.

Слева -- настройки по шагам, справа -- табулатура и журнал работы.
Любая долгая операция (разделение трека, распознавание, конвертация)
уходит в фоновый поток и общается с окном через очередь, поэтому
интерфейс не подвисает и работу видно по ходу дела.
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

from . import audioin, midiin, playback, separate, theme
from .convert import Settings, convert
from .timing import DEFAULT_GRID, GRIDS
from .tuning import DEFAULT_TUNING, TUNINGS

APP_TITLE = "MidiToTab — аудио и MIDI в гитарные табы"
MIDI_EXTENSIONS = (".mid", ".midi")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=0)
        self.master.title(APP_TITLE)
        self.master.minsize(1060, 680)
        self.master.geometry("1380x900")
        theme.apply(master)

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.queue: queue.Queue = queue.Queue()
        self.doc: midiin.MidiDocument | None = None
        self.busy = False
        self.result = None
        self.player = playback.Player()
        self.stems: dict[str, str] = {}

        self._build_left()
        self._build_right()
        self._refresh_capabilities()
        self.after(100, self._drain_queue)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ левая часть

    def _build_left(self) -> None:
        # Настроек много, а экраны бывают невысокие -- колонку делаем
        # прокручиваемой, иначе нижние поля просто не помещаются.
        holder = ttk.Frame(self)
        holder.grid(row=0, column=0, sticky="ns")
        holder.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            holder, bg=theme.BG, highlightthickness=0, width=470, takefocus=0
        )
        canvas.grid(row=0, column=0, sticky="ns")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        left = ttk.Frame(canvas, padding=(14, 14, 7, 14))
        window = canvas.create_window((0, 0), window=left, anchor="nw")

        def _resize(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window, width=canvas.winfo_width())

        left.bind("<Configure>", _resize)
        canvas.bind("<Configure>", _resize)

        def _wheel(event):
            step = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
            canvas.yview_scroll(step, "units")

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(sequence, _wheel)

        left.columnconfigure(0, weight=1)

        # --- шаг 1: файл
        src = ttk.LabelFrame(left, text=" 1 · Исходный файл ", padding=10)
        src.grid(row=0, column=0, sticky="ew")
        src.columnconfigure(0, weight=1)

        self.var_input = tk.StringVar()
        self.var_output = tk.StringVar()

        entry = ttk.Entry(src, textvariable=self.var_input, width=46)
        entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(src, text="Выбрать", command=self.pick_input).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(
            src,
            text="Целый трек, стем инструмента (.wav .mp3 .flac) или MIDI (.mid)",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))

        ttk.Label(src, text="Сохранять в").grid(row=2, column=0, sticky="w")
        ttk.Entry(src, textvariable=self.var_output).grid(row=3, column=0, sticky="ew", pady=(3, 0))
        ttk.Button(src, text="Обзор", command=self.pick_output).grid(row=3, column=1, padx=(6, 0), pady=(3, 0))

        # --- шаг 2: разделение
        sep = ttk.LabelFrame(left, text=" 2 · Разделение трека на дорожки ", padding=10)
        sep.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        sep.columnconfigure(0, weight=1)

        self.var_sep_model = tk.StringVar(value=separate.DEFAULT_MODEL)
        self.var_stem = tk.StringVar(value="")

        ttk.Label(
            sep,
            text="Нужно, если файл — готовая песня. Для стема шаг можно пропустить.",
            style="Muted.TLabel",
            wraplength=380,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Combobox(
            sep, textvariable=self.var_sep_model, values=list(separate.MODELS),
            state="readonly", width=34,
        ).grid(row=1, column=0, sticky="ew")
        self.btn_separate = ttk.Button(sep, text="Разделить", command=self.start_separate)
        self.btn_separate.grid(row=1, column=1, padx=(6, 0))

        self.lbl_sep = ttk.Label(sep, text="", style="Muted.TLabel", wraplength=380)
        self.lbl_sep.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.stems_box = ttk.Combobox(sep, textvariable=self.var_stem, state="disabled", width=34)
        self.stems_box.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.stems_box.bind("<<ComboboxSelected>>", self._use_stem)
        self.btn_use_stem = ttk.Button(sep, text="Взять", command=self._use_stem, state="disabled")
        self.btn_use_stem.grid(row=3, column=1, padx=(6, 0), pady=(6, 0))

        # --- шаг 3: дорожки MIDI
        trk = ttk.LabelFrame(left, text=" 3 · Дорожки MIDI ", padding=10)
        trk.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        trk.columnconfigure(0, weight=1)

        self.tracks_list = tk.Listbox(trk, selectmode=tk.EXTENDED, height=4, exportselection=False)
        theme.style_listbox(self.tracks_list)
        self.tracks_list.grid(row=0, column=0, sticky="ew")
        bar = ttk.Scrollbar(trk, orient="vertical", command=self.tracks_list.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tracks_list.configure(yscrollcommand=bar.set)

        row = ttk.Frame(trk)
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(row, text="Все", command=self.select_all_tracks).pack(side="left")
        ttk.Button(row, text="Снять", command=lambda: self.tracks_list.selection_clear(0, tk.END)).pack(
            side="left", padx=6
        )
        self.lbl_tracks = ttk.Label(row, text="файл не загружен", style="Muted.TLabel")
        self.lbl_tracks.pack(side="left", padx=8)

        # --- шаг 4: настройки
        self._build_options(left)

    def _build_options(self, parent: ttk.Frame) -> None:
        nb = ttk.Notebook(parent)
        nb.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        # Инструмент
        g = ttk.Frame(nb, padding=12)
        nb.add(g, text="Инструмент")
        self.var_tuning = tk.StringVar(value=DEFAULT_TUNING)
        self.var_capo = tk.IntVar(value=0)
        self.var_maxfret = tk.IntVar(value=17)
        self.var_stretch = tk.IntVar(value=5)
        self.var_transpose = tk.IntVar(value=0)
        self.var_auto_transpose = tk.BooleanVar(value=True)
        self.var_fold = tk.BooleanVar(value=True)

        ttk.Label(g, text="Строй").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(g, textvariable=self.var_tuning, values=list(TUNINGS),
                     state="readonly", width=24).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Каподастр").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(g, from_=0, to=12, textvariable=self.var_capo, width=6).grid(
            row=1, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Верхний лад").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Spinbox(g, from_=5, to=24, textvariable=self.var_maxfret, width=6).grid(
            row=2, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Растяжка, ладов").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(g, from_=2, to=9, textvariable=self.var_stretch, width=6).grid(
            row=3, column=1, sticky="w", padx=6)
        ttk.Label(g, text="Транспонировать").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Spinbox(g, from_=-24, to=24, textvariable=self.var_transpose, width=6).grid(
            row=4, column=1, sticky="w", padx=6)
        ttk.Checkbutton(g, text="Подобрать октаву автоматически",
                        variable=self.var_auto_transpose).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(g, text="Переносить ноты вне грифа на октаву",
                        variable=self.var_fold).grid(
            row=6, column=0, columnspan=2, sticky="w")

        # Ритм
        r = ttk.Frame(nb, padding=12)
        nb.add(r, text="Ритм")
        self.var_grid = tk.StringVar(value=DEFAULT_GRID)
        self.var_triplets = tk.BooleanVar(value=True)
        self.var_letring = tk.BooleanVar(value=False)
        self.var_tempo = tk.IntVar(value=0)

        ttk.Label(r, text="Квантизация").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(r, textvariable=self.var_grid, values=list(GRIDS),
                     state="readonly", width=24).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(r, text="Темп").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(r, from_=0, to=300, textvariable=self.var_tempo, width=6).grid(
            row=1, column=1, sticky="w", padx=6)
        ttk.Label(r, text="0 — взять из файла. Для аудио укажите настоящий темп:\n"
                         "от него зависит, куда лягут ноты по долям.",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 6))
        ttk.Checkbutton(r, text="Разрешить триоли", variable=self.var_triplets).grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(r, text="Let ring — пусть струны звенят", variable=self.var_letring).grid(
            row=4, column=0, columnspan=2, sticky="w")

        # Распознавание
        a = ttk.Frame(nb, padding=12)
        nb.add(a, text="Распознавание")
        self.var_onset = tk.DoubleVar(value=0.5)
        self.var_frame = tk.DoubleVar(value=0.3)
        self.var_minnote = tk.DoubleVar(value=90.0)
        self.var_limit = tk.BooleanVar(value=True)
        self.var_ghosts = tk.BooleanVar(value=True)
        self.var_maxpoly = tk.IntVar(value=0)

        self._slider(a, 0, "Порог атаки", self.var_onset, 0.1, 0.9,
                     "выше — меньше лишних нот")
        self._slider(a, 1, "Порог удержания", self.var_frame, 0.1, 0.9,
                     "выше — короче ноты")
        self._slider(a, 2, "Мин. нота, мс", self.var_minnote, 20, 400,
                     "короче — считается шумом")
        ttk.Checkbutton(a, text="Только диапазон инструмента", variable=self.var_limit).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(a, text="Убирать призрачные обертоны", variable=self.var_ghosts).grid(
            row=4, column=0, columnspan=4, sticky="w")
        ttk.Label(a, text="Макс. одновременных нот").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(a, from_=0, to=8, textvariable=self.var_maxpoly, width=6).grid(
            row=5, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(a, text="0 — без ограничения", style="Muted.TLabel").grid(
            row=5, column=2, columnspan=2, sticky="w")
        self.lbl_audio = ttk.Label(a, text="", style="Muted.TLabel", wraplength=360)
        self.lbl_audio.grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _slider(self, parent, row, label, var, lo, hi, hint) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", length=150).grid(
            row=row, column=1, sticky="w", padx=6)
        value = ttk.Label(parent, width=5, style="Muted.TLabel")
        value.grid(row=row, column=2, sticky="w")

        def update(*_a):
            value.configure(text=f"{var.get():.2f}" if hi <= 1 else f"{var.get():.0f}")

        var.trace_add("write", update)
        update()
        ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row, column=3, sticky="w", padx=8)

    # ------------------------------------------------------------ правая часть

    def _build_right(self) -> None:
        right = ttk.Frame(self, padding=(7, 14, 14, 14))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # строка действия
        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        bar.columnconfigure(1, weight=1)

        self.btn_convert = ttk.Button(bar, text="Сделать табы", style="Accent.TButton",
                                      command=self.start_convert)
        self.btn_convert.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)
        self.btn_folder = ttk.Button(bar, text="Папка", command=self.open_folder, state="disabled")
        self.btn_folder.grid(row=0, column=2)

        # табулатура
        out = ttk.LabelFrame(right, text=" Табулатура ", padding=8)
        out.grid(row=1, column=0, sticky="nsew")
        out.columnconfigure(0, weight=1)
        out.rowconfigure(0, weight=1)

        self.text = tk.Text(out, wrap="none", height=22)
        theme.style_text(self.text)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(out, orient="vertical", command=self.text.yview)
        ys.grid(row=0, column=1, sticky="ns")
        xs = ttk.Scrollbar(out, orient="horizontal", command=self.text.xview)
        xs.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        # прослушивание
        play = ttk.LabelFrame(right, text=" Прослушать ", padding=10)
        play.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        play.columnconfigure(1, weight=1)

        self.var_instrument = tk.StringVar(value=playback.DEFAULT_INSTRUMENT)
        ttk.Label(play, text="Тембр").grid(row=0, column=0, sticky="w")
        ttk.Combobox(play, textvariable=self.var_instrument, values=list(playback.INSTRUMENTS),
                     state="readonly", width=22).grid(row=0, column=1, sticky="w", padx=8)
        self.btn_play = ttk.Button(play, text="▶  Играть", command=self.toggle_play, state="disabled")
        self.btn_play.grid(row=0, column=2, padx=(0, 6))
        self.btn_save_midi = ttk.Button(play, text="Сохранить .mid", command=self.save_midi,
                                        state="disabled")
        self.btn_save_midi.grid(row=0, column=3)
        self.lbl_play = ttk.Label(play, text="", style="Muted.TLabel", wraplength=520)
        self.lbl_play.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        self.status = ttk.Label(right, text="Выберите файл и нажмите «Сделать табы».",
                                style="Muted.TLabel")
        self.status.grid(row=3, column=0, sticky="w", pady=(8, 0))

    # ------------------------------------------------------------- готовность

    def _refresh_capabilities(self) -> None:
        ok_audio, why_audio = audioin.available()
        self.lbl_audio.configure(
            text="Распознавание готово к работе." if ok_audio else why_audio,
            style="Ok.TLabel" if ok_audio else "Warn.TLabel",
        )
        ok_sep, why_sep = separate.available()
        self.lbl_sep.configure(
            text=(f"Готово, считает на {separate.describe_device()}." if ok_sep else why_sep),
            style="Ok.TLabel" if ok_sep else "Warn.TLabel",
        )
        self.btn_separate.configure(state="normal" if ok_sep else "disabled")
        ok_play, why_play = playback.available()
        ports = playback.output_ports()
        if ok_play and ports:
            self.lbl_play.configure(text=f"Выход: {ports[0]}", style="Muted.TLabel")
        elif ok_play:
            self.lbl_play.configure(
                text="MIDI-выход не найден. Сохраните .mid и откройте плеером.",
                style="Warn.TLabel")
        else:
            self.lbl_play.configure(text=why_play, style="Warn.TLabel")

    # ---------------------------------------------------------------- события

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Трек, стем или MIDI",
            filetypes=[
                ("Всё поддерживаемое", "*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif *.mid *.midi"),
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
                self.lbl_tracks.configure(text=f"не прочитать: {exc}", style="Warn.TLabel")
                return
            for tr in self.doc.tracks:
                self.tracks_list.insert(tk.END, tr.label())
            playable = [t for t in self.doc.tracks if t.note_count and not t.is_drum]
            self.lbl_tracks.configure(
                text=f"всего {len(self.doc.tracks)}, со звуком {len(playable)}",
                style="Muted.TLabel")
            self.select_all_tracks()
        else:
            self.lbl_tracks.configure(
                text="аудио — дорожка появится после распознавания", style="Muted.TLabel")

    def select_all_tracks(self) -> None:
        if not self.doc:
            return
        self.tracks_list.selection_clear(0, tk.END)
        for tr in self.doc.tracks:
            if tr.note_count and not tr.is_drum:
                self.tracks_list.selection_set(tr.index)

    # ------------------------------------------------------------- разделение

    def start_separate(self) -> None:
        if self.busy:
            return
        src = self.var_input.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning(APP_TITLE, "Сначала выберите файл.")
            return
        if src.lower().endswith(MIDI_EXTENSIONS):
            messagebox.showinfo(APP_TITLE, "Разделять можно только аудио, MIDI уже разложен на дорожки.")
            return
        out = self.var_output.get().strip() or os.path.dirname(os.path.abspath(src))
        self._begin("Разделяю трек...")
        threading.Thread(
            target=self._separate_worker,
            args=(src, os.path.join(out, "stems"), self.var_sep_model.get()),
            daemon=True,
        ).start()

    def _separate_worker(self, src: str, out: str, model: str) -> None:
        try:
            res = separate.separate(src, out, model, progress=lambda m: self.queue.put(("status", m)))
            self.queue.put(("stems", res))
        except Exception as exc:
            self.queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

    def _show_stems(self, res: separate.SeparateResult) -> None:
        self._end()
        self.stems = res.stems
        labels = [f"{res.label_for(k)} — {os.path.basename(v)}" for k, v in res.stems.items()]
        self.stems_box.configure(values=labels, state="readonly")
        self.btn_use_stem.configure(state="normal")
        preferred = res.guitar
        if preferred:
            index = list(res.stems).index("guitar")
            self.stems_box.current(index)
            self.var_input.set(preferred)
            self.lbl_sep.configure(
                text=f"Готово. Гитара выбрана автоматически: {os.path.basename(preferred)}",
                style="Ok.TLabel")
        else:
            self.stems_box.current(0)
            self.lbl_sep.configure(
                text=f"Готово, дорожек: {len(res.stems)}. Выберите нужную.", style="Ok.TLabel")
        self.status.configure(text=f"Дорожки сохранены: {res.out_dir}")

    def _use_stem(self, _event=None) -> None:
        if not self.stems:
            return
        index = self.stems_box.current()
        if index < 0:
            return
        path = list(self.stems.values())[index]
        self.set_input(path)
        self.status.configure(text=f"Выбрана дорожка: {os.path.basename(path)}")

    # ------------------------------------------------------------ конвертация

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
            remove_ghosts=bool(self.var_ghosts.get()),
            max_polyphony=int(self.var_maxpoly.get()),
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
        self.player.stop()
        self._begin("Работаю...")
        self.text.delete("1.0", tk.END)
        threading.Thread(target=self._convert_worker, args=(settings,), daemon=True).start()

    def _convert_worker(self, settings: Settings) -> None:
        try:
            result = convert(settings, progress=lambda m: self.queue.put(("status", m)))
            self.queue.put(("done", result))
        except Exception as exc:
            self.queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

    # ----------------------------------------------------------- прослушивание

    def toggle_play(self) -> None:
        if self.player.playing:
            self.player.stop()
            self.btn_play.configure(text="▶  Играть")
            return
        if not self.result:
            return
        try:
            self.player.play(
                self.result.placements,
                self.collect_settings().fretboard(),
                self.result.tempo,
                self.var_instrument.get(),
                on_finish=lambda: self.queue.put(("played", None)),
                on_error=lambda m: self.queue.put(("playerror", m)),
            )
            self.btn_play.configure(text="■  Стоп")
            self.lbl_play.configure(text=f"Играю тембром «{self.var_instrument.get()}»",
                                    style="Ok.TLabel")
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))

    def save_midi(self) -> None:
        if not self.result:
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить MIDI", defaultextension=".mid",
            filetypes=[("MIDI", "*.mid")],
            initialfile=Path(self.var_input.get()).stem + "_tab.mid",
        )
        if not path:
            return
        playback.write_midi(
            self.result.placements, self.collect_settings().fretboard(),
            path, self.result.tempo, self.var_instrument.get(),
        )
        self.status.configure(text=f"Сохранено: {path}")

    # ------------------------------------------------------------- вывод, цикл

    def _begin(self, message: str) -> None:
        self.busy = True
        for button in (self.btn_convert, self.btn_separate, self.btn_play, self.btn_folder):
            button.configure(state="disabled")
        self.progress.start(14)
        self.status.configure(text=message, style="Muted.TLabel")

    def _end(self) -> None:
        self.busy = False
        self.progress.stop()
        self.btn_convert.configure(state="normal")
        ok_sep, _ = separate.available()
        self.btn_separate.configure(state="normal" if ok_sep else "disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self.status.configure(text=payload, style="Muted.TLabel")
                elif kind == "done":
                    self._finish(payload)
                elif kind == "stems":
                    self._show_stems(payload)
                elif kind == "played":
                    self.btn_play.configure(text="▶  Играть")
                elif kind == "playerror":
                    self.btn_play.configure(text="▶  Играть")
                    self.lbl_play.configure(text=payload, style="Warn.TLabel")
                    messagebox.showwarning(APP_TITLE, payload)
                elif kind == "error":
                    self._fail(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _finish(self, result) -> None:
        self._end()
        self.result = result
        self.btn_folder.configure(state="normal")
        self.btn_play.configure(state="normal")
        self.btn_save_midi.configure(state="normal")
        self._render(result)
        self.status.configure(
            text=result.summary[-1] if result.summary else "Готово.", style="Ok.TLabel")

    def _render(self, result) -> None:
        """Вывести табулатуру с подсветкой ладов и отчёт под ней."""
        self.text.delete("1.0", tk.END)
        for line in result.tab_text.splitlines():
            stripped = line.lstrip()
            if len(stripped) > 1 and stripped[1] == "|":
                self.text.insert(tk.END, line[:2], "string")
                start = self.text.index("end-1c")
                self.text.insert(tk.END, line[2:] + "\n")
                self._highlight_frets(start, line[2:])
            else:
                self.text.insert(tk.END, line + "\n", "head")

        self.text.insert(tk.END, "\n" + "─" * 60 + "\n", "head")
        for message in result.summary:
            tag = "bad" if ("не " in message or "Убрано" in message or "вне " in message) else "good"
            self.text.insert(tk.END, message + "\n", tag)
        files = [p for p in (result.gp5_path, result.txt_path, result.midi_path) if p]
        if files:
            self.text.insert(tk.END, "\nСохранено:\n", "head")
            for path in files:
                self.text.insert(tk.END, f"  {path}\n", "head")

    def _highlight_frets(self, start: str, body: str) -> None:
        line = int(start.split(".")[0])
        offset = int(start.split(".")[1])
        index = 0
        while index < len(body):
            if body[index].isdigit():
                end = index
                while end < len(body) and body[end].isdigit():
                    end += 1
                self.text.tag_add(
                    "fret", f"{line}.{offset + index}", f"{line}.{offset + end}")
                index = end
            else:
                index += 1

    def _fail(self, message: str) -> None:
        self._end()
        self.status.configure(text="Ошибка.", style="Warn.TLabel")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", message, "bad")
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

    def _on_close(self) -> None:
        self.player.stop()
        self.master.destroy()


def main() -> None:
    root = tk.Tk()
    app = App(root)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.set_input(sys.argv[1])
    root.mainloop()


if __name__ == "__main__":
    main()
