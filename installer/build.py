"""Utility script to build a distributable installer package for the project."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
ARCHIVE_NAME = "youtube_downloader_installer.zip"
EXECUTABLE_STEM = "youtube-downloader"
EXECUTABLE_NAME = f"{EXECUTABLE_STEM}.exe"

APP_ITEMS = [
    "core",
    "ui",
    "main.py",
    "requirements.txt",
    "README.md",
]

INSTALLER_FILES = [
    Path(__file__).with_name("install.ps1"),
    Path(__file__).with_name("install.sh"),
    Path(__file__).with_name("README.txt"),
]


def copy_app_payload(destination: Path) -> None:
    """Copy the application source into the installer payload directory."""
    app_dir = destination / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    for item in APP_ITEMS:
        src = ROOT / item
        dst = app_dir / Path(item).name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)
        else:
            raise FileNotFoundError(f"Required item '{item}' was not found at {src}")


def build_windows_executable(destination: Path) -> None:
    """Build the Windows executable and copy it into the app payload directory.

    Building a Windows executable requires running this build script on a
    Windows host with PyInstaller installed. A helpful error message is raised
    if those requirements are not met.
    """

    if platform.system() != "Windows":
        raise RuntimeError(
            "Building the Windows executable requires running on Windows. "
            "Re-run this script on a Windows machine with PyInstaller installed."
        )

    try:
        import PyInstaller  # noqa: F401  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "PyInstaller is required to build the Windows executable. "
            "Install it with 'pip install pyinstaller'."
        ) from exc

    with tempfile.TemporaryDirectory() as build_dir:
        build_path = Path(build_dir)
        dist_path = build_path / "dist"
        work_path = build_path / "build"
        spec_path = build_path / "spec"

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--name",
            EXECUTABLE_STEM,
            str(ROOT / "main.py"),
            "--distpath",
            str(dist_path),
            "--workpath",
            str(work_path),
            "--specpath",
            str(spec_path),
        ]

        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError as exc:  # pragma: no cover - subprocess guard
            raise RuntimeError(
                "Failed to execute PyInstaller. Ensure it is installed and available "
                "on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("PyInstaller failed to build the executable.") from exc

        built_exe = dist_path / EXECUTABLE_NAME
        if not built_exe.exists():
            raise RuntimeError(
                "The expected executable was not produced by PyInstaller: "
                f"{built_exe}"
            )

        shutil.copy2(built_exe, destination / EXECUTABLE_NAME)


def copy_installer_scripts(destination: Path) -> None:
    """Copy helper scripts for installing the application."""
    for file_path in INSTALLER_FILES:
        if not file_path.exists():
            raise FileNotFoundError(f"Installer script missing: {file_path}")
        shutil.copy2(file_path, destination / file_path.name)


def build_archive() -> Path:
    """Build a zip archive containing the app payload and installer scripts."""
    DIST_DIR.mkdir(exist_ok=True)
    archive_path = DIST_DIR / ARCHIVE_NAME

    with tempfile.TemporaryDirectory() as tmp_dir:
        build_root = Path(tmp_dir) / "youtube_downloader_installer"
        build_root.mkdir()

        copy_app_payload(build_root)
        app_destination = build_root / "app"
        try:
            build_windows_executable(app_destination)
        except RuntimeError as exc:
            raise RuntimeError(
                "Unable to build the Windows executable: "
                f"{exc}"
            ) from exc

        copy_installer_scripts(build_root)

        shutil.make_archive(
            base_name=str(archive_path.with_suffix("")),
            format="zip",
            root_dir=build_root.parent,
            base_dir=build_root.name,
        )

    return archive_path


def main() -> None:
    archive_path = build_archive()
    print(f"Installer package created at: {archive_path}")


if __name__ == "__main__":
    main()
