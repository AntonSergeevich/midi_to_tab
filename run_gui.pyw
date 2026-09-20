"""Запуск окна двойным кликом в Windows (.pyw -- без чёрной консоли)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from midi2tab.gui import main

main()
