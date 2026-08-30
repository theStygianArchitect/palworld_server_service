#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Palworld Unified Operations Suite - Zero-Drift Deployer
# ==============================================================================

TARGET_BRANCH="${1:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="/opt/palworld-web-manager"
APP_USER="palmanager"

echo "========================================================================="
echo " Deploying Palworld Operations Suite"
echo " Target Branch: ${TARGET_BRANCH}"
echo "========================================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Please run as root or with sudo: sudo ./scripts/deploy.sh [branch]"
    exit 1
fi

cd "${REPO_ROOT}"

echo -n "[1/4] Pulling latest updates from origin/${TARGET_BRANCH}... "
git fetch origin "${TARGET_BRANCH}" > /dev/null 2>&1
git checkout "${TARGET_BRANCH}" > /dev/null 2>&1
git pull origin "${TARGET_BRANCH}" > /dev/null 2>&1
echo "[ OK ]"

echo -n "[2/4] Syncing application code to ${APP_DIR}... "
cp -r "${REPO_ROOT}/app" "${APP_DIR}/"
cp "${REPO_ROOT}/pyproject.toml" "${APP_DIR}/"
if [ -f "${REPO_ROOT}/README.md" ]; then
    cp "${REPO_ROOT}/README.md" "${APP_DIR}/"
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
echo "[ OK ]"

echo -n "[3/4] Updating Python dependencies via uv... "
cd "${APP_DIR}"
su -s /bin/bash "${APP_USER}" -c "uv pip install --python .venv/bin/python -r <(uv pip compile pyproject.toml) > /dev/null 2>&1 || uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic httpx websockets psutil > /dev/null"
echo "[ OK ]"

echo -n "[4/4] Restarting palworld-manager.service... "
systemctl restart palworld-manager.service
echo "[ OK ]"

echo "========================================================================="
echo " Deployment Complete! Service status: $(systemctl is-active palworld-manager.service)"
echo "========================================================================="
