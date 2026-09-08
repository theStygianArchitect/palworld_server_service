#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Standalone Single Binary Compiler
# ==============================================================================
# Compiles app/main.py and all dependencies into a standalone single ELF binary
# using PyInstaller. Eliminates runtime Python, package managers, and virtualenvs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build"

cd "${REPO_ROOT}"

echo "========================================================================="
echo " Palworld Operations Suite - Standalone ELF Binary Compiler"
echo " Target Output: ${DIST_DIR}/palworld-manager"
echo "========================================================================="

mkdir -p "${DIST_DIR}"
mkdir -p "${BUILD_DIR}"

if command -v uv >/dev/null 2>&1; then
    echo ">>> Compiling binary via uv and PyInstaller..."
    uv run pyinstaller \
        --name palworld-manager \
        --onefile \
        --clean \
        --add-data "app/templates:app/templates" \
        --add-data "app/static:app/static" \
        --hidden-import "uvicorn.logging" \
        --hidden-import "uvicorn.loops" \
        --hidden-import "uvicorn.loops.auto" \
        --hidden-import "uvicorn.protocols" \
        --hidden-import "uvicorn.protocols.http" \
        --hidden-import "uvicorn.protocols.http.auto" \
        --hidden-import "uvicorn.protocols.websockets" \
        --hidden-import "uvicorn.protocols.websockets.auto" \
        --hidden-import "uvicorn.lifespan" \
        --hidden-import "uvicorn.lifespan.on" \
        --hidden-import "engineio.async_drivers.asgi" \
        --distpath "${DIST_DIR}" \
        --workpath "${BUILD_DIR}" \
        app/main.py
elif command -v python3 >/dev/null 2>&1; then
    echo ">>> Compiling binary via python3 and PyInstaller..."
    python3 -m pip install --quiet pyinstaller
    python3 -m PyInstaller \
        --name palworld-manager \
        --onefile \
        --clean \
        --add-data "app/templates:app/templates" \
        --add-data "app/static:app/static" \
        --hidden-import "uvicorn.logging" \
        --hidden-import "uvicorn.loops" \
        --hidden-import "uvicorn.loops.auto" \
        --hidden-import "uvicorn.protocols" \
        --hidden-import "uvicorn.protocols.http" \
        --hidden-import "uvicorn.protocols.http.auto" \
        --hidden-import "uvicorn.protocols.websockets" \
        --hidden-import "uvicorn.protocols.websockets.auto" \
        --hidden-import "uvicorn.lifespan" \
        --hidden-import "uvicorn.lifespan.on" \
        --hidden-import "engineio.async_drivers.asgi" \
        --distpath "${DIST_DIR}" \
        --workpath "${BUILD_DIR}" \
        app/main.py
else
    echo "[-] Error: Neither uv nor python3 is available to compile the standalone binary." >&2
    exit 1
fi

echo "========================================================================="
echo " [SUCCESS] Standalone binary compiled: ${DIST_DIR}/palworld-manager"
echo "========================================================================="
