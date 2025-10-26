
# -*- coding: utf-8 -*-
import os
import sys
import shutil
from datetime import datetime

DEFAULT_BITRATE = "192"

def human_time():
    return datetime.now().strftime("%H:%M:%S")

def ensure_ffmpeg_or_die(root=None):
    if not shutil.which("ffmpeg"):
        # Avoid importing tkinter here to keep utils lightweight.
        # Let caller decide how to show error dialogs.
        msg = "FFmpeg is not on PATH. Install FFmpeg and restart the app."
        if root is not None:
            try:
                from tkinter import messagebox
                messagebox.showerror("FFmpeg missing", msg)
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass
        sys.exit(1)

def default_download_dir():
    home = os.path.expanduser("~")
    dl = os.path.join(home, "Downloads")
    return dl if os.path.isdir(dl) else os.getcwd()

def is_likely_playlist_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ("list=" in u) or ("/playlist" in u) or ("music.youtube.com/playlist" in u)
