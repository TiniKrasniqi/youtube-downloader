
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Callable, Optional
import os
import threading

import yt_dlp
from yt_dlp.utils import DownloadError, DownloadCancelled

from .utils import human_time, DEFAULT_BITRATE, is_likely_playlist_url


@dataclass
class DownloadProgress:
    status: str = ""         # 'downloading', 'finished', 'error', 'stopped'
    percent: float = 0.0
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0       # bytes/sec
    eta: Optional[int] = None
    message: str = ""
    title: str = ""
    item_index: Optional[int] = None
    item_count: Optional[int] = None


class YTDLogger:
    """Forward yt-dlp log lines to UI."""
    def __init__(self, emit_cb: Callable[[str], None]):
        self.emit = emit_cb

    def debug(self, msg):
        m = str(msg)
        if m.strip():
            self.emit(f"[{human_time()}] {m}")

    def warning(self, msg):
        self.emit(f"[{human_time()}] ⚠️ {msg}")

    def error(self, msg):
        self.emit(f"[{human_time()}] ❌ {msg}")


class YTAudioDownloader:
    def __init__(self, log_cb: Callable[[str], None], progress_cb: Callable[[DownloadProgress], None], stop_event: threading.Event):
        self.log = log_cb
        self.progress = progress_cb
        self.stop_event = stop_event
        self._last_title: str = ""
        self._last_item_index: Optional[int] = None
        self._last_item_count: Optional[int] = None

    def _progress_hook(self, d):
        if self.stop_event.is_set():
            raise DownloadCancelled("User requested stop.")

        status = d.get("status")
        info = d.get("info_dict") or {}
        title = info.get("track") or info.get("title") or info.get("alt_title") or info.get("id") or ""
        playlist_index = info.get("playlist_index")
        playlist_count = (
            info.get("playlist_count")
            or info.get("n_entries")
            or d.get("playlist_count")
            or d.get("n_entries")
        )

        if status == "downloading":
            speed = d.get("speed") or 0.0
            eta = d.get("eta")
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total else 0.0

            progress = DownloadProgress(
                status="downloading",
                percent=pct,
                downloaded=int(downloaded),
                total=int(total),
                speed=float(speed),
                eta=eta,
                message="downloading",
                title=title,
                item_index=int(playlist_index) if playlist_index is not None else None,
                item_count=int(playlist_count) if playlist_count else None,
            )
            self._last_title = progress.title
            self._last_item_index = progress.item_index
            self._last_item_count = progress.item_count
            self.progress(progress)

        elif status == "finished":
            self.log(f"[{human_time()}] ✅ Downloaded; converting…")
            progress = DownloadProgress(
                status="finished",
                message="postprocessing",
                percent=100.0,
                title=title,
                item_index=int(playlist_index) if playlist_index is not None else None,
                item_count=int(playlist_count) if playlist_count else None,
            )
            self._last_title = progress.title
            self._last_item_index = progress.item_index
            self._last_item_count = progress.item_count
            self.progress(progress)

    def _build_outtmpl(self, url: str, out_dir: str) -> str:
        # Auto-select template based on URL heuristics (no UI toggle needed)
        if is_likely_playlist_url(url):
            # Put items in a playlist folder; fall back to playlist_id if title missing
            return os.path.join(out_dir, "%(playlist_title,playlist_id)s", "%(playlist_index)03d - %(title)s.%(ext)s")
        else:
            # Single: flat filename in chosen folder
            return os.path.join(out_dir, "%(title)s.%(ext)s")

    def build_opts(self, url: str, out_dir: str, bitrate: str = DEFAULT_BITRATE):
        outtmpl = self._build_outtmpl(url, out_dir)
        return {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate},
            ],
            # Do not ignore errors so they surface in the UI instead of reporting
            # a successful download despite failures.
            "retries": 3,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "lazy_playlist": True,
            "logger": YTDLogger(self.log),
            "progress_hooks": [self._progress_hook],
        }

    def download(self, url: str, out_dir: str, bitrate: str = DEFAULT_BITRATE):
        opts = self.build_opts(url, out_dir, bitrate)
        mode = "Auto"
        self.log(f"[{human_time()}] ▶ Starting ({mode}) → MP3 {bitrate} kbps")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")

        try:
            self.progress(DownloadProgress(status="downloading", message="preparing"))
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎵 Finished successfully.")
                self.progress(DownloadProgress(
                    status="finished",
                    message="all_done",
                    percent=100.0,
                    title=self._last_title,
                    item_index=self._last_item_index,
                    item_count=self._last_item_count,
                ))

        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
            self.progress(DownloadProgress(status="stopped", message=str(e)))

        except DownloadError as e:
            self.log(f"[{human_time()}] ❌ Download error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e)))

        except Exception as e:
            self.log(f"[{human_time()}] 💥 Unexpected error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e)))

    def download_video(self, url: str, out_dir: str, quality: str = "720p"):
        opts = self.build_video_opts(url, out_dir, quality)
        self.log(f"[{human_time()}] ▶ Starting (Video) → MP4 {quality}")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎥 Finished successfully.")
                self.progress(DownloadProgress(
                    status="finished",
                    message="all_done",
                    percent=100.0,
                    title=self._last_title,
                    item_index=self._last_item_index,
                    item_count=self._last_item_count,
                ))
        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
            self.progress(DownloadProgress(status="stopped", message=str(e)))
        except Exception as e:
            self.log(f"[{human_time()}] 💥 Video error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e)))


    def build_video_opts(self, url: str, out_dir: str, quality: str = "720p"):
        """Build yt-dlp options for video downloads."""
        outtmpl = self._build_outtmpl(url, out_dir)
        # map quality text to resolution cap
        # map quality text to resolution cap
        height_map = {
            "480p": "480",
            "720p": "720",
            "1080p": "1080",
            "1440p": "1440",
            "2160p": "2160",
            "2160p (4K)": "2160",
        }
        height = height_map.get(quality, "720")

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": f"bestvideo[height<={height}]+bestaudio/best",
            "merge_output_format": "mp4",
            # Surface errors to the caller so the UI can react appropriately.
            "retries": 10,
            "continuedl": True,
            "noprogress": True,
            "logger": YTDLogger(self.log),
            "progress_hooks": [self._progress_hook],
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
        }
        return ydl_opts

