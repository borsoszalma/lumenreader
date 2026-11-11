"""Lumen Reader (Python edition). Double-click to start, or: py LumenReader.pyw [file]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 9):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Lumen Reader needs Python 3.9 or newer.", "Lumen Reader", 0x10)
    sys.exit(1)

from lumen.app import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
