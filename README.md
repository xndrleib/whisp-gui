# Whisp GUI — ffmpeg + whisper-cli

A small, cross-platform Tkinter desktop app to batch-transcribe media using
[`ffmpeg`](https://ffmpeg.org/) and [`whisper.cpp`](https://github.com/ggerganov/whisper.cpp)'s `whisper-cli`.

- Select any number of audio/video files
- Auto-extract mono 16 kHz WAVs via `ffmpeg`
- Run `whisper-cli` with your chosen model and language
- Generate TXT/SRT/VTT (toggle each)
- Per-file subfolders (optional), overwrite/keep WAV toggles
- Persistent settings + a flexible “extra params” panel

> Default language is `en` (English) — change it in the UI.

---

## Quick start

### 1) Requirements

**Runtime**
- Python **3.8+**
- Tkinter (bundled with Python on macOS/Windows; on Linux install your distro’s `python3-tk`)
- `ffmpeg` available on PATH (or set its full path in the UI)
- `whisper.cpp` built with `whisper-cli` binary
- A Whisper model file (`.bin`), e.g. `ggml-large-v3.bin`

**Typical installs**
- macOS: `brew install ffmpeg`  
- Ubuntu/Debian: `sudo apt-get install ffmpeg python3-tk`
- Windows: install Python from python.org; install ffmpeg (e.g., winget/choco) or set a full path in the app

> Build `whisper.cpp` (see its README) so that `build/bin/whisper-cli` exists.  
> Download a model into `whisper.cpp/models/` (e.g., `ggml-large-v3.bin`).

### 2) Get the app

```bash
git clone https://github.com/yourname/whisp-gui.git
cd whisp-gui
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
````

### 3) Run

```bash
python whisp_gui.py
```

The app will remember paths and options between runs.

---

## Why this tool?

`whisper.cpp` is fast and local, but CLI flags can get repetitive. **Whisp GUI** gives you:

* A persistent, friendly front end for common `whisper-cli` workflows
* Smart WAV extraction, folder management, and output format toggles
* A parameter grid to add/update any extra `whisper-cli` flags without editing code

---

## Features (mapped to the UI)

* **Paths & Settings**

  * `whisper-cli` binary (defaults to `~/whisper.cpp/build/bin/whisper-cli`)
  * Model `.bin` (defaults to `~/whisper.cpp/models/ggml-large-v3.bin`)
  * `ffmpeg` binary (defaults to `ffmpeg` on PATH)
  * Output directory (optional; otherwise next to sources)

* **Language & flags**

  * Language code (e.g., `en`)
  * Overwrite existing files
  * Keep intermediate WAVs
  * **Per-file subfolder**: create a dedicated directory for each input file (collision-safe)

* **Output formats**

  * Toggle TXT / SRT / VTT

* **Context & performance**

  * Context flag name (default `--max-context`) + size (tokens)
  * Threads (`-t`) for `whisper-cli`

* **Extra args**

  * Free text appended to `whisper-cli` (split shell-style)

* **Parameters panel**

  * Add name/value pairs (e.g., `--temperature 0.2`)
  * Enable/disable per-row
  * Double-click to edit
  * Settings persist to disk

* **Logs**

  * Command previews and status
  * Success and error reporting per file

---

## Configuration files (persistence & overrides)

* **App settings (JSON, auto-managed)**

  * **Linux/macOS:** `~/.config/whisp_gui.json`
  * **Windows:** `%APPDATA%\WhispGUI\settings.json`

* **Optional shell-style overrides**

  * Path from env `WHISPER_PIPELINE_CONFIG` or default `~/.whisper-pipeline.conf`
  * Format: `KEY="value"` (quotes optional). Supported keys:

    * `WHISPER_BIN`, `WHISPER_MODEL`, `FFMPEG_BIN`, `LANG`, `OUTDIR`, `KEEP_WAV`, `OVERWRITE`

**Example `~/.whisper-pipeline.conf`:**

```sh
# Lines starting with # are comments
WHISPER_BIN="~/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL="~/whisper.cpp/models/ggml-large-v3.bin"
FFMPEG_BIN="ffmpeg"
LANG="en"
OUTDIR="~/Transcripts"
KEEP_WAV=0
OVERWRITE=0
```

> The app loads defaults from this file, then applies your saved JSON settings.
> Changing the config file affects only fields you haven’t changed in the GUI yet.

---

## How it works

1. For each selected input file:

   * Create an **artifacts directory** (either the specified output dir or next to the file).
     If “Per-file subfolder” is on, use `<stem>/` with collision-safe naming.
2. Extract mono 16 kHz WAV: `ffmpeg -y -i INPUT -vn -ac 1 -ar 16000 <stem>.16k.mono.wav`
3. Run `whisper-cli` with:

   * Model (`-m`), Language (`-l`), Threads (`-t` if provided)
   * Context flag (`--max-context` by default) if a size is set
   * Output toggles (`--output-txt true`, etc.)
   * Any user-added parameters and “Extra args”
4. Normalize output to `<stem>.txt` (TXT is also used as the presence check)
5. Optionally remove the intermediate WAV
6. Log results and continue to the next file

---

## Troubleshooting

* **“whisper-cli not found”**
  Verify the built binary path (often `~/whisper.cpp/build/bin/whisper-cli`).
* **“Model not found”**
  Ensure the `.bin` model exists and the path is correct.
* **“ffmpeg not in PATH”**
  Install ffmpeg or set its absolute path in the UI.
* **No `.txt` after transcription**
  Check logs; mis-specified arguments or unexpected output filenames can cause this. The app tries to normalize to `<stem>.txt` automatically.

