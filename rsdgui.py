#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rsdgui.py - RSD Tool window (decode / encode / replace / view / listen)
=======================================================================
    The Tool Is Made By TheTurkishDude

Radical Entertainment RSD sound files (The Incredible Hulk: Ultimate Destruction, PS2).
Double-click RSD_Tool.pyw (or run  python rsdgui.py)  - no Command Prompt needed.
"""
import os
import queue
import struct
import sys
import threading

import rsdtool as R
import rsdplayer as P

CREDIT = R.CREDIT
CODECS = ("RADP", "VAG", "PCM", "PCMB")


# ---------------------------------------------------------------------------
# conversion jobs (no widgets in here, so they can be tested and run in a thread)
# ---------------------------------------------------------------------------
class LogStream:
    """File-like object that forwards whole lines to log(line)."""

    def __init__(self, log):
        self.log = log

    def write(self, s):
        for line in str(s).splitlines():
            if line.strip():
                self.log(line.rstrip())

    def flush(self):
        pass


def make_opts(codec="RADP", template="", rate=0, srcpath="", desc="", force=False, out=""):
    o = R.Opts()
    o.codec = codec.lower()
    o.template = template
    o.rate = rate
    o.srcpath = srcpath
    o.desc = desc
    o.force = force
    o.out = out
    return o


def _run_each(mode, paths, o, log):
    stream = LogStream(log)
    ok = failed = 0
    for p in paths:
        try:
            R.process(mode, p, len(paths) > 1, o, out=stream)
            ok += 1
        except (R.RSDError, OSError, struct.error, ValueError) as e:
            log("ERROR %s: %s" % (os.path.basename(p), e))
            failed += 1
    log("Done: %d converted, %d failed." % (ok, failed))
    return ok, failed


def do_decode(paths, outdir, force, log):
    """RSD -> WAV into outdir."""
    os.makedirs(outdir, exist_ok=True)
    return _run_each("d", paths, make_opts(force=force, out=outdir), log)


def do_encode(paths, outdir, o, log):
    """WAV -> RSD into outdir with the options in `o`."""
    os.makedirs(outdir, exist_ok=True)
    o.out = outdir
    return _run_each("e", paths, o, log)


def do_replace(rsd_path, wav_path, out_path, codec, log):
    """Encode `wav_path` as a replacement for `rsd_path`: same header text, chosen codec."""
    o = make_opts(codec=codec, template=rsd_path, force=True, out=out_path)
    return _run_each("e", [wav_path], o, log)


class JobRunner:
    """One background job at a time; the window polls drain() for log lines / completion."""

    def __init__(self):
        self.q = queue.Queue()
        self.busy = False

    def start(self, fn):
        self.busy = True

        def work():
            try:
                fn(lambda m: self.q.put(("log", m)))
            except BaseException as e:      # shown in the log, never lost in the thread
                self.q.put(("done", e))
                return
            self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        events = []
        try:
            while True:
                events.append(self.q.get_nowait())
        except queue.Empty:
            pass
        if any(k == "done" for k, _ in events):
            self.busy = False
        return events


# ---------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------
def run(args=()):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception:
        sys.stderr.write("tkinter is not available in this Python installation.\n"
                         "Reinstall Python from python.org (tick 'tcl/tk') or use the command line: "
                         "python rsdtool.py -h\n")
        return 3

    br = P.Browser()
    jobs = JobRunner()
    root = tk.Tk()
    root.title("RSD Tool  -  Hulk: Ultimate Destruction  -  " + CREDIT)
    root.geometry("1040x720")
    root.minsize(860, 560)
    after_done = []                       # callbacks run (in the window thread) when a job ends
    convert_buttons = []

    # ---- top bar -------------------------------------------------------------
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=8, pady=6)
    status = tk.Label(root, anchor="w", text=CREDIT, fg="#b00020", font=("Segoe UI", 9, "bold"))
    status.pack(side="bottom", fill="x", padx=8, pady=3)

    # ---- log (bottom) --------------------------------------------------------
    logframe = tk.LabelFrame(root, text="Log")
    logframe.pack(side="bottom", fill="x", padx=8, pady=4)
    logbox = tk.Text(logframe, height=6, wrap="none")
    logsb = tk.Scrollbar(logframe, command=logbox.yview)
    logbox.config(yscrollcommand=logsb.set)
    logsb.pack(side="right", fill="y")
    logbox.pack(side="left", fill="both", expand=True)

    # ---- convert panel -------------------------------------------------------
    conv = tk.LabelFrame(root, text="Convert")
    conv.pack(side="bottom", fill="x", padx=8, pady=4)
    row1 = tk.Frame(conv)
    row1.pack(fill="x", padx=6, pady=3)
    row2 = tk.Frame(conv)
    row2.pack(fill="x", padx=6, pady=3)
    row3 = tk.Frame(conv)
    row3.pack(fill="x", padx=6, pady=3)

    # ---- list + viewer -------------------------------------------------------
    body = tk.PanedWindow(root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=8)
    cols = ("codec", "ch", "rate", "time")
    tree = ttk.Treeview(body, columns=cols, selectmode="extended")
    tree.heading("#0", text="Sound")
    for c, wd in zip(cols, (60, 30, 60, 60)):
        tree.heading(c, text=c)
        tree.column(c, width=wd, anchor="center")
    tree.column("#0", width=250)
    body.add(tree, minsize=300)
    right = tk.Frame(body)
    body.add(right, minsize=420)
    info = tk.Label(right, anchor="w", justify="left", text="Add .rsd / .wav files or a folder to begin.")
    info.pack(fill="x")
    canvas = tk.Canvas(right, bg="#181a20", highlightthickness=0, height=200)
    canvas.pack(fill="both", expand=True, pady=6)
    clock = tk.Label(right, anchor="w", text="00:00.0 / 00:00.0")
    clock.pack(fill="x")

    loopvar = tk.BooleanVar(value=False)
    force_var = tk.BooleanVar(value=False)
    use_sel_header = tk.BooleanVar(value=False)
    codec_var = tk.StringVar(value="RADP")
    rate_var = tk.StringVar(value="")
    src_var = tk.StringVar(value="")
    desc_var = tk.StringVar(value="")
    tpl_var = tk.StringVar(value="")        # header template file chosen by the user

    # ---- helpers -------------------------------------------------------------
    def say(t):
        status.config(text=t)

    def log(msg):
        logbox.insert("end", msg + "\n")
        logbox.see("end")

    def sel_indices():
        return [int(i) for i in tree.selection()]

    def refresh_list(select=None):
        tree.delete(*tree.get_children())
        for i, it in enumerate(br.items):
            tree.insert("", "end", iid=str(i), text=it["name"],
                        values=(it["codec"], it["channels"], it["rate"], fmt_time(it["seconds"])))
        if select is not None and 0 <= select < len(br.items):
            tree.selection_set(str(select))

    fmt_time = P.fmt_time

    def redraw():
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        audio = br.player.audio
        if audio is None or w < 10:
            return
        lh = h / audio.channels
        for c in range(audio.channels):
            mid = c * lh + lh / 2
            canvas.create_line(0, mid, w, mid, fill="#3c4250")
            amp = lh / 2 - 3
            colour = "#60c896" if c % 2 == 0 else "#6eaaf0"
            for x, (lo, hi) in enumerate(P.peaks(audio.samples[c], w)):
                y1 = mid - hi / 32768.0 * amp
                y2 = max(mid - lo / 32768.0 * amp, y1 + 1)
                canvas.create_line(x, y1, x, y2, fill=colour)
        canvas.create_line(0, 0, 0, h, fill="#ffd24a", width=2, tags="head")

    def on_select(_e=None):
        idx = sel_indices()
        if not idx:
            return
        i = idx[0]
        say("Decoding %s ..." % br.items[i]["name"])
        root.update_idletasks()
        try:
            audio, meta = br.select(i)
        except (R.RSDError, OSError, struct.error) as e:
            messagebox.showerror("Error", "%s: %s" % (br.items[i]["name"], e))
            say(CREDIT)
            return
        info.config(text="\n".join(P.describe(br.items[i]["path"], audio, meta)[1:]))
        redraw()
        say("%s  -  %s" % (br.items[i]["name"], CREDIT))

    # ---- player --------------------------------------------------------------
    def toggle_play():
        p = br.player
        if p.audio is None:
            return
        if not p.available:
            messagebox.showinfo("No audio player",
                                "No audio player was found on this system (see README: RSDTOOL_PLAYER).")
            return
        p.loop = loopvar.get()
        if p.state == "playing":
            p.pause()
        else:
            p.play()

    def stop():
        br.player.stop()

    def seek_click(e):
        p = br.player
        if p.audio is None:
            return
        w = max(1, canvas.winfo_width())
        p.seek(p.duration * min(max(e.x / w, 0.0), 1.0))

    def seek_rel(d):
        p = br.player
        if p.audio:
            p.seek(p.position + d)

    def on_loop():
        br.player.loop = loopvar.get()
        if br.player.state == "playing":
            br.player.play(br.player.position)

    def typing():
        try:
            return root.focus_get().winfo_class() in ("Entry", "TCombobox", "Text")
        except Exception:
            return False

    def key(fn, *a):
        def handler(_e=None):
            if not typing():
                fn(*a)
        return handler

    # ---- background jobs -----------------------------------------------------
    def set_busy(busy):
        root.config(cursor="watch" if busy else "")
        for b in convert_buttons:
            b.config(state="disabled" if busy else "normal")

    def start_job(title, fn, after=None):
        if jobs.busy:
            messagebox.showinfo("Busy", "Please wait for the current job to finish.")
            return
        after_done[:] = [after] if after else []
        log("== " + title)
        set_busy(True)
        jobs.start(fn)

    def poll():
        for kind, payload in jobs.drain():
            if kind == "log":
                log(payload)
            else:
                set_busy(False)
                if payload is not None:
                    log("ERROR: %s" % payload)
                    say("Failed - see the log")
                else:
                    say("Finished  -  " + CREDIT)
                    for cb in after_done:
                        cb()
                after_done[:] = []
        p = br.player
        p.update()
        if p.audio is not None:
            w = canvas.winfo_width()
            x = int(w * p.position / p.duration) if p.duration else 0
            canvas.coords("head", x, 0, x, canvas.winfo_height())
            clock.config(text="%s / %s   [%s]" % (fmt_time(p.position), fmt_time(p.duration), p.state))
        root.after(60, poll)

    # ---- list buttons --------------------------------------------------------
    def add_files():
        f = filedialog.askopenfilenames(title="Add sounds", filetypes=[
            ("RSD / WAV sounds", "*.rsd *.wav"), ("RSD", "*.rsd"), ("WAV", "*.wav"), ("All files", "*.*")])
        if f:
            first = len(br.items)
            n = br.add_paths(f)
            refresh_list(first if n else None)
            say("%d sound(s) added" % n)

    def add_folder():
        d = filedialog.askdirectory(title="Add a folder with .rsd / .wav files (sub-folders included)")
        if d:
            first = len(br.items)
            n = br.add_paths([d], True)
            refresh_list(first if n else None)
            say("%d sound(s) added from %s" % (n, d))

    def remove_sel():
        idx = sel_indices()
        if idx:
            br.remove(idx)
            refresh_list()
            canvas.delete("all")
            info.config(text="")

    def clear_all():
        br.clear()
        refresh_list()
        canvas.delete("all")
        info.config(text="Add .rsd / .wav files or a folder to begin.")

    # ---- convert actions -----------------------------------------------------
    def rsd_paths(indices):
        return [br.items[i]["path"] for i in indices if br.items[i]["kind"] == "RSD"]

    def decode(all_items):
        idx = list(range(len(br.items))) if all_items else sel_indices()
        paths = rsd_paths(idx)
        if not paths:
            messagebox.showinfo("Decode", "Select (or add) at least one .rsd file first.")
            return
        d = filedialog.askdirectory(title="Save the decoded WAV files in folder")
        if d:
            force = force_var.get()
            start_job("Decode %d RSD file(s) -> WAV" % len(paths), lambda lg: do_decode(paths, d, force, lg))

    def read_options():
        """Values of the encode fields -> (codec, template, rate, srcpath, desc) or None on a bad entry."""
        rate = 0
        if rate_var.get().strip():
            try:
                rate = int(rate_var.get().strip())
                if rate <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Sample rate", "Sample rate must be a whole number such as 22050 (or empty).")
                return None
        template = tpl_var.get().strip()
        if use_sel_header.get():
            idx = rsd_paths(sel_indices())
            if not idx:
                messagebox.showinfo("Header", "'Use the selected RSD's header' needs an .rsd selected in the list.")
                return None
            template = idx[0]
        return codec_var.get(), template, rate, src_var.get().strip(), desc_var.get().strip()

    def encode():
        opts = read_options()
        if opts is None:
            return
        f = filedialog.askopenfilenames(title="Choose the WAV file(s) to encode", filetypes=[
            ("WAV", "*.wav"), ("All files", "*.*")])
        if not f:
            return
        d = filedialog.askdirectory(title="Save the new RSD files in folder")
        if not d:
            return
        codec, template, rate, srcp, desc = opts
        force = force_var.get()
        paths = list(f)

        def job(lg):
            return do_encode(paths, d, make_opts(codec, template, rate, srcp, desc, force), lg)

        def added():
            n = br.add_paths([os.path.join(d, os.path.splitext(os.path.basename(p))[0] + ".rsd") for p in paths])
            refresh_list()
            say("%d new sound(s) added to the list" % n)

        start_job("Encode %d WAV file(s) -> RSD (%s)" % (len(paths), codec), job, added)

    def replace():
        idx = rsd_paths(sel_indices())
        if len(idx) != 1:
            messagebox.showinfo("Replace", "Select exactly one .rsd in the list - the sound you want to replace.")
            return
        orig = idx[0]
        it = br.items[sel_indices()[0]]
        wav = filedialog.askopenfilename(title="Choose your WAV (it replaces '%s')" % it["name"],
                                         filetypes=[("WAV", "*.wav"), ("All files", "*.*")])
        if not wav:
            return
        out = filedialog.asksaveasfilename(
            title="Save the new RSD as (same name as the original = overwrite)",
            defaultextension=".rsd", initialdir=os.path.dirname(orig),
            initialfile=os.path.splitext(it["name"])[0] + "_new.rsd", filetypes=[("RSD", "*.rsd")])
        if not out:
            return
        codec = it["codec"] if it["codec"] in CODECS else codec_var.get()

        def added():
            n = br.add_paths([out])
            refresh_list()
            say("New file saved: %s" % out)

        start_job("Replace %s with %s (%s)" % (it["name"], os.path.basename(wav), codec),
                  lambda lg: do_replace(orig, wav, out, codec, lg), added)

    def pick_template():
        f = filedialog.askopenfilename(title="Original .rsd whose 0x800-byte header should be reused",
                                       filetypes=[("RSD", "*.rsd"), ("All files", "*.*")])
        if f:
            tpl_var.set(f)
            say("Header template: " + f)

    def clear_template():
        tpl_var.set("")

    def export_sel():
        idx = sel_indices()
        if not idx:
            return
        i = idx[0]
        f = filedialog.asksaveasfilename(title="Save as WAV", defaultextension=".wav",
                                         initialfile=os.path.splitext(br.items[i]["name"])[0] + ".wav",
                                         filetypes=[("WAV", "*.wav")])
        if f:
            audio, _ = br.audio(i)
            with open(f, "wb") as fh:
                fh.write(R.wav_bytes(audio))
            say("Saved " + f)

    # ---- build the buttons ---------------------------------------------------
    def button(parent, text, cmd, convert=False, **pack):
        b = tk.Button(parent, text=text, command=cmd)
        b.pack(side="left", padx=2, **pack)
        if convert:
            convert_buttons.append(b)
        return b

    button(bar, "Add files...", add_files)
    button(bar, "Add folder...", add_folder)
    button(bar, "Remove", remove_sel)
    button(bar, "Clear", clear_all)
    tk.Label(bar, text="   ").pack(side="left")
    button(bar, "Play / Pause", toggle_play)
    button(bar, "Stop", stop)
    tk.Checkbutton(bar, text="Loop", variable=loopvar, command=on_loop).pack(side="left", padx=6)
    tk.Label(bar, text=CREDIT, fg="#b00020", font=("Segoe UI", 10, "bold")).pack(side="right")

    tk.Label(row1, text="RSD -> WAV:").pack(side="left")
    button(row1, "Decode selected...", lambda: decode(False), True)
    button(row1, "Decode all...", lambda: decode(True), True)
    button(row1, "Save selected as one WAV...", export_sel)
    tk.Checkbutton(row1, text="Overwrite existing files", variable=force_var).pack(side="left", padx=10)

    tk.Label(row2, text="WAV -> RSD:").pack(side="left")
    tk.Label(row2, text="Codec").pack(side="left", padx=(6, 2))
    ttk.Combobox(row2, textvariable=codec_var, values=CODECS, width=6, state="readonly").pack(side="left")
    tk.Label(row2, text="Sample rate").pack(side="left", padx=(8, 2))
    tk.Entry(row2, textvariable=rate_var, width=8).pack(side="left")
    tk.Label(row2, text="(empty = WAV's rate)").pack(side="left", padx=(2, 8))
    button(row2, "Encode WAV files...", encode, True)
    button(row2, "Replace selected RSD with a WAV...", replace, True)

    tk.Label(row3, text="Header:").pack(side="left")
    tk.Checkbutton(row3, text="use the selected RSD's header", variable=use_sel_header).pack(side="left")
    button(row3, "or pick a template RSD...", pick_template)
    button(row3, "x", clear_template)
    tk.Label(row3, textvariable=tpl_var, fg="#555", width=22, anchor="w").pack(side="left")
    tk.Label(row3, text="Source path").pack(side="left", padx=(6, 2))
    tk.Entry(row3, textvariable=src_var, width=16).pack(side="left")
    tk.Label(row3, text="Description").pack(side="left", padx=(6, 2))
    tk.Entry(row3, textvariable=desc_var, width=16).pack(side="left")

    tree.bind("<<TreeviewSelect>>", on_select)
    canvas.bind("<Button-1>", seek_click)
    canvas.bind("<Configure>", lambda e: redraw())
    root.bind("<space>", key(toggle_play))
    root.bind("<Escape>", key(stop))
    root.bind("<Left>", key(seek_rel, -1.0))
    root.bind("<Right>", key(seek_rel, 1.0))

    log(CREDIT)
    log("Add .rsd / .wav files, pick one to see and hear it, then use the Convert buttons.")
    if args:
        br.add_paths(R.expand(list(args), True))
        refresh_list(0 if br.items else None)
    poll()
    root.mainloop()
    br.player.close()
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
