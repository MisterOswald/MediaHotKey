#!/usr/bin/env python3
"""
Launcher for MediaHotKey.

Usage:
    python run.py              # open the desktop UI
    python run.py --headless   # run the hotkey engine with no window
                               # (uses the saved config.json)
"""

import sys


def _headless():
    from mediahotkey.config import load_config
    from mediahotkey.engine import Engine

    config = load_config()
    engine = Engine(config, log=print)
    engine.start()
    print("Running headless. Press Ctrl+C to quit.")
    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        engine.stop()
        print("\nBye.")


def main():
    if "--headless" in sys.argv or "-H" in sys.argv:
        _headless()
    else:
        from mediahotkey.gui import main as gui_main
        gui_main()


if __name__ == "__main__":
    main()
