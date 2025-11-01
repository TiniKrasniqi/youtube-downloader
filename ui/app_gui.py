# -*- coding: utf-8 -*-
import io
import os
import sys
import threading
import queue
import subprocess
from datetime import datetime
from typing import Dict, Optional, List
from urllib.request import Request, urlopen

import customtkinter as ctk
from tkinter import filedialog, messagebox

from PIL import Image

from core.utils import ensure_ffmpeg_or_die, default_download_dir, human_time, DEFAULT_BITRATE
from core.downloader import DownloadProgress
from core.queue import DownloadManager


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

HISTORY_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".webm")


try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9 fallback
    _RESAMPLE = Image.LANCZOS


_THUMBNAIL_CACHE: Dict[str, Optional[bytes]] = {}
_THUMBNAIL_CACHE_LOCK = threading.Lock()


def _fetch_thumbnail_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None

    with _THUMBNAIL_CACHE_LOCK:
        if url in _THUMBNAIL_CACHE:
            return _THUMBNAIL_CACHE[url]

    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urlopen(request, timeout=8) as response:
            data = response.read()
    except Exception:  # pylint: disable=broad-except
        data = None

    with _THUMBNAIL_CACHE_LOCK:
        _THUMBNAIL_CACHE[url] = data
    return data


class DownloadRow(ctk.CTkFrame):
    def __init__(self, master, title: str, item_index: Optional[int] = None, item_count: Optional[int] = None):
        super().__init__(master, corner_radius=10, fg_color=ROW_BG)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._item_index = item_index
        self._item_count = item_count
        self._title = title or "Preparing…"
        self._active = False
        self._thumbnail_url: Optional[str] = None

        self.thumb_container = ctk.CTkFrame(self, width=72, height=72, fg_color="#1f1f1f", corner_radius=8)
        self.thumb_container.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=8)
        self.thumb_container.grid_propagate(False)

        self.thumbnail_label = ctk.CTkLabel(self.thumb_container, text="🎬", font=("Segoe UI Emoji", 26))
        self.thumbnail_label.place(relx=0.5, rely=0.5, anchor="center")
        self.thumbnail_label.image = None

        self.progress_var = ctk.DoubleVar(value=0)
        self.progress_bar = ctk.CTkProgressBar(self.thumb_container, variable=self.progress_var, height=10)
        self.progress_bar.place(relx=0.5, rely=0.97, anchor="s", relwidth=0.88)

        self.meta_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.meta_frame.grid(row=0, column=1, rowspan=2, padx=(0, 16), pady=(16, 12), sticky="nsew")
        self.meta_frame.columnconfigure(1, weight=1)

        index_text = self._format_index()
        self.index_label = ctk.CTkLabel(
            self.meta_frame,
            text=index_text,
            width=60,
            anchor="w",
            font=("Segoe UI", 13, "bold"),
        )
        self.index_label.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.title_label = ctk.CTkLabel(self.meta_frame, text=self._title, anchor="w", font=("Segoe UI", 15, "bold"))
        self.title_label.grid(row=0, column=1, sticky="w")

        self.percent_label = ctk.CTkLabel(
            self.meta_frame,
            text="",
            anchor="w",
            font=("Segoe UI", 13, "bold"),
            text_color="#d1d9e0",
        )
        self.percent_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.status_label = ctk.CTkLabel(
            self.meta_frame,
            text="Waiting…",
            anchor="w",
            font=("Segoe UI", 12),
            text_color="#b0b0b0",
            wraplength=520,
        )
        self.status_label.grid(row=1, column=1, sticky="w", pady=(6, 0))

    def _set_thumbnail_placeholder(self):
        self.thumbnail_label.configure(text="🎬", image=None)
        self.thumbnail_label.image = None

    def set_thumbnail_url(self, url: Optional[str]):
        if not url:
            self._thumbnail_url = None
            self._set_thumbnail_placeholder()
            return
        if url == self._thumbnail_url:
            return

        self._thumbnail_url = url
        self._set_thumbnail_placeholder()

        def worker():
            data = _fetch_thumbnail_bytes(url)

            def apply():
                if self._thumbnail_url != url:
                    return
                if not data:
                    self._set_thumbnail_placeholder()
                    return
                try:
                    pil_img = Image.open(io.BytesIO(data)).convert("RGBA")
                    pil_img.thumbnail((72, 72), _RESAMPLE)
                except Exception:  # pylint: disable=broad-except
                    self._set_thumbnail_placeholder()
                    return

                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(64, 64))
                self.thumbnail_label.configure(image=ctk_img, text="")
                self.thumbnail_label.image = ctk_img

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

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
        self.percent_label.configure(text="100%", text_color=ACCENT)
        self.status_label.configure(text=label, text_color=ACCENT)
        self.set_active(False)

    def update_progress(self, prog: DownloadProgress):
        if prog.item_index is not None or prog.item_count:
            self.update_meta(prog.item_index, prog.item_count)
        if prog.title:
            self.set_title(prog.title)
        if prog.thumbnail_url:
            self.set_thumbnail_url(prog.thumbnail_url)

        if prog.percent is not None:
            clamped = max(0.0, min(100.0, float(prog.percent)))
            self.progress_var.set(clamped / 100.0)
            self.percent_label.configure(text=f"{clamped:.1f}%", text_color="#d1d9e0")
        elif prog.status == "queued":
            self.percent_label.configure(text="", text_color="#d1d9e0")

        status_text = ""
        text_color = "#b0b0b0"

        if prog.status == "queued":
            status_text = "Queued"
            text_color = "#9e9e9e"
        elif prog.status == "downloading":
            status_parts = []
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
        self._placeholder_text = "No downloads yet. Paste a link to begin."
        self._empty_label = ctk.CTkLabel(
            self,
            text=self._placeholder_text,
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

    def set_placeholder_text(self, text: str, show: bool = False):
        self._placeholder_text = text
        self._empty_label.configure(text=text)
        if show:
            self._show_placeholder()

    def _show_placeholder(self):
        self._empty_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 12))

    def _hide_placeholder(self):
        self._empty_label.grid_forget()

    def _key_for(self, prog: DownloadProgress) -> str:
        if prog.job_id:
            return prog.job_id
        if prog.item_index is not None:
            return f"item-{prog.item_index:04d}"
        return "single"

    def has_rows(self) -> bool:
        return bool(self._rows)

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
        if not prog.job_id:
            if prog.message == "all_done":
                if not self._rows:
                    self._show_placeholder()
                    return
                for row in self._rows.values():
                    row.mark_complete("Complete")
                return

            if prog.status == "error":
                for row in self._rows.values():
                    row.mark_error()
                    row.set_active(False)
                return

            if prog.status == "stopped":
                self.mark_all_inactive()
                return

        row = self._ensure_row(prog)
        if prog.status == "downloading":
            row.set_active(True)
        elif prog.status == "queued":
            row.set_active(False)
        elif prog.status in ("error", "stopped", "finished"):
            row.set_active(False)
        row.update_progress(prog)

    def mark_all_inactive(self):
        for row in self._rows.values():
            row.set_active(False)




class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Donwloader")
        self.geometry("880x620")
        self.minsize(780, 540)

        ensure_ffmpeg_or_die(self)

        # Threading / queue manager
        self.manager = DownloadManager(self._enqueue_log, self._enqueue_progress, max_workers=3)
        self.thread_count_var = ctk.StringVar(value=str(self.manager.max_workers))
        self.thread_menu: Optional[ctk.CTkOptionMenu] = None
        self._thread_menu_ready = False
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.activity_history = []
        self.history_panel = None
        self.history_images: List[ctk.CTkImage] = []
        self.history_context_stack: List[Dict[str, object]] = []
        self._current_total_items: Optional[int] = None
        self._cancel_requested = False

        # UI build
        self._build_ui()
        self._thread_menu_ready = True
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
        self.quality_var = ctk.StringVar(value="192 kbps")  # default

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
            command=self._on_quality_change,
        )
        self.quality_menu.pack(side="left", padx=(8, 0))

        threads_label = ctk.CTkLabel(selects_row, text="Parallel", font=("Segoe UI", 13))
        threads_label.pack(side="left", padx=(20, 6))

        self.thread_menu = ctk.CTkOptionMenu(
            selects_row,
            values=[str(i) for i in range(1, 5)],
            variable=self.thread_count_var,
            corner_radius=10,
            width=120,
            command=self._on_thread_limit_change,
        )
        self.thread_menu.pack(side="left", padx=(8, 0))

        # Activity + download list
        downloads_card = ctk.CTkFrame(self, fg_color="#111111", corner_radius=14)
        downloads_card.pack(pady=(10, 12), padx=30, fill="both", expand=True)

        header_row = ctk.CTkFrame(downloads_card, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(16, 8))

        self.jobs_title_var = ctk.StringVar(value="Waiting for downloads")
        jobs_title = ctk.CTkLabel(header_row, textvariable=self.jobs_title_var, font=("Segoe UI", 17, "bold"))
        jobs_title.pack(side="left")

        self.history_btn = ctk.CTkButton(
            header_row,
            text="History",
            width=110,
            height=34,
            corner_radius=8,
            fg_color=ACCENT,
            hover_color="#00e0a0",
            command=self._show_history,
        )
        self.history_btn.pack(side="right")

        header_divider = ctk.CTkFrame(downloads_card, height=2, fg_color="#1f1f1f")
        header_divider.pack(fill="x", padx=16, pady=(0, 12))

        self.download_list = DownloadList(downloads_card, width=780, height=260)
        self.download_list.pack(fill="both", expand=True, padx=16, pady=(0, 18))

        # Status bar
        self.status_var = ctk.StringVar(value="Ready")
        self.status_label = ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color="#bdbdbd", font=("Segoe UI", 12)
        )
        self.status_label.pack(pady=(5, 10))

        self._update_window_title()




    # ------------------------------------------------------------
    # UI events
    # ------------------------------------------------------------


    def _update_window_title(self):
        """Reflect the current format/quality in the window title."""
        format_choice = self.format_var.get()
        if format_choice == "Audio":
            quality_label = self.quality_var.get()
            if quality_label.startswith("MP3 "):
                quality_label = quality_label[4:]
            if quality_label not in AUDIO_QUALITIES:
                quality_label = f"{DEFAULT_BITRATE} kbps"
            self.title(f"YouTube → MP3 ({quality_label})")
        else:
            quality_label = self.quality_var.get()
            if quality_label not in VIDEO_QUALITIES:
                quality_label = "720p"
            self.title(f"YouTube → MP4 ({quality_label})")


    def _choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.out_dir_var.get() or default_download_dir())
        if chosen:
            self.out_dir_var.set(chosen)

    def _find_thumbnail(self, file_path: str) -> Optional[str]:
        base, _ = os.path.splitext(file_path)
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = base + ext
            if os.path.exists(candidate):
                return candidate
        return None

    def _gather_history(self, directory: str, include_folders: bool = True):
        entries = []
        try:
            for entry in os.scandir(directory):
                path = entry.path
                try:
                    if entry.is_file() and entry.name.lower().endswith(HISTORY_EXTENSIONS):
                        try:
                            mtime = entry.stat().st_mtime
                        except OSError:
                            mtime = 0
                        entries.append(
                            {
                                "type": "file",
                                "name": entry.name,
                                "path": path,
                                "mtime": mtime,
                                "thumbnail": self._find_thumbnail(path),
                            }
                        )
                    elif include_folders and entry.is_dir():
                        children = self._gather_history(path, include_folders=False)
                        if not children:
                            continue
                        try:
                            folder_mtime = entry.stat().st_mtime
                        except OSError:
                            folder_mtime = 0
                        latest_child_mtime = max((child.get("mtime") or 0) for child in children) if children else 0
                        entries.append(
                            {
                                "type": "folder",
                                "name": entry.name,
                                "path": path,
                                "mtime": max(folder_mtime, latest_child_mtime),
                                "count": len(children),
                            }
                        )
                except OSError:
                    continue
        except OSError:
            return []
        entries.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
        return entries

    def _format_timestamp(self, mtime: Optional[float]) -> str:
        if not mtime:
            return ""
        try:
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return ""

    def _render_history_entries(self, parent: ctk.CTkScrollableFrame, entries: List[Dict], allow_folders: bool, image_store: List[ctk.CTkImage]):
        for entry in entries:
            row = ctk.CTkFrame(parent, fg_color="#151515", corner_radius=10)
            row.grid_columnconfigure(1, weight=1)
            row.pack(fill="x", padx=4, pady=6)

            thumb_label = None
            thumb_container = None
            thumb_path = entry.get("thumbnail")
            if thumb_path:
                try:
                    with Image.open(thumb_path) as img:
                        pil_img = img.convert("RGBA").copy()
                    ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(60, 60))
                    image_store.append(ctk_img)
                    thumb_label = ctk.CTkLabel(row, image=ctk_img, text="")
                    thumb_label.image = ctk_img
                    thumb_label.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
                except Exception:
                    thumb_label = None

            if thumb_label is None:
                thumb_container = ctk.CTkFrame(row, width=60, height=60, fg_color="#1c1c1c", corner_radius=8)
                thumb_container.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
                thumb_container.grid_propagate(False)
                icon_text = "📁" if entry.get("type") == "folder" else "🎵"
                icon_label = ctk.CTkLabel(thumb_container, text=icon_text, font=("Segoe UI Emoji", 28))
                icon_label.place(relx=0.5, rely=0.5, anchor="center")

            title_prefix = "📁 " if entry.get("type") == "folder" else ""
            title_label = ctk.CTkLabel(
                row,
                text=f"{title_prefix}{entry.get('name', '')}",
                font=("Segoe UI", 14, "bold"),
                anchor="w",
            )
            title_label.grid(row=0, column=1, sticky="w", padx=(0, 6), pady=(10, 0))

            meta_parts = []
            timestamp = self._format_timestamp(entry.get("mtime"))
            if timestamp:
                meta_parts.append(timestamp)
            if entry.get("type") == "folder" and entry.get("count"):
                meta_parts.append(f"{entry['count']} tracks")
            meta_text = " • ".join(meta_parts) if meta_parts else ""
            meta_label = ctk.CTkLabel(
                row,
                text=meta_text,
                font=("Segoe UI", 12),
                text_color="#8f8f8f",
                anchor="w",
            )
            meta_label.grid(row=1, column=1, sticky="w", padx=(0, 6), pady=(0, 10))

            if entry.get("type") == "file":
                play_btn = ctk.CTkButton(
                    row,
                    text="Play",
                    width=70,
                    command=lambda p=entry.get("path"): self._play_history_entry(p),
                    fg_color=ACCENT,
                    hover_color="#00e0a0",
                )
                play_btn.grid(row=0, column=2, rowspan=2, padx=(6, 12), pady=12)

            if entry.get("type") == "folder" and allow_folders:
                def open_folder(_event, p=entry.get("path"), n=entry.get("name")):
                    self._open_history_folder(p, n)

                clickable_widgets = [row, title_label, meta_label]
                if thumb_label is not None:
                    clickable_widgets.append(thumb_label)
                elif thumb_container is not None:
                    clickable_widgets.extend([thumb_container])

                for widget in clickable_widgets:
                    widget.bind("<Button-1>", open_folder)
                    try:
                        widget.configure(cursor="hand2")
                    except Exception:
                        pass

                def on_enter(_event, target=row):
                    target.configure(fg_color=ROW_ACTIVE_BG)

                def on_leave(_event, target=row):
                    target.configure(fg_color="#151515")

                row.bind("<Enter>", on_enter)
                row.bind("<Leave>", on_leave)

    def _play_history_entry(self, file_path: str):
        if not file_path:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(file_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
        except Exception as exc:
            messagebox.showerror("Play file", f"Could not open this file.\n\n{exc}")

    def _ensure_history_panel(self):
        if self.history_panel is not None:
            return

        panel = ctk.CTkFrame(self, fg_color="#090909", corner_radius=18)
        panel.place_forget()
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        header_row.grid_columnconfigure(1, weight=1)

        self.history_back_btn = ctk.CTkButton(
            header_row,
            text="← Back",
            width=90,
            command=self._on_history_back,
            state="disabled",
            fg_color="#2b2b2b",
            hover_color="#3a3a3a",
        )
        self.history_back_btn.grid(row=0, column=0, padx=(0, 12))

        self.history_title_var = ctk.StringVar(value="Download history")
        title_label = ctk.CTkLabel(header_row, textvariable=self.history_title_var, font=("Segoe UI", 20, "bold"))
        title_label.grid(row=0, column=1, sticky="w")

        close_btn = ctk.CTkButton(
            header_row,
            text="Close",
            width=80,
            command=self._hide_history_panel,
            fg_color="#2b2b2b",
            hover_color="#3a3a3a",
        )
        close_btn.grid(row=0, column=2)

        self.history_dir_var = ctk.StringVar(value="")
        dir_label = ctk.CTkLabel(
            panel,
            textvariable=self.history_dir_var,
            font=("Segoe UI", 12),
            text_color="#b0b0b0",
            justify="left",
            anchor="w",
            wraplength=760,
        )
        dir_label.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))

        self.history_list_frame = ctk.CTkScrollableFrame(panel, fg_color="#101010")
        self.history_list_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))

        self.history_panel = panel

    def _populate_history_panel(self, entries: List[Dict], allow_folders: bool):
        for child in self.history_list_frame.winfo_children():
            child.destroy()
        self.history_images.clear()

        if not entries:
            empty_text = (
                "No saved songs yet. Start a download to build your history."
                if allow_folders
                else "No audio files detected in this folder."
            )
            empty_label = ctk.CTkLabel(
                self.history_list_frame,
                text=empty_text,
                text_color="#9e9e9e",
                font=("Segoe UI", 13),
                wraplength=420,
                justify="left",
            )
            empty_label.pack(padx=12, pady=12, anchor="w")
            return

        self._render_history_entries(
            self.history_list_frame,
            entries,
            allow_folders=allow_folders,
            image_store=self.history_images,
        )

    def _show_history_panel(self):
        if not self.history_panel:
            return
        self.history_panel.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.history_panel.lift()

    def _hide_history_panel(self):
        if not self.history_panel:
            return
        self.history_panel.place_forget()
        self.history_context_stack = []

    def _load_history_context(self, context: Dict[str, object]):
        self._ensure_history_panel()
        directory = (context.get("dir") or "").strip()
        allow_folders = bool(context.get("allow_folders"))
        title = context.get("title") or "History"

        self.history_title_var.set(title)
        if directory:
            self.history_dir_var.set(f"Folder: {directory}")
        else:
            self.history_dir_var.set("")

        entries = self._gather_history(directory, include_folders=allow_folders)
        self._populate_history_panel(entries, allow_folders)

        if len(self.history_context_stack) > 1:
            self.history_back_btn.configure(state="normal", fg_color=ACCENT, hover_color="#00e0a0")
        else:
            self.history_back_btn.configure(state="disabled", fg_color="#2b2b2b", hover_color="#3a3a3a")

    def _on_history_back(self):
        if len(self.history_context_stack) <= 1:
            return
        self.history_context_stack.pop()
        self._load_history_context(self.history_context_stack[-1])

    def _open_history_folder(self, folder_path: str, folder_name: str):
        self._ensure_history_panel()
        context = {
            "title": f"📁 {folder_name}",
            "dir": folder_path,
            "allow_folders": False,
        }
        self.history_context_stack.append(context)
        self._load_history_context(context)

    def _show_history(self):
        directory = (self.out_dir_var.get() or "").strip() or default_download_dir()

        if not os.path.isdir(directory):
            messagebox.showinfo("History", "The selected folder does not exist yet.")
            return

        self._ensure_history_panel()

        root_context = {
            "title": "Download history",
            "dir": directory,
            "allow_folders": True,
        }
        self.history_context_stack = [root_context]
        self._load_history_context(root_context)
        self._show_history_panel()

    def _start_audio_queue(self, url: str, out_dir: str, bitrate: str):
        def runner():
            try:
                self.manager.start_audio(url, out_dir, bitrate)
            except Exception as exc:  # pylint: disable=broad-except
                self._enqueue_log(f"[{human_time()}] 💥 Failed to start audio download: {exc}")
                self._enqueue_progress(DownloadProgress(status="error", message=str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def _start_video_queue(self, url: str, out_dir: str, quality: str):
        def runner():
            try:
                self.manager.start_video(url, out_dir, quality)
            except Exception as exc:  # pylint: disable=broad-except
                self._enqueue_log(f"[{human_time()}] 💥 Failed to start video download: {exc}")
                self._enqueue_progress(DownloadProgress(status="error", message=str(exc)))

        threading.Thread(target=runner, daemon=True).start()



    def _on_start(self):
        format_choice = self.format_var.get()
        quality_choice = self.quality_var.get()
        self._update_window_title()

        url = (self.url_entry.get() or "").strip()
        out_dir = (self.out_dir_var.get() or "").strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please paste a YouTube URL.")
            return
        if not out_dir:
            messagebox.showwarning("Missing Folder", "Please choose a destination folder.")
            return

        if self.manager.has_active_jobs():
            messagebox.showinfo("Busy", "Downloads are already running. Please wait.")
            return

        self._cancel_requested = False
        self._set_ui_running(True)
        self._clear_activity()
        self._log(f"[{human_time()}] 🚀 Starting download…")
        self.download_list.reset()
        self.download_list.set_placeholder_text("Fetching data…", show=True)
        self._current_total_items = None
        self.jobs_title_var.set("Fetching data…")

        if format_choice == "Audio":
            bitrate = AUDIO_QUALITIES.get(quality_choice, DEFAULT_BITRATE)
            self.status_var.set(f"Audio: {quality_choice}")
            self._start_audio_queue(url, out_dir, bitrate)
        else:
            self.status_var.set(f"Video: {quality_choice}")
            self._start_video_queue(url, out_dir, quality_choice)



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

        self._update_window_title()

    def _on_quality_change(self, choice: str):
        self._update_window_title()



    def _on_thread_limit_change(self, choice: str):
        if not self._thread_menu_ready:
            self.thread_count_var.set(str(self.manager.max_workers))
            return

        try:
            requested = int(choice)
        except (TypeError, ValueError):
            return

        if self.manager.has_active_jobs():
            messagebox.showinfo("Busy", "Please wait for active downloads to finish before changing the limit.")
            self.thread_count_var.set(str(self.manager.max_workers))
            return

        if not self.manager.set_max_workers(requested):
            self.thread_count_var.set(str(self.manager.max_workers))
            return

        self.thread_count_var.set(str(self.manager.max_workers))
        self._log(f"[{human_time()}] ⚙️ Parallel downloads limit set to {self.manager.max_workers}.")


    def _on_stop(self):
        if self.manager.has_active_jobs():
            self._cancel_requested = True
            self.manager.stop_all()
            threading.Thread(target=self._wait_for_manager, daemon=True).start()
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
    def _wait_for_manager(self):
        self.manager.wait_for_current_jobs()

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

    def _add_activity_line(self, text: str):
        clean = self._format_log_line(text)
        if not clean:
            return
        self.activity_history.append(clean)
        if len(self.activity_history) > 4:
            self.activity_history = self.activity_history[-4:]

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

                if prog.item_count and prog.job_id:
                    if self._current_total_items != prog.item_count:
                        self._current_total_items = prog.item_count
                        if prog.item_count > 1:
                            self.jobs_title_var.set(f"Playlist • {prog.item_count} tracks")
                        else:
                            self.jobs_title_var.set("Single track")
                elif self._current_total_items is None and prog.title:
                    self.jobs_title_var.set("Single track")

                if prog.job_id is None:
                    if prog.item_count and prog.item_count > 1:
                        self.jobs_title_var.set(f"Playlist • {prog.item_count} tracks")

                    if prog.status == "finished" and prog.message == "all_done":
                        self.download_list.mark_all_inactive()
                        self.jobs_title_var.set("Download Completed")
                        if not self.download_list.has_rows():
                            self.download_list.set_placeholder_text("Download completed.", show=True)
                        self._on_downloads_complete("finished")
                    elif prog.status == "stopped":
                        self.jobs_title_var.set("Stopped")
                        self.download_list.mark_all_inactive()
                        self._on_downloads_complete("cancelled")
                    elif prog.status == "error":
                        self.jobs_title_var.set("Error during download")
                        self._on_downloads_complete("error")
                else:
                    if prog.status == "error":
                        self.jobs_title_var.set("Error during download")
                    elif prog.status == "stopped" and not self._cancel_requested:
                        self.jobs_title_var.set("Stopped item")
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
            if self.thread_menu:
                self.thread_menu.configure(state="disabled")
        else:
            self.url_entry.configure(state="normal")
            self.folder_entry.configure(state="normal")
            if self.thread_menu:
                self.thread_menu.configure(state="normal")
            # Reset Start button appearance if the job finished naturally
            if self.start_btn.cget("text") == "Stop":
                self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color="#00e0a0")


    def _on_downloads_complete(self, result: str):
        """Restore UI after the queue finishes."""
        self._cancel_requested = result == "cancelled"
        self._set_ui_running(False)
        if result == "finished":
            self.status_var.set("Done.")
        elif result == "error":
            self.status_var.set("Error.")
        else:
            self.status_var.set("Stopped.")
        self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color="#00e0a0")

