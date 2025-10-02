#!/usr/bin/env python3
import os, sys, threading, queue, shlex, subprocess, re, json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

APP_TITLE = "Whisp GUI — ffmpeg + whisper-cli"

DEFAULTS = {
    "WHISPER_BIN": str(Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli"),
    "WHISPER_MODEL": str(Path.home() / "whisper.cpp" / "models" / "ggml-large-v3.bin"),
    "FFMPEG_BIN": "ffmpeg",
    "LANG": "ru",
    "OUTDIR": "",
    "KEEP_WAV": "0",
    "OVERWRITE": "0",
}

CONFIG_PATH = os.environ.get(
    "WHISPER_PIPELINE_CONFIG",
    str(Path.home() / ".whisper-pipeline.conf")
)

def settings_path():
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "WhispGUI" / "settings.json"
    return Path.home() / ".config" / "whisp_gui.json"

SETTINGS_FILE = settings_path()

def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def load_json_settings():
    p = SETTINGS_FILE
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_json_settings(d):
    try:
        ensure_parent(SETTINGS_FILE).write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Warning: failed to save settings: {e}", file=sys.stderr)

def load_shellish_kv_config(path):
    """Parse a simple KEY=\"value\" (or KEY=value) config file."""
    cfg = {}
    p = Path(path)
    if not p.exists():
        return cfg
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Z_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(.*))\s*$', line)
        if m:
            key = m.group(1)
            val = m.group(2) or m.group(3) or m.group(4) or ""
            val = os.path.expanduser(os.path.expandvars(val.strip()))
            cfg[key] = val
    return cfg

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(900, 640)

        # Load defaults from shell config + JSON settings
        cfg = dict(DEFAULTS)
        cfg.update(load_shellish_kv_config(CONFIG_PATH))
        self.settings = load_json_settings()

        # UI state (StringVars/BooleanVars with persisted defaults)
        def sget(key, default=""):
            return self.settings.get(key, cfg.get(key, default))

        self.whisper_bin = tk.StringVar(value=sget("whisper_bin", cfg["WHISPER_BIN"]))
        self.model_path  = tk.StringVar(value=sget("model_path", cfg["WHISPER_MODEL"]))
        self.ffmpeg_bin  = tk.StringVar(value=sget("ffmpeg_bin", cfg["FFMPEG_BIN"]))
        self.lang        = tk.StringVar(value=sget("lang", cfg["LANG"]))
        self.outdir      = tk.StringVar(value=sget("outdir", cfg["OUTDIR"]))
        self.extra_args  = tk.StringVar(value=sget("extra_args", ""))

        self.keep_wav    = tk.BooleanVar(value=bool(int(str(sget("KEEP_WAV", cfg.get("KEEP_WAV","0"))))))
        self.overwrite   = tk.BooleanVar(value=bool(int(str(sget("OVERWRITE", cfg.get("OVERWRITE","0"))))))

        # Output format toggles
        self.output_txt  = tk.BooleanVar(value=bool(self.settings.get("output_txt", True)))
        self.output_srt  = tk.BooleanVar(value=bool(self.settings.get("output_srt", False)))
        self.output_vtt  = tk.BooleanVar(value=bool(self.settings.get("output_vtt", False)))

        # Context + threads controls (flexible)
        self.ctx_flag    = tk.StringVar(value=self.settings.get("ctx_flag", "--max-context"))
        self.ctx_size    = tk.StringVar(value=self.settings.get("ctx_size", ""))  # tokens; empty = skip
        self.threads     = tk.StringVar(value=self.settings.get("threads", ""))   # empty = let CLI decide

        # NEW: Per-file artifacts subfolder (persisted)
        self.per_file_subdir = tk.BooleanVar(value=bool(self.settings.get("per_file_subdir", True)))

        # Recent dirs
        self.recent_input_dir  = self.settings.get("recent_input_dir", str(Path.home()))
        self.recent_output_dir = self.settings.get("recent_output_dir", self.outdir.get() or str(Path.home()))

        # Custom params list: [{"enabled": bool, "name": str, "value": str}]
        self.params = self.settings.get("params", [])

        self.files = []
        self.log_q = queue.Queue()
        self.worker = None

        self.build_ui()
        self.after(100, self.drain_logs)
        self.bind_variable_traces()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # Paths frame
        paths = ttk.LabelFrame(self, text="Paths & Settings")
        paths.pack(fill="x", **pad)

        self._row(paths, "whisper-cli", self.whisper_bin, lambda: self._pick_file(self.whisper_bin, kind="exe"))
        self._row(paths, "Model (.bin)", self.model_path,  lambda: self._pick_file(self.model_path, kind="file"))
        self._row(paths, "ffmpeg",       self.ffmpeg_bin,  lambda: self._pick_file(self.ffmpeg_bin, kind="exe"))
        self._row(paths, "Output dir",   self.outdir,      lambda: self._pick_dir(self.outdir))

        # Lang + Flags
        lf = ttk.Frame(paths)
        lf.pack(fill="x", padx=8, pady=(0,6))
        ttk.Label(lf, text="Language code").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf, textvariable=self.lang, width=10).grid(row=0, column=1, sticky="w", padx=(6,12))
        ttk.Checkbutton(lf, text="Overwrite existing", variable=self.overwrite).grid(row=0, column=2, sticky="w", padx=12)
        ttk.Checkbutton(lf, text="Keep WAVs", variable=self.keep_wav).grid(row=0, column=3, sticky="w", padx=12)
        # NEW: Per-file subfolder toggle
        ttk.Checkbutton(lf, text="Per-file subfolder", variable=self.per_file_subdir).grid(row=0, column=4, sticky="w", padx=12)

        # Output format toggles
        of = ttk.Frame(paths)
        of.pack(fill="x", padx=8, pady=(0,6))
        ttk.Label(of, text="Output formats").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(of, text="TXT", variable=self.output_txt).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Checkbutton(of, text="SRT", variable=self.output_srt).grid(row=0, column=2, sticky="w", padx=8)
        ttk.Checkbutton(of, text="VTT", variable=self.output_vtt).grid(row=0, column=3, sticky="w", padx=8)

        # Context + threads
        ct = ttk.Frame(paths)
        ct.pack(fill="x", padx=8, pady=(0,6))
        ttk.Label(ct, text="Context flag name").grid(row=0, column=0, sticky="w")
        ttk.Entry(ct, textvariable=self.ctx_flag, width=16).grid(row=0, column=1, sticky="w", padx=(6,12))
        ttk.Label(ct, text="Context size (tokens)").grid(row=0, column=2, sticky="w")
        ttk.Entry(ct, textvariable=self.ctx_size, width=10).grid(row=0, column=3, sticky="w", padx=(6,12))
        ttk.Label(ct, text="Threads (-t)").grid(row=0, column=4, sticky="w")
        ttk.Entry(ct, textvariable=self.threads, width=8).grid(row=0, column=5, sticky="w", padx=(6,12))
        ct.columnconfigure(6, weight=1)

        # Extra args (free text)
        ttk.Label(paths, text="Extra whisper-cli args (optional)").pack(anchor="w", padx=8)
        ttk.Entry(paths, textvariable=self.extra_args).pack(fill="x", padx=8, pady=(0,6))

        # Custom Parameters panel
        pframe = ttk.LabelFrame(self, text="Parameters (name/value pairs appended to whisper-cli)")
        pframe.pack(fill="both", expand=False, **pad)
        self.param_tree = ttk.Treeview(pframe, columns=("enabled","name","value"), show="headings", height=6)
        self.param_tree.heading("enabled", text="On")
        self.param_tree.heading("name", text="Name (e.g., --temperature)")
        self.param_tree.heading("value", text="Value (optional)")
        self.param_tree.column("enabled", width=40, anchor="center")
        self.param_tree.column("name", width=260)
        self.param_tree.column("value", width=260)
        self.param_tree.pack(side="left", fill="both", expand=True, padx=(8,0), pady=6)

        psb = ttk.Scrollbar(pframe, orient="vertical", command=self.param_tree.yview)
        self.param_tree.configure(yscrollcommand=psb.set)
        psb.pack(side="left", fill="y")

        # Param controls
        pc = ttk.Frame(pframe)
        pc.pack(side="left", fill="y", padx=8, pady=6)
        self.p_enabled = tk.BooleanVar(value=True)
        self.p_name    = tk.StringVar(value="")
        self.p_value   = tk.StringVar(value="")
        ttk.Checkbutton(pc, text="Enabled", variable=self.p_enabled).pack(anchor="w")
        ttk.Label(pc, text="Name").pack(anchor="w", pady=(6,0))
        ttk.Entry(pc, textvariable=self.p_name, width=28).pack(anchor="w")
        ttk.Label(pc, text="Value").pack(anchor="w", pady=(6,0))
        ttk.Entry(pc, textvariable=self.p_value, width=28).pack(anchor="w")

        brow = ttk.Frame(pc); brow.pack(fill="x", pady=8)
        ttk.Button(brow, text="Add / Update", command=self.param_add_update).pack(side="left", padx=(0,6))
        ttk.Button(brow, text="Remove", command=self.param_remove).pack(side="left")

        # Seed tree from settings
        for item in self.params:
            self.param_tree.insert("", "end", values=("✓" if item.get("enabled", True) else "", item.get("name",""), item.get("value","")))

        # Files frame
        ff = ttk.LabelFrame(self, text="Input files")
        ff.pack(fill="both", expand=True, **pad)
        inner = ttk.Frame(ff)
        inner.pack(fill="both", expand=True, padx=8, pady=6)

        self.listbox = tk.Listbox(inner, selectmode=tk.EXTENDED)
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        btns = ttk.Frame(inner)
        btns.grid(row=0, column=2, sticky="ns", padx=(8,0))
        ttk.Button(btns, text="Add files…", command=self.add_files).pack(fill="x", pady=(0,6))
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).pack(fill="x", pady=6)
        ttk.Button(btns, text="Clear", command=self.clear_files).pack(fill="x", pady=6)

        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        # Actions
        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        self.go_btn = ttk.Button(actions, text="Transcribe", command=self.start_worker)
        self.go_btn.pack(side="left")
        self.status = ttk.Label(actions, text="Idle")
        self.status.pack(side="right")

        # Log
        lgf = ttk.LabelFrame(self, text="Log")
        lgf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lgf, height=10, wrap="word", state="disabled")
        lsb = ttk.Scrollbar(lgf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # Double-click param row to load into editor
        self.param_tree.bind("<Double-1>", self.param_load_from_selection)

    def _row(self, parent, label, var, picker):
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=8, pady=(6,0))
        ttk.Label(f, text=label).pack(side="left")
        e = ttk.Entry(f, textvariable=var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(f, text="Browse…", command=picker).pack(side="left")

    # ---------- Persistence ----------
    def bind_variable_traces(self):
        def bind(var, key):
            var.trace_add("write", lambda *_: self.persist_now(key, var.get()))
        bind(self.whisper_bin, "whisper_bin")
        bind(self.model_path,  "model_path")
        bind(self.ffmpeg_bin,  "ffmpeg_bin")
        bind(self.lang,        "lang")
        bind(self.outdir,      "outdir")
        bind(self.extra_args,  "extra_args")
        bind(self.ctx_flag,    "ctx_flag")
        bind(self.ctx_size,    "ctx_size")
        bind(self.threads,     "threads")

        # bools
        self.output_txt.trace_add("write", lambda *_: self.persist_now("output_txt", self.output_txt.get()))
        self.output_srt.trace_add("write", lambda *_: self.persist_now("output_srt", self.output_srt.get()))
        self.output_vtt.trace_add("write", lambda *_: self.persist_now("output_vtt", self.output_vtt.get()))
        self.keep_wav.trace_add("write",   lambda *_: self.persist_now("KEEP_WAV", int(self.keep_wav.get())))
        self.overwrite.trace_add("write",  lambda *_: self.persist_now("OVERWRITE", int(self.overwrite.get())))
        # NEW: persist per-file subdir toggle
        self.per_file_subdir.trace_add("write", lambda *_: self.persist_now("per_file_subdir", self.per_file_subdir.get()))

    def persist_now(self, key, value):
        self.settings[key] = value
        save_json_settings(self.settings)

    def save_param_list(self):
        items = []
        for iid in self.param_tree.get_children(""):
            on, name, value = self.param_tree.item(iid, "values")
            items.append({"enabled": (on == "✓"), "name": str(name), "value": str(value)})
        self.settings["params"] = items
        save_json_settings(self.settings)

    # ---------- Param controls ----------
    def param_add_update(self):
        name = self.p_name.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Parameter name (e.g., --temperature) is required.")
            return
        value = self.p_value.get().strip()
        enabled = "✓" if self.p_enabled.get() else ""
        # If a row with same name exists, update it
        for iid in self.param_tree.get_children(""):
            on, n, v = self.param_tree.item(iid, "values")
            if n == name:
                self.param_tree.item(iid, values=(enabled, name, value))
                self.save_param_list()
                return
        self.param_tree.insert("", "end", values=(enabled, name, value))
        self.save_param_list()

    def param_remove(self):
        sel = self.param_tree.selection()
        for iid in sel:
            self.param_tree.delete(iid)
        self.save_param_list()

    def param_load_from_selection(self, _evt=None):
        sel = self.param_tree.selection()
        if not sel:
            return
        on, n, v = self.param_tree.item(sel[0], "values")
        self.p_enabled.set(on == "✓")
        self.p_name.set(n)
        self.p_value.set(v)

    # ---------- File pickers ----------
    def _pick_file(self, var, kind="file"):
        initial = self.recent_input_dir if kind != "dir" else self.recent_output_dir
        path = filedialog.askopenfilename(initialdir=initial) if kind != "exe" else filedialog.askopenfilename(initialdir=initial)
        if path:
            var.set(path)
            parent = str(Path(path).parent)
            self.recent_input_dir = parent
            self.settings["recent_input_dir"] = parent
            save_json_settings(self.settings)

    def _pick_dir(self, var):
        initial = self.recent_output_dir or self.outdir.get() or str(Path.home())
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            var.set(path)
            self.recent_output_dir = path
            self.settings["recent_output_dir"] = path
            save_json_settings(self.settings)

    def add_files(self):
        paths = filedialog.askopenfilenames(title="Choose media files", initialdir=self.recent_input_dir or str(Path.home()))
        if not paths:
            return
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", p)
        # Remember folder
        self.recent_input_dir = str(Path(paths[0]).parent)
        self.settings["recent_input_dir"] = self.recent_input_dir
        save_json_settings(self.settings)

    def remove_selected(self):
        sel = list(self.listbox.curselection())[::-1]
        for idx in sel:
            path = self.listbox.get(idx)
            if path in self.files:
                self.files.remove(path)
            self.listbox.delete(idx)

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")

    # ---------- Run ----------
    def start_worker(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning("No files", "Add at least one input file.")
            return
        self.status.config(text="Working…")
        self.go_btn.config(state="disabled")
        self.log_q.put(("info", "Starting…\n"))
        self.worker = threading.Thread(target=self.run_pipeline, daemon=True)
        self.worker.start()

    def drain_logs(self):
        try:
            while True:
                level, text = self.log_q.get_nowait()
                self.log.config(state="normal")
                self.log.insert("end", text)
                self.log.see("end")
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        finally:
            self.after(100, self.drain_logs)

    def log_info(self, msg): self.log_q.put(("info", msg + ("\n" if not msg.endswith("\n") else "")))
    def log_err (self, msg): self.log_q.put(("err",  "ERROR: " + msg + ("\n" if not msg.endswith("\n") else "")))

    def run_cmd(self, args):
        self.log_info("$ " + " ".join(shlex.quote(a) for a in args))
        subprocess.run(args, check=True)

    def run_pipeline(self):
        try:
            whisper_bin = self.whisper_bin.get().strip()
            model_path  = self.model_path.get().strip()
            ffmpeg_bin  = self.ffmpeg_bin.get().strip()
            lang        = self.lang.get().strip() or "ru"
            outdir      = self.outdir.get().strip()
            keep_wav    = self.keep_wav.get()
            overwrite   = self.overwrite.get()
            extra       = shlex.split(self.extra_args.get().strip()) if self.extra_args.get().strip() else []

            if not Path(whisper_bin).exists():
                raise FileNotFoundError(f"whisper-cli not found: {whisper_bin}")
            if not Path(model_path).exists():
                raise FileNotFoundError(f"Model not found: {model_path}")
            if not shutil_which(ffmpeg_bin):
                raise FileNotFoundError(f"ffmpeg not in PATH: {ffmpeg_bin}")
            if outdir:
                Path(outdir).mkdir(parents=True, exist_ok=True)

            # Build global whisper args that apply to all files
            base_whisper = [whisper_bin, "-m", str(model_path), "-l", lang]

            # Output formats
            if self.output_txt.get(): base_whisper += ["--output-txt", "true"]
            if self.output_srt.get(): base_whisper += ["--output-srt", "true"]
            if self.output_vtt.get(): base_whisper += ["--output-vtt", "true"]

            # Threads
            if self.threads.get().strip():
                base_whisper += ["-t", self.threads.get().strip()]

            # Context size
            if self.ctx_size.get().strip():
                flag = self.ctx_flag.get().strip() or "--max-context"
                base_whisper += [flag, self.ctx_size.get().strip()]

            # Custom params (enabled only)
            for iid in self.param_tree.get_children(""):
                on, name, value = self.param_tree.item(iid, "values")
                if on == "✓" and name.strip():
                    if value.strip():
                        base_whisper += [name.strip(), value.strip()]
                    else:
                        base_whisper += [name.strip()]

            # Free-text extra
            base_whisper += extra

            for inpath in self.files:
                try:
                    self.process_one(inpath, outdir, ffmpeg_bin, base_whisper, keep_wav, overwrite)
                except Exception as e:
                    self.log_err(f"{inpath}: {e}")
            self.log_info("Done.")
            self.status.config(text="Done")
            messagebox.showinfo("Finished", "Transcription complete.")
        except Exception as e:
            self.log_err(str(e))
            self.status.config(text="Error")
            messagebox.showerror("Error", str(e))
        finally:
            self.go_btn.config(state="normal")

    def process_one(self, inpath, outdir, ffmpeg_bin, base_whisper, keep_wav, overwrite):
        p = Path(inpath)
        if not p.exists():
            self.log_err(f"Not a file: {inpath}")
            return

        # Decide base directory for artifacts
        base_dir = Path(outdir) if outdir else p.parent
        stem = p.stem

        # NEW: create per-file subfolder if enabled (with collision-safe naming)
        if self.per_file_subdir.get():
            artifacts_dir = base_dir / stem
            if overwrite:
                artifacts_dir.mkdir(parents=True, exist_ok=True)
            else:
                artifacts_dir = unique_dir(base_dir, stem) if artifacts_dir.exists() else artifacts_dir
                artifacts_dir.mkdir(parents=True, exist_ok=True)
        else:
            artifacts_dir = base_dir
            artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.log_info(f"Artifacts dir: {artifacts_dir}")

        wav = artifacts_dir / f"{stem}.16k.mono.wav"
        txt = artifacts_dir / f"{stem}.txt"

        # Extract WAV
        if wav.exists() and not overwrite:
            self.log_info(f"Skip extract (exists): {wav}")
        else:
            self.log_info(f"Extracting → {wav}")
            args = [ffmpeg_bin, "-y", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000", str(wav)]
            self.run_cmd(args)

        # Transcribe
        if txt.exists() and not overwrite:
            self.log_info(f"Skip transcribe (exists): {txt}")
        else:
            self.log_info(f"Transcribing with: {Path(base_whisper[0]).name} …")
            args = list(base_whisper) + ["-f", str(wav)]
            self.run_cmd(args)

            # Normalize common output filenames to stem.txt
            if not txt.exists():
                candidates = list(artifacts_dir.glob(f"{stem}*.txt")) + [Path(str(wav) + ".txt")]
                newest = newest_existing(candidates)
                if newest and newest != txt:
                    newest.rename(txt)

        # Cleanup
        if not keep_wav and wav.exists():
            wav.unlink(missing_ok=True)
        if txt.exists():
            self.log_info(f"✅ {txt}")
        else:
            self.log_err(f"Could not locate .txt for: {inpath}")

    def on_close(self):
        # Persist current param list and outdir as "recent_output_dir"
        self.save_param_list()
        if self.outdir.get().strip():
            self.settings["recent_output_dir"] = self.outdir.get().strip()
            save_json_settings(self.settings)
        self.destroy()

# ---------- helpers ----------
def unique_dir(base: Path, name: str) -> Path:
    """Return base/name or base/'name (2)'/… if already exists."""
    cand = base / name
    if not cand.exists():
        return cand
    i = 2
    while True:
        alt = base / f"{name} ({i})"
        if not alt.exists():
            return alt
        i += 1

def newest_existing(paths):
    paths = [p for p in paths if Path(p).exists()]
    if not paths:
        return None
    return max(paths, key=lambda p: Path(p).stat().st_mtime)

def shutil_which(cmd):
    paths = os.environ.get("PATH", "").split(os.pathsep)
    if os.path.isabs(cmd) and os.access(cmd, os.X_OK):
        return cmd
    for base in paths:
        cand = Path(base) / cmd
        if os.name == "nt":
            for ext in [".exe", ".cmd", ".bat", ""]:
                if (cand.with_suffix(ext)).exists():
                    return str(cand.with_suffix(ext))
        else:
            if cand.exists() and os.access(cand, os.X_OK):
                return str(cand)
    return None

if __name__ == "__main__":
    app = App()
    app.mainloop()
