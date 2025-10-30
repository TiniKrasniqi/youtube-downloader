YouTube Downloader Installer
============================

This package bundles the application source code together with helper scripts
that automate the installation process on both Windows and Unix-like systems.

Building the installer
----------------------
1. Ensure you have Python 3.8 or newer available.
2. From the project root, run:
       python installer/build.py
3. The archive "dist/youtube_downloader_installer.zip" will be created.

Using the installer
-------------------
1. Distribute or extract the generated ZIP file on the target machine.
2. Run the installer script appropriate for the platform:
   * Windows:  Right-click `install.ps1` and choose "Run with PowerShell". The
     script can install Python automatically if it is not already available.
   * Linux/macOS:  Execute `bash install.sh` from a terminal. The script will
     attempt to install Python using `apt` or Homebrew if those package
     managers are detected.
3. After the installer finishes, use the generated `run_app` helper script to
   launch the program:
   * Windows:  `./run_app.ps1`
   * Linux/macOS:  `./run_app.sh`

Both installers create an isolated virtual environment to avoid interfering
with any existing Python installations on the system.
