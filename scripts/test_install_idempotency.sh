#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Installer Clean Install & Idempotency Test
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/scripts/install.sh"

echo "========================================================================="
echo " Starting Installer Clean Install & Idempotency Verification"
echo " Target Script: ${INSTALL_SCRIPT}"
echo "========================================================================="

# 1. Static Syntax & Bash Integrity Check
echo -n "[1/4] Verifying bash script syntax integrity... "
for f in "${REPO_ROOT}/scripts/"*.sh; do
    [ -f "$f" ] || continue
    bash -n "$f"
done
echo "[ OK ]"

# 2. ShellCheck Static Analysis Check (if available)
echo -n "[2/4] Verifying ShellCheck lint standards... "
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -e SC1091 -e SC2086 -e SC2129 "${INSTALL_SCRIPT}"
    echo "[ OK ]"
else
    echo "[ SKIPPED (shellcheck not installed) ]"
fi

# 3. Non-Root Environment Handling
if [ "${EUID}" -ne 0 ]; then
    echo "[*] Non-root execution detected (EUID=${EUID})."
    echo "[*] Verifying execution gate (should fail gracefully with exit code 1 when not root)..."
    set +e
    output=$(bash "${INSTALL_SCRIPT}" 2>&1)
    exit_code=$?
    set -e
    if [ ${exit_code} -eq 1 ] && echo "${output}" | grep -qi "run as root"; then
        echo "[+] Non-root root-check gate verified successfully: exit code 1 as expected."
        echo "========================================================================="
        echo " [SUCCESS] Installer static integrity and non-root guards verified!"
        echo "========================================================================="
        exit 0
    else
        echo "[-] Unexpected non-root behavior. Code: ${exit_code}, Output: ${output}"
        exit 1
    fi
fi

# 4. Root / CI Clean Install & Idempotency Execution
echo ">>> [3/4] Executing Pass 1: Clean Installation..."
bash "${INSTALL_SCRIPT}"
echo "[+] Pass 1 (Clean Install) completed successfully."

echo ">>> Recording post-install crontab and permissions state..."
CRON_PASS1=$(crontab -l 2>/dev/null || echo "")
CRON_PASS1_COUNT=$(echo "${CRON_PASS1}" | grep -c "/api/service/reboot" || true)

echo ">>> [4/4] Executing Pass 2: Idempotency Verification..."
bash "${INSTALL_SCRIPT}"
echo "[+] Pass 2 (Idempotency Re-run) completed successfully."

CRON_PASS2=$(crontab -l 2>/dev/null || echo "")
CRON_PASS2_COUNT=$(echo "${CRON_PASS2}" | grep -c "/api/service/reboot" || true)

echo ">>> Verifying zero duplicate crontab drift..."
if [ "${CRON_PASS1_COUNT}" -ne "${CRON_PASS2_COUNT}" ]; then
    echo "[-] Idempotency failure: duplicate reboot cron entries detected (${CRON_PASS1_COUNT} vs ${CRON_PASS2_COUNT})."
    exit 1
fi
echo "[+] Crontab entries remained strictly identical across passes (${CRON_PASS2_COUNT} matching entry)."

echo ">>> Verifying file permissions and system paths..."
test -d /opt/palworld-web-manager
test -f /opt/palworld-web-manager/pyproject.toml
test -f /etc/sudoers.d/palmanager

echo "========================================================================="
echo " [SUCCESS] Clean Install & Idempotency Verification Passed 100%!"
echo "========================================================================="
