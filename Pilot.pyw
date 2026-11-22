"""Double-click this file to open the Pilot window (no black console window).

If something goes wrong before the window opens, the reason is shown in a message box and saved
to data/crash.log, because a .pyw file has no console to print to.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    if "--background" in sys.argv[1:]:
        from src.cli import run_daemon
        run_daemon()
    else:
        from src.ui.app import run
        run()
except Exception:
    details = traceback.format_exc()
    try:
        from src.core.settings import Settings
        details = Settings.load().redact(details)  # never write a key into the log
    except Exception:
        pass
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    with open(os.path.join(HERE, "data", "crash.log"), "w", encoding="utf-8") as fh:
        fh.write(details)
    import tkinter
    from tkinter import messagebox
    tkinter.Tk().withdraw()
    messagebox.showerror("Pilot", "Pilot could not start. The details are in data/crash.log.")
