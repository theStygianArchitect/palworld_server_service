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

# Resolve git repository root if deploy.sh is executed directly from /opt/palworld-web-manager
if [ ! -d "${REPO_ROOT}/.git" ]; then
    CANDIDATES=(
        "${HOME}/palworld_server_service"
        "${HOME}/projects/python/personal/palworld_server_service"
        "/home/${SUDO_USER:-}/palworld_server_service"
        "/home/tsa/palworld_server_service"
        "/home/steam/palworld_server_service"
        "/var/lib/palmanager/repo"
    )
    FOUND_REPO=""
    for CANDIDATE in "${CANDIDATES[@]}"; do
        if [ -d "${CANDIDATE}/.git" ]; then
            FOUND_REPO="${CANDIDATE}"
            break
        fi
    done

    if [ -n "${FOUND_REPO}" ]; then
        REPO_ROOT="${FOUND_REPO}"
    else
        echo "[!] No local git repository found at ${REPO_ROOT}. Initializing repository cache in /var/lib/palmanager/repo..."
        mkdir -p /var/lib/palmanager
        git clone "https://github.com/theStygianArchitect/palworld_server_service.git" /var/lib/palmanager/repo
        REPO_ROOT="/var/lib/palmanager/repo"
    fi
fi

cd "${REPO_ROOT}"

echo -n "[1/5] Pulling latest updates from origin/${TARGET_BRANCH}... "
git fetch origin "${TARGET_BRANCH}"
git checkout "${TARGET_BRANCH}"
git pull origin "${TARGET_BRANCH}"
echo "[ OK ]"

echo -n "[2/5] Syncing application code & systemd units... "
cp -r "${REPO_ROOT}/app" "${APP_DIR}/"
cp "${REPO_ROOT}/pyproject.toml" "${APP_DIR}/"
cp "${REPO_ROOT}/uv.lock" "${APP_DIR}/" 2>/dev/null || true
if [ -f "${REPO_ROOT}/README.md" ]; then
    cp "${REPO_ROOT}/README.md" "${APP_DIR}/"
fi
if [ -d "${REPO_ROOT}/scripts" ]; then
    mkdir -p "${APP_DIR}/scripts"
    cp -r "${REPO_ROOT}/scripts/"* "${APP_DIR}/scripts/"
    chmod 0755 "${APP_DIR}/scripts/"*.sh 2>/dev/null || true
fi
if command -v git >/dev/null 2>&1 && [ -d "${REPO_ROOT}/.git" ]; then
    git -C "${REPO_ROOT}" rev-parse HEAD > "${APP_DIR}/.git_commit" 2>/dev/null || true
    chmod 0644 "${APP_DIR}/.git_commit" 2>/dev/null || true
fi
if [ -f "${REPO_ROOT}/scripts/palworld.service" ]; then
    cp "${REPO_ROOT}/scripts/palworld.service" /etc/systemd/system/palworld.service
fi
if [ -f "${REPO_ROOT}/scripts/palworld-manager.service" ]; then
    cp "${REPO_ROOT}/scripts/palworld-manager.service" /etc/systemd/system/palworld-manager.service
fi
if [ -f "${REPO_ROOT}/scripts/palworld-maintenance.sh" ]; then
    cp "${REPO_ROOT}/scripts/palworld-maintenance.sh" /home/steam/palworld-maintenance.sh
    chmod 0755 /home/steam/palworld-maintenance.sh
    chown steam:steam /home/steam/palworld-maintenance.sh 2>/dev/null || true
fi
systemctl daemon-reload
if [ -f "${REPO_ROOT}/scripts/duck.sh" ] && [ -d "/home/steam/duckdns" ]; then
    cp "${REPO_ROOT}/scripts/duck.sh" /home/steam/duckdns/duck.sh
    chmod 0755 /home/steam/duckdns/duck.sh
    chown steam:steam /home/steam/duckdns/duck.sh 2>/dev/null || true
fi
if [ -f "${APP_DIR}/.env" ] && [ -d "/home/steam/duckdns" ]; then
    cp "${APP_DIR}/.env" /home/steam/duckdns/.env
    chmod 0600 /home/steam/duckdns/.env
    chown steam:steam /home/steam/duckdns/.env 2>/dev/null || true
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

# Multi-path update flags provisioning
touch /home/steam/.update_requested 2>/dev/null || true
chown steam:steam /home/steam/.update_requested 2>/dev/null || true
chmod 0664 /home/steam/.update_requested 2>/dev/null || true

touch /var/lib/palmanager/update_requested 2>/dev/null || true
chown "${APP_USER}:${APP_USER}" /var/lib/palmanager/update_requested 2>/dev/null || true
chmod 0664 /var/lib/palmanager/update_requested 2>/dev/null || true

if command -v setfacl >/dev/null 2>&1; then
    setfacl -m u:"${APP_USER}":rw /home/steam/.update_requested 2>/dev/null || true
    setfacl -m u:steam:rw /var/lib/palmanager/update_requested 2>/dev/null || true
fi
echo "[ OK ]"

echo -n "[4/5] Updating Python dependencies via uv... "
cd "${APP_DIR}"
su -s /bin/bash "${APP_USER}" -c "uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx websockets psutil > /dev/null"
echo "[ OK ]"

echo -n "[5/5] Restarting palworld-manager.service... "
systemctl restart palworld-manager.service
echo "[ OK ]"

# Restore repository ownership to non-root calling user
if [ -n "${SUDO_USER:-}" ]; then
    chown -R "${SUDO_USER}:${SUDO_USER}" "${REPO_ROOT}" 2>/dev/null || true
fi

echo "========================================================================="
echo " Deployment Complete! Service status: $(systemctl is-active palworld-manager.service)"
echo "========================================================================="
