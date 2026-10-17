# RSD Tool - double-click this file (Python must be installed). No Command Prompt needed.
# The Tool Is Made By TheTurkishDude
import os
import sys
import traceback

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
try:
    import rsdtool
    sys.modules.setdefault("rsdtool", rsdtool)
    import rsdgui
    rsdgui.run(sys.argv[1:])
except Exception:
    text = traceback.format_exc()
    try:
        with open(os.path.join(here, "rsdtool_error.log"), "w") as fh:
            fh.write(text)
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()
        messagebox.showerror("RSD Tool", "RSD Tool could not start:\n\n" + text[-900:])
    except Exception:
        pass
