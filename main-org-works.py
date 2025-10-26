# -*- coding: utf-8 -*-
"""
YouTube → MP3 (192 kbps) GUI
Single-file Tkinter app with dark theme, Start/Stop, playlist/single toggle,
folder picker, threaded downloads, and live logs.

Deps:
    pip install yt-dlp
    # install ffmpeg and ensure it is on PATH

Test:
    python yt_mp3_gui.py
"""

import os
import sys
import shutil
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import yt_dlp
from yt_dlp.utils import DownloadError, DownloadCancelled  # for graceful cancel


# ------------------------------
# Helpers / constants
# ------------------------------
BG_COLOR = "#1e1e1e"
PANEL_COLOR = "#252526"
FG_COLOR = "#e0e0e0"
SUBTLE_FG = "#bdbdbd"
ACCENT = "#00c896"   # teal-ish
ACCENT_2 = "#b4ff39" # lime-ish, sparingly

DEFAULT_BITRATE = "192"

def human_time():
    return datetime.now().strftime("%H:%M:%S")

def ensure_ffmpeg_or_die(root):
    if not shutil.which("ffmpeg"):
        messagebox.showerror("FFmpeg missing",
                             "FFmpeg is not on PATH.\nInstall FFmpeg and restart the app.")
        root.destroy()
        sys.exit(1)

def default_download_dir():
    # try user's Downloads, fallback to cwd
    home = os.path.expanduser("~")
    dl = os.path.join(home, "Downloads")
    return dl if os.path.isdir(dl) else os.getcwd()


# ------------------------------
# yt-dlp logger that forwards to UI
# ------------------------------
class YTDLogger:
    def __init__(self, emit_cb):
        self.emit = emit_cb  # callable(str)

    def debug(self, msg):
        # yt-dlp is chatty; only show meaningful lines
        m = str(msg)
        if m.strip():
            self.emit(f"[{human_time()}] {m}")

    def warning(self, msg):
        self.emit(f"[{human_time()}] ⚠️ {msg}")

    def error(self, msg):
        self.emit(f"[{human_time()}] ❌ {msg}")


# ------------------------------
# Downloader (wraps yt-dlp)
# ------------------------------
class YTAudioDownloader:
    """
    Simple wrapper around yt_dlp to:
      - use progress hooks for stop/cancel
      - enforce MP3 192kbps
      - handle single vs playlist output templates
      - stream logs to UI
    """
    def __init__(self, log_cb, stop_event):
        self.log = log_cb         # callable(str)
        self.stop_event = stop_event

    def _progress_hook(self, d):
        # Called multiple times during a download
        if self.stop_event.is_set():
            # Raise a special exception to tell yt-dlp to cancel ASAP
            raise DownloadCancelled("User requested stop.")
        # Surface some friendly progress lines
        status = d.get("status")
        if status == "downloading":
            speed = d.get("speed")
            eta = d.get("eta")
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                pct = (downloaded / total) * 100
                self.log(f"[{human_time()}] ↓ {pct:5.1f}% | "
                         f"{(speed or 0)/1024/1024:.2f} MiB/s | ETA {eta or '?'}s")
            else:
                self.log(f"[{human_time()}] ↓ downloading…")
        elif status == "finished":
            self.log(f"[{human_time()}] ✅ Downloaded; converting…")

    def build_opts(self, url, out_dir, mode_single=True, bitrate=DEFAULT_BITRATE):
        # Output template
        if mode_single:
            outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")
        else:
            # playlist: put tracks inside a folder named by playlist title (fallback to id)
            outtmpl = os.path.join(out_dir, "%(playlist_title,playlist_id)s",
                                   "%(playlist_index)03d - %(title)s.%(ext)s")

        # Core options
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "writethumbnail": True,
            "embedthumbnail": True,
            "addmetadata": True,
            "ignoreerrors": True,
            "retries": 10,
            "continuedl": True,
            "noprogress": True,  # we'll show our own log lines
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "logger": YTDLogger(self.log),
            "progress_hooks": [self._progress_hook],
        }

        # Tiny tweak: if user picked "playlist" but pasted a single URL, yt-dlp still works fine.
        # No need for special-case logic here.

        return ydl_opts

    def download(self, url, out_dir, mode_single=True, bitrate=DEFAULT_BITRATE):
        opts = self.build_opts(url, out_dir, mode_single, bitrate)
        self.log(f"[{human_time()}] ▶ Starting ({'Single' if mode_single else 'Playlist'}) → MP3 {bitrate} kbps")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎵 Finished successfully.")
        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
        except DownloadError as e:
            self.log(f"[{human_time()}] ❌ Download error: {e}")
        except Exception as e:
            self.log(f"[{human_time()}] 💥 Unexpected error: {e}")


# ------------------------------
# Tkinter GUI
# ------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube → MP3 (192 kbps)")
        self.geometry("880x600")
        self.configure(bg=BG_COLOR)
        self.minsize(760, 520)

        ensure_ffmpeg_or_die(self)

        # Threading & logging
        self.worker = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()

        # UI
        self._build_style()
        self._build_ui()

        # Poll log queue
        self.after(80, self._drain_log_queue)

    # ---------- UI setup ----------
    def _build_style(self):
        style = ttk.Style(self)
        # Use 'clam' so we can recolor widgets
        style.theme_use("clam")

        style.configure("TLabel", background=BG_COLOR, foreground=FG_COLOR)
        style.configure("Panel.TFrame", background=PANEL_COLOR)
        style.configure("TEntry", fieldbackground="#2d2d2d", foreground=FG_COLOR, insertcolor=FG_COLOR)
        style.configure("TButton",
                        background=ACCENT, foreground="#0b0b0b",
                        borderwidth=0, focusthickness=3, focuscolor=ACCENT)
        style.map("TButton",
                  background=[("active", ACCENT_2)],
                  foreground=[("active", "#101010")])

        style.configure("TRadiobutton",
                        background=BG_COLOR, foreground=FG_COLOR,
                        indicatorcolor=ACCENT, indicatordiameter=12)

    def _build_ui(self):
        # Top panel (inputs)
        top = ttk.Frame(self, style="Panel.TFrame", padding=16)
        top.pack(side="top", fill="x")

        # URL
        ttk.Label(top, text="YouTube URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(top, textvariable=self.url_var, width=80)
        self.url_entry.grid(row=1, column=0, columnspan=5, sticky="we", pady=(6, 12))
        self.url_entry.focus()

        # Mode (single / playlist)
        mode_frame = ttk.Frame(top, style="Panel.TFrame")
        mode_frame.grid(row=2, column=0, sticky="w")
        ttk.Label(mode_frame, text="Mode").pack(side="left", padx=(0, 12))
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(mode_frame, text="Single", value="single", variable=self.mode_var).pack(side="left", padx=6)
        ttk.Radiobutton(mode_frame, text="Playlist", value="playlist", variable=self.mode_var).pack(side="left", padx=6)

        # Save folder
        ttk.Label(top, text="Save to").grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.out_dir_var = tk.StringVar(value=default_download_dir())
        self.out_entry = ttk.Entry(top, textvariable=self.out_dir_var, width=64)
        self.out_entry.grid(row=4, column=0, columnspan=4, sticky="we", pady=(6, 0))
        browse_btn = ttk.Button(top, text="Browse…", command=self._choose_dir)
        browse_btn.grid(row=4, column=4, sticky="e", padx=(8, 0))

        # Buttons (Start / Stop)
        btns = ttk.Frame(top, style="Panel.TFrame")
        btns.grid(row=5, column=0, columnspan=5, sticky="we", pady=(16, 0))
        self.start_btn = ttk.Button(btns, text="Start", command=self._on_start)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._on_stop)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn.pack(side="left")
        self.stop_btn.state(["disabled"])

        for c in range(5):
            top.grid_columnconfigure(c, weight=1)

        # Log console
        log_frame = ttk.Frame(self, style="Panel.TFrame", padding=12)
        log_frame.pack(side="top", fill="both", expand=True, pady=(12, 0), padx=0)

        self.log_text = tk.Text(log_frame, bg="#111111", fg=FG_COLOR, insertbackground=FG_COLOR,
                                relief="flat", height=18, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("time", foreground=ACCENT)
        self.log_text.tag_configure("subtle", foreground=SUBTLE_FG)

        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side="right", fill="y")

        # Footer
        footer = ttk.Frame(self, style="Panel.TFrame")
        footer.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Ready")
        self.status_lbl = ttk.Label(footer, textvariable=self.status_var, foreground=SUBTLE_FG)
        self.status_lbl.pack(side="left", padx=12, pady=6)

    # ---------- UI events ----------
    def _choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.out_dir_var.get() or default_download_dir())
        if chosen:
            self.out_dir_var.set(chosen)

    def _on_start(self):
        url = (self.url_var.get() or "").strip()
        out_dir = (self.out_dir_var.get() or "").strip()

        if not url:
            messagebox.showwarning("Missing URL", "Please paste a YouTube URL.")
            return
        if not out_dir:
            messagebox.showwarning("Missing Folder", "Please choose a destination folder.")
            return
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Folder Error", f"Cannot create folder:\n{e}")
                return

        self._set_ui_running(True)
        self._log(f"[{human_time()}] 🚀 Preparing…")
        self.stop_event.clear()

        mode_single = (self.mode_var.get() == "single")
        # spawn worker thread
        self.worker = threading.Thread(
            target=self._run_download_thread,
            args=(url, out_dir, mode_single),
            daemon=True
        )
        self.worker.start()
        self.status_var.set("Downloading…")

    def _on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._log(f"[{human_time()}] ⏳ Stopping… (this may take a few seconds)")
            self.status_var.set("Stopping…")

    # ---------- Thread target ----------
    def _run_download_thread(self, url, out_dir, mode_single):
        downloader = YTAudioDownloader(self._enqueue_log, self.stop_event)
        try:
            downloader.download(url, out_dir, mode_single=mode_single, bitrate=DEFAULT_BITRATE)
        finally:
            # always re-enable UI after completion/stop/error
            self.after(0, lambda: self._set_ui_running(False))
            self.after(0, self._update_status_finished)

    # ---------- Logging ----------
    def _enqueue_log(self, text):
        self.log_queue.put(text)

    def _log(self, text):
        # direct (UI thread only)
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                # simple tag for time prefix
                if line.startswith("[") and "]" in line[:16]:
                    end = line.find("]")
                    self.log_text.insert("end", line[:end+1] + " ", ("time",))
                    self.log_text.insert("end", line[end+2:] + "\n")
                else:
                    self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        finally:
            self.after(80, self._drain_log_queue)

    # ---------- UI state ----------
    def _set_ui_running(self, running: bool):
        if running:
            self.start_btn.state(["disabled"])
            self.stop_btn.state(["!disabled"])
            self.url_entry.configure(state="disabled")
            self.out_entry.configure(state="disabled")
        else:
            self.start_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
            self.url_entry.configure(state="normal")
            self.out_entry.configure(state="normal")

    def _update_status_finished(self):
        if self.stop_event.is_set():
            self.status_var.set("Stopped.")
        else:
            self.status_var.set("Done.")

# ------------------------------
# Main
# ------------------------------
if __name__ == "__main__":
    app = App()
    app.mainloop()
