#!/usr/bin/env bash
set -euo pipefail

# Bootstraps the YouTube Downloader application on Unix-like systems.
# The script ensures Python is available, creates a virtual environment,
# installs the dependencies and generates a helper script for launching
# the application.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
VENV_DIR="$SCRIPT_DIR/venv"

PYTHON_CMD=""

if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [[ -z "$PYTHON_CMD" ]]; then
    echo "Python 3 is required. Attempting to install with available package managers..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-venv python3-pip
        PYTHON_CMD="python3"
    elif command -v brew >/dev/null 2>&1; then
        brew install python@3.11
        PYTHON_CMD="python3"
    else
        echo "Unable to automatically install Python. Please install Python 3.8+ manually and rerun this script." >&2
        exit 1
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r "$APP_DIR/requirements.txt"

deactivate

cat > "$SCRIPT_DIR/run_app.sh" <<'LAUNCHER'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/venv/bin/activate"
python "$SCRIPT_DIR/app/main.py" "$@"
deactivate
LAUNCHER

chmod +x "$SCRIPT_DIR/run_app.sh"

echo "Installation complete. Run './run_app.sh' to launch the application."
