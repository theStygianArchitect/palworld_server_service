#!/usr/bin/env bash
# ==============================================================================
# Palworld Operations Suite - Quality Script Forwarding Wrapper
# ==============================================================================
# Dispatches execution directly to quality_check.sh with identical arguments.
# Ensures compatibility with both `./quality_script.sh` and `./quality_check.sh`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUALITY_RUNNER="${SCRIPT_DIR}/quality_check.sh"

if [ ! -f "${QUALITY_RUNNER}" ]; then
    echo "[-] Error: Quality runner script not found at ${QUALITY_RUNNER}" >&2
    exit 1
fi

exec "${QUALITY_RUNNER}" "$@"
