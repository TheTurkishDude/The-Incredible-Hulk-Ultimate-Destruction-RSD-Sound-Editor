# RSD Tool

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20tested-blue.svg)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)

**A windowed Python tool for Radical Entertainment `.rsd` sound files** from
*The Incredible Hulk: Ultimate Destruction* (PS2). Decode RSD to WAV, encode WAV back to RSD,
replace a game sound with your own, and look at and listen to the sounds - by clicking, no
Command Prompt needed. The command line is still there if you want it.

> **The Tool Is Made By TheTurkishDude**

## Features

- **Decode** `RADP`, `VAG`, `PCM` and `PCMB` RSD files to standard WAV
- **Encode** any WAV (8/16/24/32-bit PCM, 32/64-bit float, any channels and rate) to RSD with
  your choice of codec
- **Replace a game sound**: pick an original `.rsd` and your WAV; the new file keeps the original's
  header text and codec
- **Reuse headers** from an original RSD (`-t` / "use the selected RSD's header") or write a new one
  with your own source path and description
- **Sound browser and player**: waveform with a moving playhead, click to seek, play / pause /
  stop / loop; folders open instantly because only headers are read
- **Level report** per channel: peak, RMS, DC offset, clipped samples, leading/trailing silence
- Background conversion, so the window stays responsive on long sounds
- Pure standard library: **no packages to install** (Pillow is optional, only for PNG waveform export)

## Requirements

- Python 3.8 or newer is intended; the test suite runs on 3.11, 3.12 and 3.13 (get Python from
  [python.org](https://www.python.org/downloads/); the normal installer includes `tkinter`, which the
  window uses)
- Windows, macOS or Linux

## Quick start

1. Download the repository (**Code -> Download ZIP**) or clone it.
2. **Double-click `RSD_Tool.pyw`.** If nothing happens, double-click `Start_RSD_Tool.bat` instead.
   (Or run `python rsdtool.py` / `python rsdgui.py`.)
3. Add `.rsd` / `.wav` files or a folder, pick a sound to see and hear it, then use the
   **Convert** buttons. You can also drag files or a folder onto `RSD_Tool.pyw`.

If the window cannot start, the reason is written to `rsdtool_error.log` next to the script.

## The window

```
 [Add files] [Add folder] [Remove] [Clear]   [Play/Pause] [Stop] [x]Loop      TheTurkishDude
 +----------------------------+------------------------------------------------------+
 | Sound        codec ch rate |  format, source path, description                   |
 | boom.rsd     RADP  2  22050|  +------------------------------------------------+  |
 | voice.rsd    VAG   1  11025|  |  waveform  (click to seek, yellow playhead)    |  |
 | music.rsd    RADP  2  32000|  +------------------------------------------------+  |
 +----------------------------+  00:00.4 / 00:01.6   [playing]                       |
 | Convert                                                                            |
 |  RSD -> WAV  [Decode selected] [Decode all] [Save selected as one WAV] [] Overwrite|
 |  WAV -> RSD  Codec [RADP] Sample rate [    ] [Encode WAV files] [Replace selected] |
 |  Header      [] use selected RSD's header  [pick template]  Source path / Description
 | Log                                                                                |
 +------------------------------------------------------------------------------------+
```

*(Layout sketch, not a screenshot.)*

| Action | What it does |
|---|---|
| **Decode selected / all** | Writes WAV files to a folder you choose. Existing files are kept and the new one is saved as `name_new.wav`, unless **Overwrite existing files** is ticked. |
| **Save selected as one WAV** | One file, any name. |
| **Encode WAV files** | Choose WAVs, then an output folder. Uses the codec, sample rate, header options and source path / description from the Convert box. Results are added to the list. |
| **Replace selected RSD with a WAV** | Select **one** `.rsd`, choose your WAV, choose where to save. Keeps the original's header text and codec. Save over the original name to overwrite it (**keep a backup**). |
| **Sample rate** | Empty = the WAV's own rate. No resampling is done; the number is only written to the header. |
| Keys | `Space` play/pause, `Esc` stop, `Left` / `Right` seek 1 s |

Tip: for a sound that should drop into the game, match the original's **codec**, **channel count**
and **sample rate**.

## Command line

The same engine is available without the window:

```
python rsdtool.py decode  <file.rsd | folder | glob>... [-o out]     RSD -> WAV
python rsdtool.py encode  <file.wav | folder | glob>... [-o out]     WAV -> RSD
python rsdtool.py info    <file.rsd>...                              header details
python rsdtool.py <file>                                             .rsd -> wav, .wav -> rsd
python rsdtool.py view    <file.rsd | file.wav | folder>...          waveform + levels
python rsdtool.py play    <file.rsd | file.wav | folder>...          listen
python rsdtool.py gui     [files or folders]                         open the window
python rsdtool.py -h
```

| Option | Meaning |
|---|---|
| `-o <path>` | output file (one input) or folder (many inputs; a trailing `/` or `\` or an existing folder also counts) |
| `-c radp\|vag\|pcm\|pcmb` | encode codec (default `radp`) |
| `-t <orig.rsd>` | reuse the 0x800-byte header (names, description) of an original RSD |
| `-r <rate>` | sample rate written to the header (no resampling) |
| `-n <path>` / `-d <text>` | source path / description for a new header |
| `-R` | recurse into folders |
| `-f` | overwrite existing outputs (default: write `name_new.ext`) |
| `-q` | quiet |
| `-s <sec>`, `-l`, `-w <cols>`, `-b`, `-p <png>` | `view` / `play` only: start position, loop, console width, block characters, save waveform PNG |

Folder arguments are filtered by command (`decode` takes `.rsd`, `encode` takes `.wav`).

Example console output of `view`:

```
demo.rsd
  format      : RSD6 RADP, 2 ch, 22050 Hz, 16-bit   length 00:01.6 (35296 frames)
  source path : sfx/demo_sweep.wav
  description : example sweep
  (waveform display scaled to the peak; levels below are real)
  ch 1  peak -8.7 dBFS   rms -19.2 dBFS   dc -0.2   clipped 0   silence: start 00:00.0, end 00:00.0
     #####
    #############
   ########################
  #################################################
  ################################################################
  #################################################
   ########################
    #############
     #####
  00:00.0      00:00.4         00:00.8         00:01.2     00:01.6
```

### Typical modding round trip

```
python rsdtool.py decode  sounds/ -R -o wavs/
# edit the WAVs in any audio editor
python rsdtool.py encode  wavs/boom.wav -t sounds/boom.rsd -c radp -o boom_new.rsd
```

`-t` keeps the original header text. Use `-c vag` to match files that were VAG originally.

## Playback

No extra packages: `winsound` on Windows, `afplay` on macOS, and on Linux whichever of `paplay`,
`aplay`, `ffplay`, `play` (sox) or `pw-play` is installed. For anything else set `RSDTOOL_PLAYER`
to a command line containing `{file}`:

```
set RSDTOOL_PLAYER=ffplay -nodisp -autoexit {file}
```

## Supported formats

| Codec | Storage | Notes |
|---|---|---|
| `RADP` | IMA ADPCM, 20-byte blocks per channel (32 samples) | default encoder; padded to 32 samples |
| `VAG` | PlayStation ADPCM, 16-byte blocks per channel (28 samples) | encoder searches all 5 filters x 13 shifts; one leading silent block unless the sound starts silent |
| `PCM` | 16-bit little-endian | lossless |
| `PCMB` | 16-bit big-endian | lossless |

The RSD format keeps no exact sample count, so encoded sounds are padded with silence to a whole
block. Details of the header and codecs are in [docs/FORMAT.md](docs/FORMAT.md).

## Project layout

| File | Purpose |
|---|---|
| `rsdtool.py` | RSD parsing/building, codecs, WAV I/O, command-line interface |
| `rsdplayer.py` | waveform / level analysis, `view`, audio playback engine, sound list |
| `rsdgui.py` | the window and its background conversion jobs |
| `RSD_Tool.pyw`, `Start_RSD_Tool.bat` | double-click launchers |
| `tests/` | test suite (`python -m unittest discover -s tests`) |
| `docs/FORMAT.md` | format notes |

## Tests

```
python -m unittest discover -s tests -v
```

30 tests cover every codec round trip, header building and parsing, WAV input formats, the command
line (including output naming rules), the playback engine (with a fake audio backend), the sound
list, the conversion jobs and a button-by-button run of the window code against a stubbed `tkinter`.
They use generated audio only and need no game files or display.

## Status and limitations

Please read this before trusting the tool with files you can't replace.

- The codecs, tables, header layout and options were reconstructed from the disassembly of
  `rsdtool.exe` 1.1.0. **Output has not been compared byte-for-byte with that program, and no
  original game `.rsd` file was available during development** - testing used generated audio.
- **Not yet tried in the game.** Keep backups of original files.
- The window itself and Windows sound output were exercised only through simulated components on a
  machine without a screen or sound card; bug reports from real use are very welcome.
- The test suite passes on Python 3.11, 3.12 and 3.13. Python 3.8-3.10 are not tested (the code
  avoids newer-only syntax).
- No resampling: a WAV with a different sample rate than the original plays at a different speed
  in-game unless you resample it first. Audio is converted to 16-bit.
- Only the `RSD6` header and the four codecs above are handled.

If you have real `.rsd` samples (or find one the tool decodes wrongly), please open an issue.

## Credits and disclaimer

Made by **TheTurkishDude**.

This is an unofficial fan/modding tool. It is not affiliated with or endorsed by Radical
Entertainment, Activision, Sony or any rights holder. *The Incredible Hulk: Ultimate Destruction*,
Pure3D and related names belong to their respective owners. Use it only with files you are entitled
to modify.
