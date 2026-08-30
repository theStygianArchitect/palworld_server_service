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

echo -n "[1/5] Pulling latest updates from origin/${TARGET_BRANCH}... "
git fetch origin "${TARGET_BRANCH}" > /dev/null 2>&1
git checkout "${TARGET_BRANCH}" > /dev/null 2>&1
git pull origin "${TARGET_BRANCH}" > /dev/null 2>&1
echo "[ OK ]"

echo -n "[2/5] Syncing application code & systemd units... "
cp -r "${REPO_ROOT}/app" "${APP_DIR}/"
cp "${REPO_ROOT}/pyproject.toml" "${APP_DIR}/"
cp "${REPO_ROOT}/uv.lock" "${APP_DIR}/" 2>/dev/null || true
if [ -f "${REPO_ROOT}/README.md" ]; then
    cp "${REPO_ROOT}/README.md" "${APP_DIR}/"
fi
if [ -f "${REPO_ROOT}/scripts/palworld-manager.service" ]; then
    cp "${REPO_ROOT}/scripts/palworld-manager.service" /etc/systemd/system/palworld-manager.service
    systemctl daemon-reload
fi
if [ -f "${REPO_ROOT}/scripts/duck.sh" ] && [ -d "/home/steam/duckdns" ]; then
    cp "${REPO_ROOT}/scripts/duck.sh" /home/steam/duckdns/duck.sh
    chmod 0755 /home/steam/duckdns/duck.sh
    chown steam:steam /home/steam/duckdns/duck.sh 2>/dev/null || true
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
echo "[ OK ]"

echo -n "[3/5] Enforcing cross-user POSIX ACLs and storage permissions... "
id -u steam >/dev/null 2>&1 && usermod -aG steam "${APP_USER}" 2>/dev/null || true
id -u steam >/dev/null 2>&1 && chmod 0755 /home/steam 2>/dev/null || true

# Apply POSIX ACLs for palmanager to read/write steam directory
if command -v setfacl >/dev/null 2>&1; then
    setfacl -m u:"${APP_USER}":rx /home/steam 2>/dev/null || true
    if [ -d "/home/steam/.steam" ]; then
        setfacl -R -m u:"${APP_USER}":rwX /home/steam/.steam 2>/dev/null || true
        setfacl -R -d -m u:"${APP_USER}":rwX /home/steam/.steam 2>/dev/null || true
    fi
    if [ -d "/home/steam/Steam" ]; then
        setfacl -R -m u:"${APP_USER}":rwX /home/steam/Steam 2>/dev/null || true
        setfacl -R -d -m u:"${APP_USER}":rwX /home/steam/Steam 2>/dev/null || true
    fi
    if [ -d "/home/steam/PalServer" ]; then
        setfacl -R -m u:"${APP_USER}":rwX /home/steam/PalServer 2>/dev/null || true
        setfacl -R -d -m u:"${APP_USER}":rwX /home/steam/PalServer 2>/dev/null || true
    fi
    if [ -d "/opt/palworld" ]; then
        setfacl -R -m u:"${APP_USER}":rwX /opt/palworld 2>/dev/null || true
    fi
fi

mkdir -p /var/lib/palmanager/backups
chown -R "${APP_USER}:${APP_USER}" /var/lib/palmanager
chmod -R 0775 /var/lib/palmanager
echo "[ OK ]"

echo -n "[4/5] Updating Python dependencies via uv... "
cd "${APP_DIR}"
su -s /bin/bash "${APP_USER}" -c "uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx websockets psutil > /dev/null"
echo "[ OK ]"

echo -n "[5/5] Restarting palworld-manager.service... "
systemctl restart palworld-manager.service
echo "[ OK ]"

echo "========================================================================="
echo " Deployment Complete! Service status: $(systemctl is-active palworld-manager.service)"
echo "========================================================================="
