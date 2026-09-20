"""Командная строка -- для пакетной обработки и автоматизации."""

from __future__ import annotations

import argparse
import sys

from .convert import Settings, convert
from .timing import DEFAULT_GRID, GRIDS
from .tuning import DEFAULT_TUNING, TUNINGS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="midi2tab",
        description="Перевод MIDI или аудио-стема в гитарные табы (.gp5 и .txt).",
    )
    p.add_argument("input", help="файл .mid или аудио (.wav .mp3 .flac .ogg .m4a)")
    p.add_argument("-o", "--out", default="", help="папка для результата")
    p.add_argument("-t", "--tracks", default="", help="номера дорожек через запятую")
    p.add_argument("--tuning", default=DEFAULT_TUNING, choices=list(TUNINGS))
    p.add_argument("--capo", type=int, default=0)
    p.add_argument("--max-fret", type=int, default=17)
    p.add_argument("--stretch", type=int, default=5, help="макс. растяжка в ладах")
    p.add_argument("--transpose", type=int, default=0)
    p.add_argument("--no-auto-transpose", action="store_true")
    p.add_argument("--grid", default=DEFAULT_GRID, choices=list(GRIDS))
    p.add_argument("--no-triplets", action="store_true")
    p.add_argument("--let-ring", action="store_true")
    p.add_argument("--tempo", type=int, default=0, help="0 -- взять из файла")
    p.add_argument("--print-tab", action="store_true", help="вывести табы в консоль")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(
        input_path=args.input,
        output_dir=args.out,
        tracks=[int(x) for x in args.tracks.split(",") if x.strip().isdigit()],
        tuning=args.tuning,
        capo=args.capo,
        max_fret=args.max_fret,
        max_stretch=args.stretch,
        transpose=args.transpose,
        auto_transpose=not args.no_auto_transpose,
        grid=args.grid,
        allow_triplets=not args.no_triplets,
        let_ring=args.let_ring,
        tempo=args.tempo,
    )
    try:
        result = convert(settings, progress=lambda m: print(f"  {m}", file=sys.stderr))
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if args.print_tab:
        print(result.tab_text)
    for line in result.summary:
        print(line)
    for path in (result.gp5_path, result.txt_path, result.midi_path):
        if path:
            print(f"Сохранено: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
