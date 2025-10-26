# -*- coding: utf-8 -*-
import os
import threading
import queue
import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.utils import ensure_ffmpeg_or_die, default_download_dir, human_time, DEFAULT_BITRATE
from core.downloader import YTAudioDownloader, DownloadProgress


# Color theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")  # or "dark-blue", "green", "blue"

ACCENT = "#00c896"

AUDIO_QUALITIES = {
    "128 kbps": "128",
    "192 kbps": "192",
    "256 kbps": "256",
    "320 kbps": "320",
}

VIDEO_QUALITIES = ["480p", "720p", "1080p", "1440p", "2160p (4K)"]




class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube → MP3 (192 kbps)")
        self.geometry("880x620")
        self.minsize(780, 540)

        ensure_ffmpeg_or_die(self)

        # Threading
        self.worker = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()

        # UI build
        self._build_ui()

        # Poll queues
        self.after(80, self._drain_log_queue)
        self.after(80, self._drain_progress_queue)

    # ------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------
    def _build_ui(self):
        # Title
        title = ctk.CTkLabel(self, text="🎧 YouTube Downloader", font=("Segoe UI", 22, "bold"))
        title.pack(pady=(20, 10))

        # URL + Start button row
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.pack(pady=(10, 8), padx=30, fill="x")

        self.url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="Paste YouTube URL here...",
            height=40,
            corner_radius=10,
        )
        self.url_entry.pack(side="left", fill="x", expand=True)

        self.start_btn = ctk.CTkButton(
            url_row,
            text="Start",
            width=100,
            height=40,
            corner_radius=10,
            fg_color=ACCENT,
            hover_color="#00e0a0",
            command=self._toggle_download,
        )
        self.start_btn.pack(side="right", padx=(10, 0))

        # Folder input (full width + opens on click)
        self.out_dir_var = ctk.StringVar(value=default_download_dir())
        self.folder_entry = ctk.CTkEntry(
            self,
            textvariable=self.out_dir_var,
            height=40,
            corner_radius=10,
        )
        self.folder_entry.pack(pady=(5, 10), padx=30, fill="x")
        self.folder_entry.bind("<Button-1>", lambda e: self._choose_dir())

        # --- NEW: Format + Quality row ---
        selects_row = ctk.CTkFrame(self, fg_color="transparent")
        selects_row.pack(pady=(2, 6), padx=30, fill="x")

        self.format_var = ctk.StringVar(value="Audio")
        self.quality_var = ctk.StringVar(value="MP3 192 kbps")  # default

        # format_label = ctk.CTkLabel(selects_row, text="Format", font=("Segoe UI", 13))
        # format_label.pack(side="left")

        self.format_menu = ctk.CTkOptionMenu(
            selects_row,
            values=["Audio", "Video"],
            variable=self.format_var,
            corner_radius=10,
            width=140,
            command=self._on_format_change,
        )
        self.format_menu.pack(side="left", padx=(8, 20))

        # quality_label = ctk.CTkLabel(selects_row, text="Quality", font=("Segoe UI", 13))
        # quality_label.pack(side="left")

        self.quality_menu = ctk.CTkOptionMenu(
            selects_row,
            values=list(AUDIO_QUALITIES.keys()),  # these are now "128 kbps", "192 kbps", etc.
            variable=self.quality_var,
            corner_radius=10,
            width=180,
        )
        self.quality_menu.pack(side="left", padx=(8, 0))

        # Progress bar
        self.progress_var = ctk.DoubleVar(value=0)
        self.progress_bar = ctk.CTkProgressBar(self, variable=self.progress_var, width=700, height=12)
        self.progress_bar.pack(pady=(20, 6))
        self.progress_bar.set(0)

        # Progress info
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(pady=(0, 10), fill="x")
        self.percent_label = ctk.CTkLabel(info_frame, text="0%", font=("Segoe UI", 13))
        self.percent_label.pack(side="left", padx=(90, 0))
        self.eta_label = ctk.CTkLabel(info_frame, text="ETA: —", font=("Segoe UI", 13))
        self.eta_label.pack(side="right", padx=(0, 90))

        # Log box
        self.log_box = ctk.CTkTextbox(
            self, width=820, height=300,
            corner_radius=12, fg_color="#141414",
            text_color="#e0e0e0", border_width=1, border_color="#333"
        )
        self.log_box.pack(pady=10)
        self.log_box.insert("end", "[ready]\n")

        # Status bar
        self.status_var = ctk.StringVar(value="Ready")
        self.status_label = ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color="#bdbdbd", font=("Segoe UI", 12)
        )
        self.status_label.pack(pady=(5, 10))




    # ------------------------------------------------------------
    # UI events
    # ------------------------------------------------------------
    def _choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.out_dir_var.get() or default_download_dir())
        if chosen:
            self.out_dir_var.set(chosen)

    def _run_download_thread_with_bitrate(self, url, out_dir, bitrate):
        downloader = YTAudioDownloader(self._enqueue_log, self._enqueue_progress, self.stop_event)
        try:
            downloader.download(url, out_dir, bitrate=bitrate)
        finally:
            self.after(0, lambda: self._set_ui_running(False))
            self.after(0, self._update_status_finished)

    def _run_download_thread_video(self, url, out_dir, quality):
        downloader = YTAudioDownloader(self._enqueue_log, self._enqueue_progress, self.stop_event)
        try:
            downloader.download_video(url, out_dir, quality=quality)
        finally:
            self.after(0, lambda: self._set_ui_running(False))
            self.after(0, self._update_status_finished)



    def _on_start(self):
        format_choice = self.format_var.get()
        quality_choice = self.quality_var.get()

        url = (self.url_entry.get() or "").strip()
        out_dir = (self.out_dir_var.get() or "").strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please paste a YouTube URL.")
            return
        if not out_dir:
            messagebox.showwarning("Missing Folder", "Please choose a destination folder.")
            return

        self._set_ui_running(True)
        self._log(f"[{human_time()}] 🚀 Starting download…")
        self.stop_event.clear()
        self.progress_bar.set(0)
        self.progress_var.set(0)
        self.percent_label.configure(text="0%")

        if format_choice == "Audio":
            bitrate = AUDIO_QUALITIES.get(quality_choice, DEFAULT_BITRATE)
            self.status_var.set(f"Audio: {quality_choice}")
            self.worker = threading.Thread(
                target=self._run_download_thread_with_bitrate,
                args=(url, out_dir, bitrate),
                daemon=True,
            )
        else:
            self.status_var.set(f"Video: {quality_choice}")
            self.worker = threading.Thread(
                target=self._run_download_thread_video,
                args=(url, out_dir, quality_choice),
                daemon=True,
            )

        self.worker.start()



    def _on_format_change(self, choice: str):
        """Switch quality list based on selected format."""
        if choice == "Audio":
            self.quality_menu.configure(values=list(AUDIO_QUALITIES.keys()))
            if self.quality_var.get() not in AUDIO_QUALITIES:
                self.quality_var.set("192 kbps")
        else:
            self.quality_menu.configure(values=VIDEO_QUALITIES)
            if self.quality_var.get() not in VIDEO_QUALITIES:
                self.quality_var.set("720p")



    def _on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._log(f"[{human_time()}] ⏳ Stopping… please wait.")
            self.status_var.set("Stopping…")

    def _toggle_download(self):
        # toggles between start and stop modes
        if self.start_btn.cget("text") == "Start":
            self._on_start()
            self.start_btn.configure(text="Stop", fg_color="#ff5656", hover_color="#ff3030")
        else:
            self._on_stop()
            self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color="#00e0a0")


    # ------------------------------------------------------------
    # Thread target
    # ------------------------------------------------------------
    def _run_download_thread(self, url, out_dir):
        downloader = YTAudioDownloader(self._enqueue_log, self._enqueue_progress, self.stop_event)
        try:
            downloader.download(url, out_dir, bitrate=DEFAULT_BITRATE)
        finally:
            self.after(0, lambda: self._set_ui_running(False))
            self.after(0, self._update_status_finished)

    # ------------------------------------------------------------
    # Logging & progress
    # ------------------------------------------------------------
    def _enqueue_log(self, text):
        self.log_queue.put(text)

    def _log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_box.insert("end", line + "\n")
                self.log_box.see("end")
        except queue.Empty:
            pass
        finally:
            self.after(80, self._drain_log_queue)

    def _enqueue_progress(self, prog: DownloadProgress):
        self.progress_queue.put(prog)

    def _drain_progress_queue(self):
        try:
            while True:
                prog: DownloadProgress = self.progress_queue.get_nowait()
                if prog.status == "downloading":
                    pct = max(0, min(100, prog.percent or 0))
                    self.progress_var.set(pct / 100)
                    self.percent_label.configure(text=f"{pct:.1f}%")
                    if prog.eta:
                        self.eta_label.configure(text=f"ETA: {int(prog.eta)}s")
                elif prog.status in ("finished", "stopped", "error"):
                    if prog.status == "finished":
                        self.progress_bar.set(1)
                        self.percent_label.configure(text="100%")
        except queue.Empty:
            pass
        finally:
            self.after(120, self._drain_progress_queue)

    # ------------------------------------------------------------
    # UI state
    # ------------------------------------------------------------
    def _set_ui_running(self, running: bool):
        """Disable inputs while downloading; handled by single toggle button."""
        if running:
            self.url_entry.configure(state="disabled")
            self.folder_entry.configure(state="disabled")
        else:
            self.url_entry.configure(state="normal")
            self.folder_entry.configure(state="normal")
            # Reset Start button appearance if the job finished naturally
            if self.start_btn.cget("text") == "Stop":
                self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color="#00e0a0")


    def _update_status_finished(self):
        """Update footer and restore Start button after job end or stop."""
        if self.stop_event.is_set():
            self.status_var.set("Stopped.") 
        else:
            self.status_var.set("Done.")
        # Always reset button to Start when work completes
        self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color="#00e0a0")

