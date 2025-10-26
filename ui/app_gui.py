# -*- coding: utf-8 -*-
import threading
import queue
from typing import Dict, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.utils import ensure_ffmpeg_or_die, default_download_dir, human_time, DEFAULT_BITRATE
from core.downloader import YTAudioDownloader, DownloadProgress
from PIL import Image


# Color theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")  # or "dark-blue", "green", "blue"

ACCENT = "#00c896"
ROW_BG = "#151515"
ROW_ACTIVE_BG = "#1f2a24"
ROW_ERROR_BG = "#2a1515"

AUDIO_QUALITIES = {
    "128 kbps": "128",
    "192 kbps": "192",
    "256 kbps": "256",
    "320 kbps": "320",
}

VIDEO_QUALITIES = ["480p", "720p", "1080p", "1440p", "2160p (4K)"]


class DownloadRow(ctk.CTkFrame):
    def __init__(self, master, title: str, item_index: Optional[int] = None, item_count: Optional[int] = None):
        super().__init__(master, corner_radius=10, fg_color=ROW_BG)
        self.columnconfigure(1, weight=1)

        self._item_index = item_index
        self._item_count = item_count
        self._title = title or "Preparing…"
        self._active = False

        index_text = self._format_index()
        self.index_label = ctk.CTkLabel(self, text=index_text, width=60, anchor="w", font=("Segoe UI", 13, "bold"))
        self.index_label.grid(row=0, column=0, padx=(14, 8), pady=(10, 0), sticky="w")

        self.title_label = ctk.CTkLabel(self, text=self._title, anchor="w", font=("Segoe UI", 15, "bold"))
        self.title_label.grid(row=0, column=1, pady=(10, 0), sticky="w")

        self.percent_label = ctk.CTkLabel(self, text="0%", anchor="e", font=("Segoe UI", 13))
        self.percent_label.grid(row=0, column=2, padx=(8, 16), pady=(10, 0), sticky="e")

        self.progress_var = ctk.DoubleVar(value=0)
        self.progress_bar = ctk.CTkProgressBar(self, variable=self.progress_var, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=3, padx=16, pady=(6, 4), sticky="ew")

        self.status_label = ctk.CTkLabel(
            self,
            text="Waiting…",
            anchor="w",
            font=("Segoe UI", 12),
            text_color="#b0b0b0",
        )
        self.status_label.grid(row=2, column=0, columnspan=3, padx=16, pady=(0, 12), sticky="w")

    # ------------------------------------------------------------
    def _format_index(self) -> str:
        if self._item_index is None:
            return "•"
        if self._item_count:
            return f"{self._item_index}/{self._item_count}"
        return f"#{self._item_index}"

    def update_meta(self, item_index: Optional[int], item_count: Optional[int]):
        if item_index is not None:
            self._item_index = item_index
        if item_count:
            self._item_count = item_count
        self.index_label.configure(text=self._format_index())

    def set_title(self, title: str):
        if title and title != self._title:
            self._title = title
            self.title_label.configure(text=title)

    def set_active(self, active: bool):
        if self._active == active:
            return
        self._active = active
        self.configure(fg_color=ROW_ACTIVE_BG if active else ROW_BG)

    def mark_error(self):
        self.configure(fg_color=ROW_ERROR_BG)
        self.status_label.configure(text_color="#ff9d9d")

    def mark_complete(self, label: str = "Complete"):
        self.progress_var.set(1.0)
        self.percent_label.configure(text="100%")
        self.status_label.configure(text=label, text_color=ACCENT)
        self.set_active(False)

    def update_progress(self, prog: DownloadProgress):
        if prog.item_index is not None or prog.item_count:
            self.update_meta(prog.item_index, prog.item_count)
        if prog.title:
            self.set_title(prog.title)

        if prog.percent is not None:
            clamped = max(0.0, min(100.0, float(prog.percent)))
            self.progress_var.set(clamped / 100.0)
            self.percent_label.configure(text=f"{clamped:.1f}%")

        status_text = ""
        text_color = "#b0b0b0"

        if prog.status == "downloading":
            status_parts = []
            if prog.percent:
                status_parts.append(f"{prog.percent:.1f}%")
            if prog.eta:
                status_parts.append(f"ETA {int(prog.eta)}s")
            if prog.speed:
                kb = prog.speed / 1024
                status_parts.append(f"{kb:,.0f} KB/s")
            status_text = " • ".join(status_parts) or "Downloading…"
            text_color = "#cfd8dc"

        elif prog.status == "finished":
            if prog.message == "postprocessing":
                status_text = "Converting…"
                text_color = "#cfd8dc"
            elif prog.message == "all_done":
                self.mark_complete("Complete")
                return
            else:
                self.mark_complete("Finished")
                return

        elif prog.status == "stopped":
            status_text = prog.message or "Stopped"
            text_color = "#ffcc80"

        elif prog.status == "error":
            status_text = prog.message or "Error"
            text_color = "#ff9d9d"
            self.mark_error()

        if status_text:
            self.status_label.configure(text=status_text, text_color=text_color)


class DownloadList(ctk.CTkScrollableFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, corner_radius=12, fg_color="#101010", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._rows: Dict[str, DownloadRow] = {}
        self._empty_label = ctk.CTkLabel(
            self,
            text="No downloads yet. Paste a link to begin.",
            text_color="#6f6f6f",
            font=("Segoe UI", 13),
            anchor="w",
        )
        self._empty_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 12))

    def reset(self):
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self._show_placeholder()

    def _show_placeholder(self):
        self._empty_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 12))

    def _hide_placeholder(self):
        self._empty_label.grid_forget()

    def _key_for(self, prog: DownloadProgress) -> str:
        if prog.item_index is not None:
            return f"item-{prog.item_index:04d}"
        return "single"

    def _ensure_row(self, prog: DownloadProgress) -> DownloadRow:
        key = self._key_for(prog)
        row = self._rows.get(key)
        if row is None:
            self._hide_placeholder()
            display_index = prog.item_index
            row = DownloadRow(self, prog.title or "Preparing…", display_index, prog.item_count)
            row.grid(row=len(self._rows), column=0, padx=12, pady=8, sticky="ew")
            self._rows[key] = row
        return row

    def update_from_progress(self, prog: DownloadProgress):
        if prog.message == "all_done":
            if not self._rows:
                self._show_placeholder()
                return
            for row in self._rows.values():
                row.mark_complete("Complete")
            return

        row = self._ensure_row(prog)
        if prog.status == "downloading":
            for key, other_row in self._rows.items():
                other_row.set_active(other_row is row)
            if prog.item_index and prog.item_index > 1:
                prev_key = f"item-{prog.item_index - 1:04d}"
                prev_row = self._rows.get(prev_key)
                if prev_row and prev_row is not row:
                    prev_row.mark_complete("Complete")
        row.update_progress(prog)
        if prog.status in ("error", "stopped"):
            row.set_active(False)

    def mark_all_inactive(self):
        for row in self._rows.values():
            row.set_active(False)




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
        self.activity_history = []
        self._current_total_items: Optional[int] = None

        # UI build
        self._build_ui()
        self._clear_activity()

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

        # Activity + download list
        downloads_card = ctk.CTkFrame(self, fg_color="#111111", corner_radius=14)
        downloads_card.pack(pady=(10, 12), padx=30, fill="both", expand=True)

        header_row = ctk.CTkFrame(downloads_card, fg_color="transparent")
        header_row.pack(fill="x", padx=18, pady=(16, 8))

        self.jobs_title_var = ctk.StringVar(value="Waiting for downloads")
        jobs_title = ctk.CTkLabel(header_row, textvariable=self.jobs_title_var, font=("Segoe UI", 17, "bold"))
        jobs_title.pack(side="left")

        self.activity_label = ctk.CTkLabel(
            header_row,
            text="Ready",
            font=("Segoe UI", 12),
            text_color="#9ba0a5",
            justify="right",
            anchor="e",
        )
        self.activity_label.pack(side="right")

        self.download_list = DownloadList(downloads_card, width=780, height=260)
        self.download_list.pack(fill="both", expand=True, padx=16, pady=(0, 18))

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
        self._clear_activity()
        self._log(f"[{human_time()}] 🚀 Starting download…")
        self.stop_event.clear()
        self.progress_bar.set(0)
        self.progress_var.set(0)
        self.percent_label.configure(text="0%")
        self.download_list.reset()
        self._current_total_items = None
        self.jobs_title_var.set("Preparing download…")

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
        self._add_activity_line(text)

    def _format_log_line(self, text: str) -> str:
        cleaned = text.strip()
        if "] " in cleaned:
            cleaned = cleaned.split("] ", 1)[1]
        if cleaned.lower().startswith("[download] "):
            cleaned = cleaned.split(" ", 1)[1]
        return cleaned

    def _clear_activity(self, message: str = "Ready"):
        self.activity_history = []
        self.activity_label.configure(text=message)

    def _add_activity_line(self, text: str):
        clean = self._format_log_line(text)
        if not clean:
            return
        self.activity_history.append(clean)
        if len(self.activity_history) > 4:
            self.activity_history = self.activity_history[-4:]
        joined = "\n".join(self.activity_history)
        self.activity_label.configure(text=joined or "Ready")

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._add_activity_line(line)
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
                self.download_list.update_from_progress(prog)

                if prog.item_count:
                    if self._current_total_items != prog.item_count:
                        self._current_total_items = prog.item_count
                        if prog.item_count > 1:
                            self.jobs_title_var.set(f"Playlist • {prog.item_count} tracks")
                        else:
                            self.jobs_title_var.set("Single track")
                elif self._current_total_items is None and prog.title:
                    self.jobs_title_var.set("Single track")

                if prog.status == "downloading":
                    pct = max(0, min(100, prog.percent or 0))
                    self.progress_var.set(pct / 100)
                    self.percent_label.configure(text=f"{pct:.1f}%")
                    if prog.eta:
                        self.eta_label.configure(text=f"ETA: {int(prog.eta)}s")
                elif prog.status == "finished":
                    if prog.message == "all_done":
                        self.progress_bar.set(1)
                        self.percent_label.configure(text="100%")
                        self.eta_label.configure(text="ETA: —")
                        self.download_list.mark_all_inactive()
                        self.jobs_title_var.set("Completed")
                    elif prog.message == "postprocessing":
                        self.eta_label.configure(text="ETA: —")
                elif prog.status in ("stopped", "error"):
                    if prog.status == "stopped":
                        self.jobs_title_var.set("Stopped")
                        self.download_list.mark_all_inactive()
                    if prog.status == "error":
                        self.jobs_title_var.set("Error during download")
                    self.eta_label.configure(text="ETA: —")
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

