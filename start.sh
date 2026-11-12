#!/usr/bin/env bash

# Set terminal window title
echo -ne "\033]0;LMS+ Application\007"
echo "Starting LMS+ Application..."

# Ensure uv directory is in PATH even when launched from file manager
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Change to the script's directory
cd "$(dirname "$0")"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed or not in PATH."
    read -p "Press Enter to continue..."
    exit 1
fi

# Auto-create .venv if missing
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment with uv..."
    uv venv
    if [ -f "requirements.txt" ]; then
        echo "Installing requirements..."
        uv pip install -r requirements.txt
    fi
fi

# Run the application using the venv python
./.venv/bin/python run.py

# Keep terminal open on error/exit
read -p "Press Enter to continue..."
