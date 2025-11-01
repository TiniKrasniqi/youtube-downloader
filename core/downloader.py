
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Callable, Optional, Iterator, Tuple, Set
import concurrent.futures
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
        self._thread_state = threading.local()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_thread_state(self) -> dict:
        state = getattr(self._thread_state, "state", None)
        if state is None:
            state = {}
            self._thread_state.state = state
        return state

    def _reset_thread_state(self):
        self._thread_state.state = {}

    def _record_last_progress(self, prog: DownloadProgress):
        state = self._get_thread_state()
        state["last_progress"] = prog

    def _get_last_progress(self) -> Optional[DownloadProgress]:
        state = self._get_thread_state()
        return state.get("last_progress")

    def _handle_progress_event(self, d, *, log_conversion: bool = True):
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
            self._record_last_progress(progress)
            self.progress(progress)

        elif status == "finished":
            if log_conversion:
                self.log(f"[{human_time()}] ✅ Downloaded; converting…")
            progress = DownloadProgress(
                status="finished",
                message="postprocessing",
                percent=100.0,
                title=title,
                item_index=int(playlist_index) if playlist_index is not None else None,
                item_count=int(playlist_count) if playlist_count else None,
            )
            self._record_last_progress(progress)
            self.progress(progress)

    def _make_progress_hook(
        self,
        *,
        item_index: Optional[int] = None,
        item_count: Optional[int] = None,
        initial_title: Optional[str] = None,
        playlist_title: Optional[str] = None,
        playlist_id: Optional[str] = None,
        log_conversion: bool = True,
    ):
        def hook(d):
            info = d.setdefault("info_dict", {})
            if item_index is not None and info.get("playlist_index") is None:
                info["playlist_index"] = item_index
            if item_count is not None:
                info.setdefault("playlist_count", item_count)
                info.setdefault("n_entries", item_count)
            if playlist_title and not info.get("playlist_title"):
                info["playlist_title"] = playlist_title
            if playlist_id and not info.get("playlist_id"):
                info["playlist_id"] = playlist_id
            if initial_title and not info.get("title"):
                info["title"] = initial_title
            self._handle_progress_event(d, log_conversion=log_conversion)

        return hook

    def _build_outtmpl(self, url: str, out_dir: str) -> str:
        # Auto-select template based on URL heuristics (no UI toggle needed)
        if is_likely_playlist_url(url):
            # Put items in a playlist folder; fall back to playlist_id if title missing
            return os.path.join(out_dir, "%(playlist_title,playlist_id)s", "%(playlist_index)03d - %(title)s.%(ext)s")
        else:
            # Single: flat filename in chosen folder
            return os.path.join(out_dir, "%(title)s.%(ext)s")

    def build_opts(
        self,
        url: str,
        out_dir: str,
        bitrate: str = DEFAULT_BITRATE,
        *,
        item_index: Optional[int] = None,
        item_count: Optional[int] = None,
        initial_title: Optional[str] = None,
        playlist_title: Optional[str] = None,
        playlist_id: Optional[str] = None,
        template_url: Optional[str] = None,
        log_conversion: bool = True,
    ):
        outtmpl = self._build_outtmpl(template_url or url, out_dir)
        return {
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
            # Do not ignore errors so they surface in the UI instead of reporting
            # a successful download despite failures.
            "retries": 10,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "logger": YTDLogger(self.log),
            "progress_hooks": [
                self._make_progress_hook(
                    item_index=item_index,
                    item_count=item_count,
                    initial_title=initial_title,
                    playlist_title=playlist_title,
                    playlist_id=playlist_id,
                    log_conversion=log_conversion,
                )
            ],
        }

    def _download_single(
        self,
        url: str,
        out_dir: str,
        bitrate: str,
        *,
        info_dict: Optional[dict] = None,
        item_index: Optional[int] = None,
        item_count: Optional[int] = None,
        initial_title: Optional[str] = None,
        playlist_title: Optional[str] = None,
        playlist_id: Optional[str] = None,
        template_url: Optional[str] = None,
        log_conversion: bool = True,
        emit_final: bool = True,
    ) -> bool:
        self._reset_thread_state()
        opts = self.build_opts(
            url,
            out_dir,
            bitrate,
            item_index=item_index,
            item_count=item_count,
            initial_title=initial_title,
            playlist_title=playlist_title,
            playlist_id=playlist_id,
            template_url=template_url,
            log_conversion=log_conversion,
        )

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                if info_dict is not None:
                    ydl.process_ie_result(info_dict, download=True)
                else:
                    ydl.download([url])

            if self.stop_event.is_set():
                return False

            if emit_final:
                last_progress = self._get_last_progress()
                title = initial_title or ""
                if last_progress and last_progress.title:
                    title = last_progress.title
                final_index = item_index if item_index is not None else (last_progress.item_index if last_progress else None)
                final_count = item_count if item_count is not None else (last_progress.item_count if last_progress else None)
                self.progress(
                    DownloadProgress(
                        status="finished",
                        message="all_done",
                        percent=100.0,
                        title=title,
                        item_index=final_index,
                        item_count=final_count,
                    )
                )

            return True

        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped: {e}")
            self.progress(
                DownloadProgress(
                    status="stopped",
                    message=str(e),
                    title=initial_title or "",
                    item_index=item_index,
                    item_count=item_count,
                )
            )
        except DownloadError as e:
            self.log(f"[{human_time()}] ❌ Download error: {e}")
            self.progress(
                DownloadProgress(
                    status="error",
                    message=str(e),
                    title=initial_title or "",
                    item_index=item_index,
                    item_count=item_count,
                )
            )
        except Exception as e:
            self.log(f"[{human_time()}] 💥 Unexpected error: {e}")
            self.progress(
                DownloadProgress(
                    status="error",
                    message=str(e),
                    title=initial_title or "",
                    item_index=item_index,
                    item_count=item_count,
                )
            )

        return False

    def _download_playlist_concurrent(self, url: str, out_dir: str, bitrate: str) -> bool:
        meta_opts = {
            "quiet": True,
            "skip_download": True,
            "logger": YTDLogger(self.log),
        }

        try:
            with yt_dlp.YoutubeDL(meta_opts) as ydl:
                playlist_info = ydl.extract_info(url, download=False)
        except DownloadCancelled as e:
            self.log(f"[{human_time()}] ⏹ Stopped while preparing playlist: {e}")
            self.progress(DownloadProgress(status="stopped", message=str(e)))
            return True
        except DownloadError as e:
            self.log(f"[{human_time()}] ❌ Playlist error: {e}")
            self.progress(DownloadProgress(status="error", message=str(e)))
            return True
        except Exception as e:
            self.log(f"[{human_time()}] 💥 Failed to prepare playlist: {e}")
            self.progress(DownloadProgress(status="error", message=str(e)))
            return True

        entries = playlist_info.get("entries") or []
        if not entries:
            return False

        playlist_title = (
            playlist_info.get("title")
            or playlist_info.get("playlist_title")
            or playlist_info.get("alt_title")
            or ""
        )
        playlist_id = playlist_info.get("id") or playlist_info.get("playlist_id")
        item_count = (
            playlist_info.get("playlist_count")
            or playlist_info.get("n_entries")
            or (len(entries) if isinstance(entries, (list, tuple)) else None)
        )

        entries_iter: Iterator[dict] = iter(entries)
        max_workers = 3
        error_event = threading.Event()
        fetch_lock = threading.Lock()
        entry_counter = 0

        def fetch_next_entry() -> Optional[Tuple[dict, int]]:
            nonlocal entry_counter
            if self.stop_event.is_set():
                return None
            with fetch_lock:
                while not self.stop_event.is_set():
                    try:
                        entry = next(entries_iter)
                    except StopIteration:
                        return None
                    if entry is None:
                        continue
                    entry_counter += 1
                    index = entry.get("playlist_index") or entry_counter
                    return entry, int(index)
            return None

        def worker(data: Tuple[dict, int]):
            if self.stop_event.is_set():
                return False

            entry, index = data
            entry_dict = dict(entry)
            if item_count is not None:
                entry_dict.setdefault("playlist_count", item_count)
                entry_dict.setdefault("n_entries", item_count)
            entry_dict.setdefault("playlist_index", index)
            if playlist_title:
                entry_dict.setdefault("playlist_title", playlist_title)
            if playlist_id:
                entry_dict.setdefault("playlist_id", playlist_id)

            entry_title = (
                entry_dict.get("track")
                or entry_dict.get("title")
                or entry_dict.get("alt_title")
                or entry_dict.get("id")
                or f"Item {index}"
            )

            if item_count:
                item_label = f"{index}/{item_count}"
            else:
                item_label = str(index)
            self.log(
                f"[{human_time()}] ▶ Starting playlist item {item_label}: {entry_title}"
            )

            self.progress(
                DownloadProgress(
                    status="downloading",
                    percent=0.0,
                    downloaded=0,
                    total=0,
                    message="starting",
                    title=entry_title,
                    item_index=index,
                    item_count=int(item_count) if item_count else None,
                )
            )

            entry_url = (
                entry_dict.get("webpage_url")
                or entry_dict.get("original_url")
                or entry_dict.get("url")
                or url
            )

            success = self._download_single(
                entry_url,
                out_dir,
                bitrate,
                info_dict=entry_dict,
                item_index=index,
                item_count=int(item_count) if item_count else None,
                initial_title=entry_title,
                playlist_title=playlist_title,
                playlist_id=playlist_id,
                template_url=url,
            )

            if not success and not self.stop_event.is_set():
                error_event.set()

            return success

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            active: Set[concurrent.futures.Future] = set()

            def submit_next():
                data = fetch_next_entry()
                if data is None:
                    return False
                future = executor.submit(worker, data)
                active.add(future)
                return True

            for _ in range(max_workers):
                if not submit_next():
                    break

            while active:
                done, _ = concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
                active.difference_update(done)

                if self.stop_event.is_set():
                    for future in active:
                        future.cancel()
                    break

                while len(active) < max_workers and submit_next():
                    pass

        if self.stop_event.is_set():
            return True

        if error_event.is_set():
            return True

        self.log(f"[{human_time()}] 🎵 Playlist completed successfully.")
        self.progress(
            DownloadProgress(
                status="finished",
                message="all_done",
                percent=100.0,
                title=playlist_title or "Playlist",
                item_count=int(item_count) if item_count else None,
            )
        )
        return True

    def download(self, url: str, out_dir: str, bitrate: str = DEFAULT_BITRATE):
        mode = "Auto"
        self.log(f"[{human_time()}] ▶ Starting ({mode}) → MP3 {bitrate} kbps")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")

        if is_likely_playlist_url(url):
            handled = self._download_playlist_concurrent(url, out_dir, bitrate)
            if handled:
                return

        success = self._download_single(url, out_dir, bitrate)
        if success and not self.stop_event.is_set():
            self.log(f"[{human_time()}] 🎵 Finished successfully.")

    def download_video(self, url: str, out_dir: str, quality: str = "720p"):
        self._reset_thread_state()
        opts = self.build_video_opts(url, out_dir, quality)
        self.log(f"[{human_time()}] ▶ Starting (Video) → MP4 {quality}")
        self.log(f"[{human_time()}] 📁 Output: {out_dir}")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not self.stop_event.is_set():
                self.log(f"[{human_time()}] 🎥 Finished successfully.")
                last_progress = self._get_last_progress()
                title = last_progress.title if last_progress else ""
                self.progress(
                    DownloadProgress(
                        status="finished",
                        message="all_done",
                        percent=100.0,
                        title=title,
                        item_index=last_progress.item_index if last_progress else None,
                        item_count=last_progress.item_count if last_progress else None,
                    )
                )
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
            "progress_hooks": [self._make_progress_hook()],
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
        }
        return ydl_opts

