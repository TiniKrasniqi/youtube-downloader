# -*- coding: utf-8 -*-
"""Queue controller for resolving playlists and running downloads in parallel."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

import yt_dlp

from .downloader import DownloadProgress, YTAudioDownloader
from .utils import human_time


ProgressCallback = Callable[[DownloadProgress], None]
LogCallback = Callable[[str], None]


@dataclass
class QueueEntry:
    """Represents a single resolved item from a playlist or individual URL."""

    url: str
    title: str = ""
    index: Optional[int] = None
    total: Optional[int] = None


def _normalise_entry_url(entry: Dict[str, Any]) -> Optional[str]:
    url = entry.get("webpage_url") or entry.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url

    video_id = entry.get("id")
    if isinstance(video_id, str):
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def resolve_entries(url: str, log: Optional[LogCallback] = None) -> List[QueueEntry]:
    """Return queue entries for a URL, flattening playlists to individual items."""

    opts = {
        "skip_download": True,
        "extract_flat": True,
        "quiet": True,
        "lazy_playlist": False,
        "nocheckcertificate": True,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # pylint: disable=broad-except
        if log:
            log(f"[{human_time()}] ⚠️ Failed to resolve playlist: {exc}")
        return []

    entries: List[QueueEntry] = []

    info_type = info.get("_type")
    if info_type in {"playlist", "multi_video"}:
        raw_entries: Iterable[Dict[str, Any]] = info.get("entries") or []
        filtered: List[Dict[str, Any]] = [entry for entry in raw_entries if entry]
        if not filtered:
            return []

        total = len(filtered)
        for idx, entry in enumerate(filtered, start=1):
            resolved_url = _normalise_entry_url(entry)
            if not resolved_url:
                continue
            title = entry.get("title") or ""
            entries.append(QueueEntry(url=resolved_url, title=title, index=idx, total=total))
    else:
        resolved_url = info.get("webpage_url") or info.get("original_url") or url
        title = info.get("title") or ""
        entries.append(QueueEntry(url=resolved_url, title=title, index=1, total=1))

    if log:
        if entries and (entries[0].total or len(entries) > 1):
            count = entries[0].total or len(entries)
            if count and count > 1:
                log(f"[{human_time()}] 📜 Playlist resolved: {count} items")
            else:
                log(f"[{human_time()}] 🎧 Single item detected")
        else:
            log(f"[{human_time()}] 🎧 Single item detected")

    return entries


class DownloadManager:
    """Manage multiple download workers backed by a ThreadPoolExecutor."""

    def __init__(self, log_cb: LogCallback, progress_cb: ProgressCallback, max_workers: int = 3):
        self._log = log_cb
        self._progress = progress_cb
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yt-dl")
        self._lock = threading.Lock()
        self._stop_events: Dict[str, threading.Event] = {}
        self._futures: Dict[str, Future] = {}
        self._active_total: Optional[int] = None
        self._cancel_requested = False
        self._had_errors = False

    # ------------------------------------------------------------------
    def has_active_jobs(self) -> bool:
        with self._lock:
            return bool(self._futures)

    # ------------------------------------------------------------------
    def start_audio(self, url: str, out_dir: str, bitrate: str) -> None:
        self._start_jobs(url, out_dir, bitrate=bitrate, video_quality=None)

    def start_video(self, url: str, out_dir: str, quality: str) -> None:
        self._start_jobs(url, out_dir, bitrate=None, video_quality=quality)

    # ------------------------------------------------------------------
    def _start_jobs(self, url: str, out_dir: str, bitrate: Optional[str], video_quality: Optional[str]) -> None:
        entries = resolve_entries(url, log=self._log)
        if not entries:
            entries = [QueueEntry(url=url, title="", index=1, total=1)]

        total_items = entries[0].total or len(entries)
        self._active_total = total_items
        self._cancel_requested = False
        self._had_errors = False

        for entry in entries:
            job_id = str(uuid.uuid4())
            stop_event = threading.Event()

            self._emit_placeholder(job_id, entry)

            future = self._executor.submit(
                self._run_worker,
                job_id,
                entry,
                out_dir,
                bitrate,
                video_quality,
                stop_event,
            )

            with self._lock:
                self._stop_events[job_id] = stop_event
                self._futures[job_id] = future

            future.add_done_callback(lambda fut, jid=job_id: self._on_future_done(jid, fut))

    # ------------------------------------------------------------------
    def _emit_placeholder(self, job_id: str, entry: QueueEntry) -> None:
        placeholder = DownloadProgress(
            status="queued",
            message="queued",
            percent=0.0,
            title=entry.title or f"Item {entry.index}",
            item_index=entry.index,
            item_count=entry.total,
            job_id=job_id,
        )
        self._progress(placeholder)

    # ------------------------------------------------------------------
    def _run_worker(
        self,
        job_id: str,
        entry: QueueEntry,
        out_dir: str,
        bitrate: Optional[str],
        video_quality: Optional[str],
        stop_event: threading.Event,
    ) -> None:
        def progress_cb(progress: DownloadProgress) -> None:
            if progress.job_id is None:
                progress.job_id = job_id
            if not progress.title and entry.title:
                progress.title = entry.title
            if progress.item_index is None:
                progress.item_index = entry.index
            if progress.item_count is None:
                progress.item_count = entry.total
            if progress.status == "error":
                self._had_errors = True
            self._progress(progress)

        downloader = YTAudioDownloader(self._log, progress_cb, stop_event, job_id=job_id)
        if bitrate is not None:
            downloader.download(entry.url, out_dir, bitrate=bitrate)
        else:
            quality = video_quality or "720p"
            downloader.download_video(entry.url, out_dir, quality=quality)

    # ------------------------------------------------------------------
    def _on_future_done(self, job_id: str, future: Future) -> None:
        exception = future.exception()

        with self._lock:
            self._futures.pop(job_id, None)
            self._stop_events.pop(job_id, None)
            remaining = bool(self._futures)

        if exception and not self._cancel_requested:
            self._log(f"[{human_time()}] 💥 Worker {job_id[:8]} failed: {exception}")

        if not remaining:
            self._emit_terminal_event()

    # ------------------------------------------------------------------
    def _emit_terminal_event(self) -> None:
        if self._cancel_requested:
            terminal = DownloadProgress(status="stopped", message="cancelled", job_id=None)
        elif self._had_errors:
            terminal = DownloadProgress(
                status="error",
                message="one_or_more_failed",
                item_count=self._active_total,
                job_id=None,
            )
        else:
            terminal = DownloadProgress(
                status="finished",
                message="all_done",
                percent=100.0,
                item_count=self._active_total,
                job_id=None,
            )
        self._progress(terminal)

    # ------------------------------------------------------------------
    def stop_all(self) -> None:
        with self._lock:
            self._cancel_requested = True
            events = list(self._stop_events.values())
        for event in events:
            event.set()

    # ------------------------------------------------------------------
    def wait_for_current_jobs(self) -> None:
        with self._lock:
            futures = list(self._futures.values())
        for future in futures:
            try:
                future.result()
            except Exception:  # pylint: disable=broad-except
                continue

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self.stop_all()
        self.wait_for_current_jobs()
        self._executor.shutdown(wait=False, cancel_futures=True)
