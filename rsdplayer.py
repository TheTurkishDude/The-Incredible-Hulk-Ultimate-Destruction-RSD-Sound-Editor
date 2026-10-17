#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rsdplayer.py - sound viewer / listener for rsdtool.py
=====================================================
    The Tool Is Made By TheTurkishDude

Used through rsdtool.py:

    python rsdtool.py view  sound.rsd [more.rsd | folder] [-w 120] [-b] [-s 1.5] [-p wave.png]
    python rsdtool.py play  sound.rsd [more.rsd | folder] [-s 1.5] [-l]
    python rsdtool.py gui   [files or folders]

Playback needs no extra Python packages:
  * Windows : winsound (built in)
  * macOS   : afplay
  * Linux   : paplay / aplay / ffplay / play (whichever is installed)
  * anything else: set the environment variable RSDTOOL_PLAYER to a command
    line containing {file}, e.g.   set RSDTOOL_PLAYER=ffplay -nodisp -autoexit {file}
"""
import atexit
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import rsdtool as R

CREDIT = R.CREDIT


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def fmt_time(sec):
    if sec != sec or sec == math.inf:
        return "--:--.-"
    sec = max(0.0, sec)
    return "%02d:%04.1f" % (int(sec // 60), sec % 60)


def db(v):
    return "-inf" if v <= 0 else "%.1f" % (20 * math.log10(v / 32768.0))


def duration_of(audio):
    return audio.frames / audio.rate if audio.rate else 0.0


def channel_stats(samples):
    """peak, rms, dc offset, clipped count, leading / trailing silent frames."""
    n = len(samples)
    if n == 0:
        return {"peak": 0, "rms": 0.0, "dc": 0.0, "clipped": 0, "lead": 0, "trail": 0}
    peak = max(max(samples), -min(samples))
    rms = math.sqrt(sum(x * x for x in samples) / n)
    dc = sum(samples) / n
    clipped = sum(1 for x in samples if x >= 32767 or x <= -32768)
    lead = 0
    while lead < n and samples[lead] == 0:
        lead += 1
    trail = 0
    while trail < n - lead and samples[n - 1 - trail] == 0:
        trail += 1
    return {"peak": peak, "rms": rms, "dc": dc, "clipped": clipped, "lead": lead, "trail": trail}


def peaks(samples, width):
    """[(min, max)] for `width` equal slices of `samples` (fast: uses C min/max on slices)."""
    n = len(samples)
    out = []
    if n == 0 or width <= 0:
        return [(0, 0)] * max(width, 0)
    for x in range(width):
        a = n * x // width
        b = max(a + 1, n * (x + 1) // width)
        seg = samples[a:b]
        out.append((min(seg), max(seg)) if len(seg) else (0, 0))
    return out


def _can_encode(ch):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        ch.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def wave_rows(samples, width, height=9, solid="#", full_scale=32768.0):
    """Console waveform for one channel -> list of strings (height rows)."""
    if height % 2 == 0:
        height += 1
    half = height // 2

    def row_of(v):
        r = half - int(round(v / float(full_scale) * half))
        return min(max(r, 0), height - 1)

    cols = [(row_of(hi), row_of(lo)) for lo, hi in peaks(samples, width)]
    rows = []
    for r in range(height):
        line = []
        for top, bot in cols:
            if top <= r <= bot:
                line.append(solid)
            else:
                line.append("-" if r == half else " ")
        rows.append("".join(line))
    return rows


def time_ruler(width, duration, start=0.0):
    """One line with time marks at 0, 25, 50, 75 and 100 percent."""
    line = [" "] * width
    for k in range(5):
        pos = min(width - 1, width * k // 4)
        label = fmt_time(start + duration * k / 4.0)
        if k == 4:
            pos = max(0, width - len(label))
        elif k > 0:
            pos = max(0, min(pos - len(label) // 2, width - len(label)))
        for i, ch in enumerate(label):
            if pos + i < width:
                line[pos + i] = ch
    return "".join(line)


def save_png(audio, path, start=0.0, width=1200, lane=170):
    """Waveform PNG (needs Pillow)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise R.RSDError("Pillow is required for -p (pip install pillow)")
    first = int(start * audio.rate)
    chans = [s[first:] for s in audio.samples]
    n = len(chans[0]) if chans else 0
    dur = n / audio.rate if audio.rate else 0.0
    pad_t, pad_b, left = 8, 26, 8
    img = Image.new("RGB", (width + 2 * left, lane * len(chans) + pad_t + pad_b), (24, 26, 32))
    d = ImageDraw.Draw(img)
    for c, smp in enumerate(chans):
        top = pad_t + c * lane
        mid = top + lane // 2
        d.rectangle((left, top + 1, left + width, top + lane - 2), outline=(48, 52, 62))
        d.line((left, mid, left + width, mid), fill=(60, 66, 80))
        amp = lane / 2 - 4
        for x, (lo, hi) in enumerate(peaks(smp, width)):
            y1 = mid - hi / 32768.0 * amp
            y2 = mid - lo / 32768.0 * amp
            if y2 - y1 < 1:
                y2 = y1 + 1
            d.line((left + x, y1, left + x, y2), fill=(96, 200, 150) if c % 2 == 0 else (110, 170, 240))
        d.text((left + 4, top + 3), "ch %d" % (c + 1), fill=(150, 156, 170))
    for k in range(11):
        x = left + width * k // 10
        d.line((x, pad_t + lane * len(chans), x, pad_t + lane * len(chans) + 4), fill=(120, 126, 140))
        if k % 2 == 0:
            label = fmt_time(start + dur * k / 10.0)
            lx = x + 2 if k < 10 else x - 6 * len(label) - 2     # keep the last label inside the image
            d.text((lx, pad_t + lane * len(chans) + 8), label, fill=(150, 156, 170))
    d.text((width + left - 235, 2), CREDIT, fill=(190, 80, 90))
    img.save(path)
    return path


# ---------------------------------------------------------------------------
# sound loading / listing (header only, so big folders open instantly)
# ---------------------------------------------------------------------------
def quick_info(path):
    """dict(name, kind, codec, channels, rate, frames, seconds) without decoding."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(R.HEADER_SIZE)
    base = os.path.basename(path)
    if head[:3] == b"RSD":
        rsd = R.parse_rsd(head)
        body = size - R.HEADER_SIZE
        ch = rsd.channels
        if rsd.codec == "RADP":
            frames = body // (20 * ch) * 32
        elif rsd.codec == "VAG":
            frames = body // (16 * ch) * 28
        elif rsd.codec in ("PCM", "PCMB"):
            frames = (body >> 1) // ch
        else:
            frames = 0
        return {"name": base, "path": path, "kind": "RSD", "codec": rsd.codec, "channels": ch,
                "rate": rsd.rate, "frames": frames,
                "seconds": frames / rsd.rate if rsd.rate else 0.0}
    audio, _ = R.load_sound(path)
    return {"name": base, "path": path, "kind": "WAV", "codec": "PCM", "channels": audio.channels,
            "rate": audio.rate, "frames": audio.frames, "seconds": duration_of(audio)}


# ---------------------------------------------------------------------------
# audio output
# ---------------------------------------------------------------------------
class NullBackend:
    name = "none"

    def start(self, path, loop, length):
        raise R.RSDError("no audio player found (set RSDTOOL_PLAYER, see README)")

    def stop(self):
        pass

    def alive(self):
        return False


class WinBackend:
    """winsound, asynchronous from a temp file (memory + async is not allowed by winsound)."""
    name = "winsound"

    def __init__(self):
        import winsound
        self.ws = winsound
        self._end = 0.0
        self._loop = False

    def start(self, path, loop, length):
        flags = self.ws.SND_FILENAME | self.ws.SND_ASYNC
        if loop:
            flags |= self.ws.SND_LOOP
        self.ws.PlaySound(path, flags)
        self._loop = loop
        self._end = time.monotonic() + length

    def stop(self):
        self._loop = False
        self._end = 0.0
        try:
            self.ws.PlaySound(None, self.ws.SND_PURGE)
        except Exception:
            pass

    def alive(self):
        return self._loop or time.monotonic() < self._end


class CmdBackend:
    """Any command line player; {file} is replaced by the temp WAV."""

    def __init__(self, argv, label):
        self.argv = argv
        self.name = label
        self.proc = None

    def start(self, path, loop, length):
        cmd = [a.replace("{file}", path) for a in self.argv]
        if not any("{file}" in a for a in self.argv):
            cmd.append(path)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
        self.proc = None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


def detect_backend():
    env = os.environ.get("RSDTOOL_PLAYER", "").strip()
    if env:
        return CmdBackend(shlex.split(env, posix=(os.name != "nt")), "RSDTOOL_PLAYER")
    if sys.platform == "win32":
        try:
            return WinBackend()
        except ImportError:
            pass
    candidates = [["afplay"]] if sys.platform == "darwin" else []
    candidates += [["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                   ["play", "-q"], ["pw-play"]]
    for argv in candidates:
        if shutil.which(argv[0]):
            return CmdBackend(argv, argv[0])
    return NullBackend()


class Player:
    """Play / pause / stop / seek / loop on top of a backend. Position is tracked by the clock."""

    def __init__(self, backend=None, loop=False):
        self.backend = backend if backend is not None else detect_backend()
        self.loop = loop
        self.audio = None
        self.state = "stopped"          # stopped | playing | paused
        self.cursor = 0.0               # seconds, where play() will resume
        self._t0 = 0.0
        self._seg = 0.0                 # start offset of the segment that is currently playing
        self._dir = None
        self._n = 0
        atexit.register(self.close)

    @property
    def available(self):
        return not isinstance(self.backend, NullBackend)

    @property
    def duration(self):
        return duration_of(self.audio) if self.audio else 0.0

    def load(self, audio):
        self.stop()
        self.audio = audio
        self.cursor = 0.0

    def _write(self, start):
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="rsdtool_")
        self._n += 1
        path = os.path.join(self._dir, "play_%d.wav" % self._n)
        with open(path, "wb") as fh:
            fh.write(R.wav_bytes(self.audio, int(start * self.audio.rate)))
        for old in range(max(1, self._n - 3)):          # keep only the last few files
            try:
                os.remove(os.path.join(self._dir, "play_%d.wav" % old))
            except OSError:
                pass
        return path

    def _launch(self, start):
        path = self._write(start)
        length = max(0.0, self.duration - start)
        self.backend.start(path, self.loop, length)
        self._seg = start
        self._t0 = time.monotonic()

    def play(self, start=None):
        if self.audio is None or self.audio.frames == 0:
            return
        if start is None:
            start = self.cursor if self.cursor < self.duration else 0.0
        start = min(max(0.0, start), max(0.0, self.duration - 0.001))
        self.backend.stop()
        self._launch(start)
        self.cursor = start
        self.state = "playing"

    @property
    def position(self):
        if self.state != "playing":
            return self.cursor
        el = time.monotonic() - self._t0
        seg = max(self.duration - self._seg, 1e-9)
        if self.loop:
            return self._seg + el % seg
        return min(self._seg + el, self.duration)

    def pause(self):
        if self.state == "playing":
            self.cursor = self.position
            self.backend.stop()
            self.state = "paused"

    def stop(self):
        self.backend.stop()
        self.state = "stopped"
        self.cursor = 0.0

    def seek(self, sec):
        if self.state == "playing":
            self.play(sec)
        else:
            self.cursor = min(max(0.0, sec), self.duration)

    def update(self):
        """Call regularly: restarts a loop (command players) and detects the end of the sound."""
        if self.state == "playing" and not self.backend.alive():
            if self.loop:
                self._launch(self._seg)
            else:
                self.state = "stopped"
                self.cursor = 0.0
        return self.state

    def close(self):
        try:
            self.backend.stop()
        except Exception:
            pass
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


# ---------------------------------------------------------------------------
# command line: view / play
# ---------------------------------------------------------------------------
def describe(path, audio, meta):
    out = []
    dur = duration_of(audio)
    tag = "%s %s" % (meta["magic"], meta["codec"]) if meta["kind"] == "RSD" else "WAV PCM"
    out.append("%s" % path)
    out.append("  format      : %s, %d ch, %d Hz, %d-bit   length %s (%d frames)" %
               (tag, audio.channels, audio.rate, meta["bits"], fmt_time(dur), audio.frames))
    if meta.get("source"):
        out.append("  source path : %s" % meta["source"])
    if meta.get("description"):
        out.append("  description : %s" % meta["description"])
    return out


def view_one(path, o, out=None):
    out = out or sys.stdout
    audio, meta = R.load_sound(path)
    for line in describe(path, audio, meta):
        out.write(line + "\n")
    if audio.frames == 0:
        out.write("  (no audio)\n")
        return
    first = min(int(o.start * audio.rate), audio.frames - 1) if o.start > 0 else 0
    start_t = first / audio.rate if audio.rate else 0.0
    width = o.width if o.width > 0 else max(40, min(shutil.get_terminal_size((100, 20)).columns - 4, 200))
    solid = "█" if (o.blocks and _can_encode("█")) else "#"
    stats = [channel_stats(audio.samples[c]) for c in range(audio.channels)]
    top = max(st["peak"] for st in stats)
    full_scale = max(top, 512)                 # display is scaled to the loudest peak
    if full_scale < 24000:
        out.write("  (waveform display scaled to the peak; levels below are real)\n")
    for c in range(audio.channels):
        smp = audio.samples[c][first:]
        st = stats[c]
        out.write("  ch %d  peak %s dBFS   rms %s dBFS   dc %+.1f   clipped %d   silence: start %s, end %s\n" %
                  (c + 1, db(st["peak"]), db(st["rms"]), st["dc"], st["clipped"],
                   fmt_time(st["lead"] / audio.rate), fmt_time(st["trail"] / audio.rate)))
        for row in wave_rows(smp, width, 9, solid, full_scale):
            out.write("  " + row.rstrip() + "\n" if row.strip() else "\n")
    out.write("  " + time_ruler(width, (audio.frames - first) / audio.rate, start_t).rstrip() + "\n")
    if o.png:
        target = o.png
        if o.png and (os.path.isdir(o.png) or o.png.endswith(("/", "\\"))):
            os.makedirs(o.png, exist_ok=True)
            target = os.path.join(o.png, os.path.splitext(os.path.basename(path))[0] + ".png")
        elif len(getattr(o, "_multi", [])) > 1:
            stem, ext = os.path.splitext(o.png)
            target = "%s_%s%s" % (stem, os.path.splitext(os.path.basename(path))[0], ext or ".png")
        save_png(audio, target, start_t)
        out.write("  waveform PNG: %s\n" % target)
    out.write("\n")


def play_one(path, o, player, out=None):
    out = out or sys.stdout
    audio, meta = R.load_sound(path)
    for line in describe(path, audio, meta)[:2]:
        out.write(line + "\n")
    if audio.frames == 0:
        out.write("  (no audio)\n\n")
        return True
    player.loop = o.loop
    player.load(audio)
    player.play(o.start if o.start > 0 else 0.0)
    dur = player.duration
    tty = sys.stdout.isatty()
    out.write("  playing via %s%s - press Ctrl+C to skip, twice quickly to quit\n" %
              (player.backend.name, " (looping)" if o.loop else ""))
    try:
        while player.update() == "playing":
            pos = player.position
            if tty:
                fill = int(30 * pos / dur) if dur else 0
                out.write("\r  [%s%s] %s / %s " % ("#" * fill, "-" * (30 - fill), fmt_time(pos), fmt_time(dur)))
                out.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        player.stop()
        now = time.monotonic()
        quick = now - getattr(player, "_last_int", 0.0) < 1.0
        player._last_int = now
        if quick:
            raise
        out.write("\n  skipped\n")
    player.stop()
    if tty:
        out.write("\r" + " " * 70 + "\r")
    out.write("\n")
    return True


def run_cli(mode, files, o):
    o._multi = files
    failed = 0
    player = None
    if mode == "p":
        player = Player()
        if not player.available:
            sys.stderr.write("ERROR: no audio player found on this system.\n"
                             "  Windows has one built in; on Linux install pulseaudio-utils/alsa-utils/ffmpeg,\n"
                             "  or set RSDTOOL_PLAYER, e.g.  RSDTOOL_PLAYER=\"ffplay -nodisp -autoexit {file}\"\n"
                             "  Meanwhile 'python rsdtool.py decode' makes WAV files you can open anywhere.\n")
            return 3
    for path in files:
        try:
            if mode == "v":
                view_one(path, o)
            else:
                try:
                    play_one(path, o, player)
                except KeyboardInterrupt:
                    sys.stdout.write("\n")
                    break
        except (R.RSDError, OSError) as e:
            sys.stderr.write("ERROR %s: %s\n" % (path, e))
            failed += 1
    if player:
        player.close()
    return 2 if failed else 0


# ---------------------------------------------------------------------------
# GUI (tkinter): list, waveform with playhead, play/pause/stop/loop/seek, export
# ---------------------------------------------------------------------------
class Browser:
    """Everything the GUI does, without any widgets (so it can be tested)."""

    def __init__(self, player=None):
        self.player = player or Player()
        self.items = []
        self.current = -1
        self._cache = {}

    def add_paths(self, paths, recurse=True):
        added = 0
        for p in R.expand(list(paths), recurse):
            if os.path.splitext(p)[1].lower() not in (".rsd", ".wav"):
                continue
            if any(it["path"] == p for it in self.items):
                continue
            try:
                self.items.append(quick_info(p))
                added += 1
            except (R.RSDError, OSError):
                self.items.append({"name": os.path.basename(p), "path": p, "kind": "?", "codec": "error",
                                   "channels": 0, "rate": 0, "frames": 0, "seconds": 0.0})
                added += 1
        return added

    def audio(self, i):
        path = self.items[i]["path"]
        if path not in self._cache:
            if len(self._cache) >= 6:
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = R.load_sound(path)
        return self._cache[path]

    def select(self, i):
        audio, meta = self.audio(i)
        self.current = i
        self.player.load(audio)
        return audio, meta

    def remove(self, indices):
        gone = {self.items[i]["path"] for i in indices if 0 <= i < len(self.items)}
        playing = self.current >= 0 and self.items[self.current]["path"] in gone
        self.items = [it for it in self.items if it["path"] not in gone]
        for p in gone:
            self._cache.pop(p, None)
        if playing:
            self.player.stop()
            self.player.audio = None
        self.current = -1

    def clear(self):
        self.remove(range(len(self.items)))

    def export_wav(self, i, folder, force=True):
        audio, _ = self.audio(i)
        stem = os.path.splitext(self.items[i]["name"])[0]
        return R.write_out(os.path.join(folder, stem + ".wav"), R.wav_bytes(audio), force)

    def export_all(self, folder):
        os.makedirs(folder, exist_ok=True)
        done = 0
        for i in range(len(self.items)):
            try:
                self.export_wav(i, folder)
                done += 1
            except (R.RSDError, OSError):
                pass
        return done


def run_gui(args, o):
    """The sound browser is part of the full RSD Tool window (rsdgui.py)."""
    import rsdgui
    return rsdgui.run(args)
