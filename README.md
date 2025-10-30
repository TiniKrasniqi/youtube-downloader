# 🎧 YouTube → MP3 Downloader

A polished desktop app for turning YouTube links into high-quality audio or video files with just a couple of clicks. Built with love (and a little help from Codex in ChatGPT) to make collecting playlists and tracks painless.

## ✨ What the app delivers
- **Streamlined downloads** – Paste any YouTube URL and press start. The app handles single videos or full playlists automatically.
- **Audio-first workflow** – Converts to MP3 with embeddable thumbnails, metadata, and selectable bitrates (128–320 kbps) powered by FFmpeg + yt-dlp.
- **Video mode** – Switch to MP4 downloads and choose the resolution that fits your storage or screen (480p up to 4K).
- **Smart file organization** – Playlists get their own folders and tidy filenames so your library stays orderly.
- **Real-time progress** – Modern CustomTkinter UI shows per-item progress, ETA, speed, and detailed logs.
- **Download history browser** – Quickly revisit finished tracks, preview thumbnails, and launch files right from the app.
- **One-click stop & resume** – Toggle between Start/Stop to cancel in-flight downloads gracefully.

## 🖥️ Tech highlights
- **CustomTkinter** for a sleek dark-themed interface with scrollable download rows and dialogs.
- **yt-dlp** under the hood for resilient fetching, playlist awareness, and retry logic.
- **FFmpeg** integration for audio extraction, metadata embedding, and thumbnail support.
- **Python threading** to keep the UI responsive while downloads run in the background.

## 🛠️ Installation & setup
1. **Prerequisites**
   - Python 3.9 or newer.
   - FFmpeg available on your system `PATH`.
   - A working Tk installation (included with most Python distributions on Windows/macOS; install `python3-tk` on many Linux distros).
2. **Clone the project**
   ```bash
   git clone https://github.com/tinikrasniqi/youtube-downloader.git
   cd youtube-downloader
   ```
3. **(Optional) Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
4. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```
5. **Run the app**
   ```bash
   python main.py
   ```

## 📦 Building a distributable installer

You can bundle the application with platform-specific installation helpers by
running the build script:

```bash
python installer/build.py
```

The script produces `dist/youtube_downloader_installer.zip`, which contains the
application source alongside two installer entry points:

- **Windows:** Run `install.ps1`. The script downloads Python if it is missing,
  creates an isolated virtual environment, and installs all Python
  dependencies automatically.
- **Linux/macOS:** Run `install.sh`. The script ensures Python 3 is installed
  (using `apt` or Homebrew when available), provisions a virtual environment,
  and installs the required packages.

After either installer completes, launch the application with the generated
`run_app` helper script (`run_app.ps1` on Windows or `run_app.sh` on
Linux/macOS).

Happy downloading! 🎵