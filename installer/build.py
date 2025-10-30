"""Utility script to build a distributable installer package for the project."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
ARCHIVE_NAME = "youtube_downloader_installer.zip"

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
