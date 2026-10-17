#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rsdtool.py - Radical Entertainment RSD <-> WAV converter, viewer and player
===========================================================================
For Radical Entertainment's Pure3D games, tested layout: The Incredible Hulk:
Ultimate Destruction (PS2).  Python port of rsdtool 1.1.0 with a sound
viewer / listener added.

    The Tool Is Made By TheTurkishDude

Command line (same commands and options as rsdtool.exe):

  python rsdtool.py decode  <file.rsd|dir|glob>... [-o out]     RSD  -> WAV
  python rsdtool.py encode  <file.wav|dir|glob>... [-o out]     WAV  -> RSD
  python rsdtool.py info    <file.rsd>...                       header details
  python rsdtool.py <file>                                      auto by extension

Viewer / listener (new):

  python rsdtool.py view    <file.rsd|file.wav|dir>...          waveform in the console
  python rsdtool.py play    <file.rsd|file.wav|dir>...          listen to the sound
  python rsdtool.py gui                                         the full window (same as no arguments)

Only the Python standard library is needed (Pillow is optional, for -p PNGs).
"""
import array
import glob as _glob
import math
import os
import struct
import subprocess
import sys
import tempfile
import time

VERSION = "1.1.0"
CREDIT = "The Tool Is Made By TheTurkishDude"
HEADER_SIZE = 0x800

# ---------------------------------------------------------------------------
# tables (extracted from rsdtool.exe)
# ---------------------------------------------------------------------------
IMA_STEP = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
            45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
            209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
            796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
            2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
            7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
            20350, 22385, 24623, 27086, 29794, 32767)
IMA_INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)
VAG_FILTER = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


def _build_ima_tables():
    """DIFF[index*16+code] and NEXT[index*16+code] for the IMA ADPCM step."""
    diff, nxt = [], []
    for index in range(89):
        step = IMA_STEP[index]
        for code in range(16):
            d = step >> 3
            if code & 1:
                d += step >> 2
            if code & 2:
                d += step >> 1
            if code & 4:
                d += step
            diff.append(-d if code & 8 else d)
            nxt.append(min(max(index + IMA_INDEX[code & 7], 0), 88))
    return diff, nxt


IMA_DIFF, IMA_NEXT = _build_ima_tables()


class RSDError(Exception):
    pass


class Audio:
    """rate, channels and per-channel signed 16-bit samples (array('h'))."""

    def __init__(self, rate, samples):
        self.rate = rate
        self.samples = samples

    @property
    def channels(self):
        return len(self.samples)

    @property
    def frames(self):
        return len(self.samples[0]) if self.samples else 0


def clamp16(v):
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def _trunc_div64(t):
    """Go's integer t/64 (rounds toward zero)."""
    return t >> 6 if t >= 0 else -((-t) >> 6)


# ---------------------------------------------------------------------------
# RSD header
# ---------------------------------------------------------------------------
class RSD:
    def __init__(self, codec, channels, bits, rate, header, data):
        self.codec, self.channels, self.bits, self.rate = codec, channels, bits, rate
        self.header, self.data = header, data

    @property
    def magic(self):
        return self.header[0:4].decode("latin-1")

    def source_path(self):
        s = _cstr(self.header[0x14:0xa4])
        return s[4:] if s.startswith("****") else s

    def description(self):
        return _cstr(self.header[0xa4:0xa4 + 0x100])


def _cstr(b):
    i = bytes(b).find(b"\0")
    return bytes(b if i < 0 else b[:i]).decode("latin-1")


def parse_rsd(buf):
    if len(buf) < HEADER_SIZE:
        raise RSDError("file too small for an RSD header")
    if buf[0:3] != b"RSD":
        raise RSDError("not an RSD file (missing RSD magic)")
    codec = buf[4:8].decode("latin-1").rstrip(" \0")
    channels, bits, rate = struct.unpack_from("<III", buf, 8)
    if not 1 <= channels <= 16:
        raise RSDError("implausible channel count %d" % channels)
    return RSD(codec, channels, bits, rate, bytes(buf[:HEADER_SIZE]), bytes(buf[HEADER_SIZE:]))


def build_header(codec, channels, bits, rate, template, src_path, desc):
    """New 0x800-byte header (or the template's header with the new fields)."""
    if template is not None and len(template) >= HEADER_SIZE:
        h = bytearray(template[:HEADER_SIZE])
    else:
        h = bytearray(HEADER_SIZE)
        for i in range(0x1a4, HEADER_SIZE):
            h[i] = 0x2d
        p = ("****" + src_path).encode("latin-1", "replace")[:0x8f]
        h[0x14:0x14 + len(p)] = p
        d = desc.encode("latin-1", "replace")[:0xff]
        h[0xa4:0xa4 + len(d)] = d
    h[0:4] = b"RSD6"
    h[4:8] = (codec + "    ").encode("latin-1")[:4]
    struct.pack_into("<III", h, 8, channels, bits, rate)
    return bytes(h)


# ---------------------------------------------------------------------------
# codecs
# ---------------------------------------------------------------------------
def decode_radp(rsd):
    ch = rsd.channels
    fsz = 20 * ch
    data = rsd.data
    nblk = len(data) // fsz
    if nblk == 0:
        raise RSDError("no audio blocks")
    outs = [array.array("h") for _ in range(ch)]
    diff, nxt = IMA_DIFF, IMA_NEXT
    unpack = struct.Struct("<hh").unpack_from
    for f in range(nblk):
        base = f * fsz
        for c in range(ch):
            o = base + c * 20
            idx, pred = unpack(data, o)
            idx = 0 if idx < 0 else (88 if idx > 88 else idx)
            ap = outs[c].append
            for byte in data[o + 4:o + 20]:
                for code in (byte & 15, byte >> 4):
                    k = idx * 16 + code
                    pred += diff[k]
                    if pred > 32767:
                        pred = 32767
                    elif pred < -32768:
                        pred = -32768
                    idx = nxt[k]
                    ap(pred)
    return Audio(rsd.rate, outs)


def encode_radp(audio):
    ch, n = audio.channels, audio.frames
    nblk = (n + 31) // 32
    out = bytearray()
    idx = [0] * ch
    pred = [0] * ch
    step_t, diff, nxt = IMA_STEP, IMA_DIFF, IMA_NEXT
    for b in range(nblk):
        for c in range(ch):
            blk = bytearray(20)
            struct.pack_into("<hh", blk, 0, idx[c], pred[c])
            i_, p_ = idx[c], pred[c]
            src = audio.samples[c]
            for k in range(32):
                pos = b * 32 + k
                target = src[pos] if pos < n else 0
                d = target - p_
                code = 0
                if d < 0:
                    d = -d
                    code = 8
                step = step_t[i_]
                if step <= d:
                    code |= 4
                    d -= step
                half = step >> 1
                if d >= half:
                    code |= 2
                    d -= half
                if d >= step >> 2:
                    code |= 1
                kk = i_ * 16 + code
                p_ = clamp16(p_ + diff[kk])
                i_ = nxt[kk]
                if k & 1:
                    blk[4 + k // 2] |= code << 4
                else:
                    blk[4 + k // 2] = code
            idx[c], pred[c] = i_, p_
            out += blk
    return bytes(out)


def decode_vag(rsd):
    ch = rsd.channels
    data = rsd.data
    nblk = len(data) // (16 * ch)
    if nblk == 0:
        raise RSDError("no audio blocks")
    outs = [array.array("h") for _ in range(ch)]
    h1 = [0] * ch
    h2 = [0] * ch
    for f in range(nblk):
        for c in range(ch):
            o = (f * ch + c) * 16
            b0 = data[o]
            fi = b0 >> 4
            if fi > 4:
                fi = 0
            shift = b0 & 15
            if shift > 12:
                shift = 9
            c0, c1 = VAG_FILTER[fi]
            s1, s2 = h1[c], h2[c]
            ap = outs[c].append
            for k in range(28):
                byte = data[o + 2 + (k >> 1)]
                nib = (byte >> 4) if k & 1 else (byte & 15)
                v = (nib << 12) & 0xffff
                if v & 0x8000:
                    v -= 0x10000
                v >>= shift
                t = c0 * s1 + c1 * s2
                s = clamp16(v + (t >> 6 if t >= 0 else -((-t) >> 6)))
                s2, s1 = s1, s
                ap(s)
            h1[c], h2[c] = s1, s2
    return Audio(rsd.rate, outs)


def encode_vag(audio):
    ch, n = audio.channels, audio.frames
    silent_first = n >= 28 and all(all(s[i] == 0 for i in range(28)) for s in audio.samples)
    nblk = (n + 27) // 28
    out = bytearray()
    if not silent_first:
        out += bytes(16 * ch)
    s1 = [0] * ch
    s2 = [0] * ch
    big = (1 << 63) - 1
    for b in range(nblk):
        for c in range(ch):
            src = audio.samples[c]
            tgt = [src[b * 28 + i] if b * 28 + i < n else 0 for i in range(28)]
            best_err, best = big, None
            for p in range(5):
                c0, c1 = VAG_FILTER[p]
                for shift in range(12, -1, -1):
                    scale = 4096 >> shift
                    h1, h2 = s1[c], s2[c]
                    err = 0
                    blk = bytearray(16)
                    blk[0] = (p << 4) | shift
                    blk[1] = 2
                    for i in range(28):
                        t = tgt[i]
                        q = c0 * h1 + c1 * h2
                        pr = q >> 6 if q >= 0 else -((-q) >> 6)
                        code = math.floor((t - pr) / scale + 0.5)
                        code = 7 if code > 7 else (-8 if code < -8 else code)
                        rec = clamp16(pr + code * scale)
                        e = t - rec
                        err += e * e
                        if i & 1:
                            blk[2 + (i >> 1)] |= (code & 15) << 4
                        else:
                            blk[2 + (i >> 1)] = code & 15
                        h2, h1 = h1, rec
                    if best_err > err:
                        best_err, best = err, (blk, h1, h2)
            blk, s1[c], s2[c] = best
            out += blk
    return bytes(out)


def decode_pcm(rsd, big_endian):
    if rsd.bits != 16:
        raise RSDError("PCM with %d bits not supported" % rsd.bits)
    ch = rsd.channels
    frames = (len(rsd.data) >> 1) // ch
    raw = array.array("h")
    raw.frombytes(rsd.data[:frames * ch * 2])
    if (sys.byteorder == "big") != big_endian:
        raw.byteswap()
    return Audio(rsd.rate, [raw[c::ch] for c in range(ch)])


def encode_pcm(audio, big_endian):
    raw = interleave(audio)
    if (sys.byteorder == "big") != big_endian:
        raw.byteswap()
    return raw.tobytes()


def interleave(audio):
    ch, n = audio.channels, audio.frames
    out = array.array("h", bytes(2 * ch * n))
    for c in range(ch):
        out[c::ch] = audio.samples[c]
    return out                  # native byte order; callers swap when they need to


def decode_rsd(rsd):
    codec = rsd.codec
    if codec == "PCM":
        return decode_pcm(rsd, False)
    if codec == "VAG":
        return decode_vag(rsd)
    if codec == "PCMB":
        return decode_pcm(rsd, True)
    if codec == "RADP":
        return decode_radp(rsd)
    raise RSDError("unsupported RSD codec %r (supported: RADP, VAG, PCM, PCMB)" % codec)


# ---------------------------------------------------------------------------
# WAV
# ---------------------------------------------------------------------------
def _go_round(x):
    """math.Round: half away from zero."""
    if x >= 0:
        f = math.floor(x)
        return f + 1 if x - f >= 0.5 else f
    f = math.floor(-x)
    return -(f + 1 if -x - f >= 0.5 else f)


def _float16(x):
    if x != x:
        return 0
    x *= 32768.0
    if x in (math.inf, -math.inf) or abs(x) >= 9.223372036854775808e18:
        return -32768                      # cvttsd2si "indefinite" -> clamped
    return clamp16(int(_go_round(x)))


def read_wav(buf):
    if len(buf) < 12 or buf[0:4] != b"RIFF" or buf[8:12] != b"WAVE":
        raise RSDError("not a RIFF/WAVE file")
    pos = 12
    have_fmt = False
    tag = chans = rate = bits = 0
    data = None
    while pos + 8 <= len(buf):
        cid = buf[pos:pos + 4]
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        end = min(len(buf), pos + 8 + size)
        if cid == b"data":
            data = buf[pos + 8:end]
        elif cid == b"fmt ":
            if end - pos - 8 < 16:
                raise RSDError("bad fmt chunk")
            tag, chans, rate = struct.unpack_from("<HHI", buf, pos + 8)
            bits = struct.unpack_from("<H", buf, pos + 22)[0]
            if tag == 0xFFFE and end - pos - 8 >= 0x1a:
                tag = struct.unpack_from("<H", buf, pos + 32)[0]
            have_fmt = True
        pos = end + (size & 1)
    if not have_fmt or data is None:
        raise RSDError("WAV missing fmt or data chunk")
    bps = bits >> 3
    if bps == 0:
        raise RSDError("bad bits per sample")
    if chans <= 0:
        raise RSDError("WAV has zero channels")
    frames = len(data) // (chans * bps)
    total = frames * chans
    if tag == 1 and bits == 8:
        flat = array.array("h", [(b - 128) << 8 for b in data[:total]])
    elif tag == 1 and bits == 16:
        flat = array.array("h")
        flat.frombytes(bytes(data[:total * 2]))
        if sys.byteorder == "big":
            flat.byteswap()
    elif tag == 1 and bits == 24:
        flat = array.array("h", [struct.unpack_from("<i", b"\0" + bytes(data[i * 3:i * 3 + 3]))[0] >> 16
                                 for i in range(total)])
    elif tag == 1 and bits == 32:
        v = struct.unpack("<%di" % total, bytes(data[:total * 4]))
        flat = array.array("h", [x >> 16 for x in v])
    elif tag == 3 and bits == 32:
        v = struct.unpack("<%df" % total, bytes(data[:total * 4]))
        flat = array.array("h", [_float16(x) for x in v])
    elif tag == 3 and bits == 64:
        v = struct.unpack("<%dd" % total, bytes(data[:total * 8]))
        flat = array.array("h", [_float16(x) for x in v])
    else:
        raise RSDError("unsupported WAV format tag=%d bits=%d" % (tag, bits))
    return Audio(rate, [flat[c::chans] for c in range(chans)])


def wav_bytes(audio, start=0):
    """16-bit PCM WAV file (44-byte header) for `audio`, optionally from frame `start`."""
    ch = audio.channels
    if start:
        audio = Audio(audio.rate, [s[start:] for s in audio.samples])
    n = audio.frames
    pcm = interleave(audio)
    if sys.byteorder == "big":
        pcm.byteswap()
    body = pcm.tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, audio.rate, audio.rate * ch * 2, ch * 2, 16) +
            b"data" + struct.pack("<I", len(body)) + body)


# ---------------------------------------------------------------------------
# loading any sound file (used by view / play / gui)
# ---------------------------------------------------------------------------
def load_sound(path):
    """-> (Audio, description-dict).  Accepts .rsd and .wav files."""
    with open(path, "rb") as fh:
        buf = fh.read()
    if buf[:3] == b"RSD":
        rsd = parse_rsd(buf)
        return decode_rsd(rsd), {"kind": "RSD", "codec": rsd.codec, "magic": rsd.magic,
                                 "bits": rsd.bits, "source": rsd.source_path(),
                                 "description": rsd.description()}
    audio = read_wav(buf)
    return audio, {"kind": "WAV", "codec": "PCM", "magic": "RIFF", "bits": 16,
                   "source": "", "description": ""}


# ---------------------------------------------------------------------------
# command line (mirrors rsdtool.exe)
# ---------------------------------------------------------------------------
HELP_TEXT = """rsdtool 1.1.0 - Radical Entertainment RSD <-> WAV converter
The Tool Is Made By TheTurkishDude

USAGE
  rsdtool decode  <file.rsd|dir|glob>... [-o out]     RSD  -> WAV
  rsdtool encode  <file.wav|dir|glob>... [-o out]     WAV  -> RSD (RSD6RADP by default)
  rsdtool info    <file.rsd>...                       show header details
  rsdtool <file>                                      auto: .rsd -> wav, .wav -> rsd
  (python rsdtool.py ... works the same as rsdtool.exe)

  rsdtool view    <file.rsd|file.wav|dir>...          waveform + levels in the console
  rsdtool play    <file.rsd|file.wav|dir>...          listen to the sounds
  rsdtool gui                                         sound viewer / listener window

OPTIONS
  -o <path>        output file (single input) or output directory (many inputs)
  -c <codec>       encode codec: radp (default), vag, pcm, pcmb
  -t <orig.rsd>    reuse the 0x800-byte header (names, description) of an original RSD
  -r <rate>        override sample rate stored in the RSD header (no resampling)
  -n <path>        source path string stored in a new RSD header
  -d <text>        description string stored in a new RSD header
  -R               recurse into directories
  -f               overwrite existing outputs (default: write name_new.ext)
  -q               quiet

  view / play only:
  -s <seconds>     start position            -l   loop the sound (play)
  -w <columns>     waveform width (view)     -b   use block characters (view)
  -p <file.png>    save the waveform as a PNG (view, needs Pillow)

NOTES
  * Any WAV is accepted: 8/16/24/32-bit PCM, 32/64-bit float, any channel count
    and sample rate. Audio is converted to 16-bit.
  * RADP output is padded with silence to a multiple of 32 samples (the format
    stores no exact length).
  * Decoding supports RADP, VAG, PCM and PCMB RSD files.
  * VAG output starts with one silent block (like the originals) unless the audio
    already starts with silence; blocks use flag 2 as in the original files.
"""


class Opts:
    def __init__(self):
        self.out = ""
        self.codec = "radp"
        self.template = ""
        self.desc = ""
        self.srcpath = ""
        self.rate = 0
        self.recurse = False
        self.force = False
        self.quiet = False
        # viewer / player extras
        self.start = 0.0
        self.loop = False
        self.width = 0
        self.blocks = False
        self.png = ""


class Fatal(Exception):
    pass


def fatal(msg):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(1)


def _split_ext(path):
    """(stem, ext) like Go: ext starts at the last '.' after the last separator."""
    for i in range(len(path) - 1, -1, -1):
        c = path[i]
        if c in "/\\":
            break
        if c == ".":
            return path[:i], path[i:]
    return path, ""


def out_path(inp, ext, multi, o):
    """main.outPath"""
    stem, _ = _split_ext(inp)
    cand = stem + ext
    if not o.out:
        return cand
    out = o.out
    if multi or out.endswith("/") or out.endswith("\\") or os.path.isdir(out):
        os.makedirs(out, mode=0o755, exist_ok=True)
        return os.path.join(out, os.path.basename(cand))
    return out


def write_out(path, data, force):
    """main.write: an existing file is kept (name_new.ext) unless -f was given."""
    if os.path.exists(path) and not force:
        stem, ext = _split_ext(path)
        path = stem + "_new" + ext
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def expand(args, recurse, modes=(".rsd", ".wav")):
    files = []
    for a in args:
        if os.path.isdir(a):
            for root, dirs, names in os.walk(a):
                dirs.sort()
                for nme in sorted(names):
                    if os.path.splitext(nme)[1].lower() in modes:
                        files.append(os.path.join(root, nme))
                if not recurse:
                    break
        elif any(ch in a for ch in "*?["):
            files.extend(sorted(_glob.glob(a)))
        else:
            files.append(a)
    return files


def process(mode, path, multi, o, out=None):
    out = out or sys.stdout
    if mode == "":
        mode = "d" if os.path.splitext(path)[1].lower() == ".rsd" else "e"
    try:
        with open(path, "rb") as fh:
            buf = fh.read()
    except OSError as e:
        raise RSDError("open %s: %s" % (path, e.strerror))
    if mode == "i":
        rsd = parse_rsd(buf)
        out.write("%s\n  magic/codec : %s / %s\n  channels    : %d\n  bits        : %d\n"
                  "  sample rate : %d Hz\n  source path : %s\n  description : %s\n" %
                  (path, rsd.magic, rsd.codec, rsd.channels, rsd.bits, rsd.rate,
                   rsd.source_path(), rsd.description()))
        if rsd.codec == "VAG":
            blocks = len(rsd.data) // (16 * rsd.channels)
            samples = blocks * 28
            out.write("  blocks      : %d (%d samples/ch, %.3f s)\n" %
                      (blocks, samples, samples / rsd.rate if rsd.rate else math.inf))
        if rsd.codec == "RADP":
            blocks = len(rsd.data) // (20 * rsd.channels)
            samples = blocks * 32
            out.write("  blocks      : %d (%d samples/ch, %.3f s)\n" %
                      (blocks, samples, samples / rsd.rate if rsd.rate else math.inf))
        return
    if mode == "d":
        rsd = parse_rsd(buf)
        audio = decode_rsd(rsd)
        target = out_path(path, ".wav", multi, o)
        final = write_out(target, wav_bytes(audio), o.force)
        if not o.quiet:
            out.write("%s -> %s  [%s, %d ch, %d Hz, %d frames]\n" %
                      (path, final, rsd.codec, audio.channels, audio.rate, audio.frames))
        return
    if mode == "e":
        audio = read_wav(buf)
        template = None
        if o.template:
            try:
                with open(o.template, "rb") as fh:
                    template = fh.read()
            except OSError as e:
                raise RSDError("open %s: %s" % (o.template, e.strerror))
        rate = o.rate if o.rate > 0 else audio.rate
        name = o.codec
        if name == "pcm":
            payload, cname = encode_pcm(audio, False), "PCM"
        elif name == "vag":
            payload, cname = encode_vag(audio), "VAG"
        elif name == "radp":
            payload, cname = encode_radp(audio), "RADP"
        elif name == "pcmb":
            payload, cname = encode_pcm(audio, True), "PCMB"
        else:
            raise RSDError("unknown codec %r" % name)
        header = build_header(cname, audio.channels, 16, rate, template,
                              o.srcpath or os.path.basename(path), o.desc or "no description")
        target = out_path(path, ".rsd", multi, o)
        final = write_out(target, header + payload, o.force)
        if not o.quiet:
            out.write("%s -> %s  [%s, %d ch, %d Hz, %d frames]\n" %
                      (path, final, cname, audio.channels, rate, audio.frames))
        return


_VALUE_OPTS = {"-c": "codec", "-d": "desc", "-n": "srcpath", "-o": "out", "-r": "rate",
               "-t": "template"}
_VIEW_VALUE_OPTS = {"-s": "start", "-w": "width", "-p": "png"}


def parse_args(rest, mode):
    o = Opts()
    files = []
    i = 0
    while i < len(rest):
        a = rest[i]
        i += 1
        if len(a) == 2 and a[0] == "-":
            if a == "-R":
                o.recurse = True
                continue
            if a == "-f":
                o.force = True
                continue
            if a == "-q":
                o.quiet = True
                continue
            if mode in ("v", "p", "g"):
                if a == "-l":
                    o.loop = True
                    continue
                if a == "-b":
                    o.blocks = True
                    continue
            table = dict(_VALUE_OPTS)
            if mode in ("v", "p", "g"):
                table.update(_VIEW_VALUE_OPTS)
            if a in table:
                if i >= len(rest):
                    fatal("option %s needs a value" % a)
                val = rest[i]
                i += 1
                attr = table[a]
                if attr == "codec":
                    val = val.lower()
                elif attr in ("rate", "width"):
                    try:
                        val = int(val)
                    except ValueError:
                        fatal("option %s expects an integer, got %r" % (a, val))
                elif attr == "start":
                    try:
                        val = float(val)
                    except ValueError:
                        fatal("option -s expects seconds, got %r" % val)
                setattr(o, attr, val)
                continue
        files.append(a)
    return o, files


def _load_module(name):
    """Import a sibling module from this script's folder (works from any working directory)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    sys.modules.setdefault("rsdtool", sys.modules[__name__])
    return __import__(name)


def _player_module():
    return _load_module("rsdplayer")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if not argv:                                # double-clicked / no arguments: open the window
        return _load_module("rsdgui").run([])
    if argv[0] in ("-h", "--help", "help") or argv[0].lower() == "-h":
        sys.stdout.write(HELP_TEXT)
        return 0
    cmd = argv[0].lower()
    names = {"d": "d", "decode": "d", "e": "e", "encode": "e", "i": "i", "info": "i",
             "v": "v", "view": "v", "p": "p", "play": "p", "g": "g", "gui": "g"}
    if cmd in names:
        mode, rest = names[cmd], argv[1:]
    else:
        mode, rest = "", argv
    o, args = parse_args(rest, mode)
    if mode == "g":
        return _load_module("rsdgui").run(args)
    if not o.quiet:
        sys.stdout.write("rsdtool %s - %s\n\n" % (VERSION, CREDIT))
    if mode in ("v", "p"):
        files = expand(args, o.recurse)
        if not files:
            fatal("no input files found")
        return _player_module().run_cli(mode, files, o)
    folder_types = {"d": (".rsd",), "e": (".wav",), "i": (".rsd",)}.get(mode, (".rsd", ".wav"))
    files = expand(args, o.recurse, folder_types)
    if not files:
        fatal("no input files found")
    failed = 0
    for path in files:
        try:
            process(mode, path, len(files) > 1, o)
        except (RSDError, OSError, struct.error) as e:
            sys.stderr.write("ERROR %s: %s\n" % (path, e))
            failed += 1
    if failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
