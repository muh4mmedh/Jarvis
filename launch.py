"""Entry point for the packaged executable.

PyInstaller runs its entry script as a top-level module, which means the
relative imports inside jarvis/__main__.py have no parent package to resolve
against. This wrapper imports the package properly and hands off.

Running from source, use `python -m jarvis` instead — this file exists for the
frozen build. It works either way.
"""
from __future__ import annotations

import multiprocessing
import os
import sys

if __name__ == "__main__":
    # Required or a frozen build re-runs the whole app in each worker process.
    multiprocessing.freeze_support()

    # Console-less builds have no stdout; print() would raise on a bad write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    # A Windows console defaults to a legacy codepage (cp1252 here), which
    # cannot encode the box-drawing characters in the banner. Force UTF-8 and
    # fall back to replacement rather than letting a decorative character
    # crash the program.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    from jarvis.__main__ import main

    main()
