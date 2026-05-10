#!/usr/bin/env bash
# Build the Linux agent binary into ./agents-dist/.
# Run on a Windows host with `pyinstaller agent/installers/pyinstaller_windows.spec` to produce the .exe.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/agent/installers"
"$ROOT/.venv/bin/pyinstaller" --clean --distpath "$ROOT/agents-dist" pyinstaller_linux.spec
echo "built into $ROOT/agents-dist"
ls -l "$ROOT/agents-dist"
