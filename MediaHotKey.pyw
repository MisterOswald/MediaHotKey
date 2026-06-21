#!/usr/bin/env pythonw
"""
No-console launcher for MediaHotKey.

Double-click this file (or run `pythonw MediaHotKey.pyw`) to start the app
WITHOUT a cmd / console window. The .pyw extension tells Windows to use
pythonw.exe (the windowless Python interpreter) instead of python.exe.

For a console (e.g. to see errors while setting up), use `python run.py`.
"""

from mediahotkey.gui import main

if __name__ == "__main__":
    main()
