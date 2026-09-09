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
DEPLOY_START_TIME=$(date +%s 2>/dev/null || echo 0)
LOCK_FILE="/tmp/palmanager_update.lock"
POST_UPDATE_FILE="/var/lib/palmanager/last_update.json"

cleanup_on_exit() {
    local exit_code=$?
    if [ "${exit_code}" -ne 0 ]; then
        echo "[-] Deployment aborted with error exit code: ${exit_code}"
        rm -f "${LOCK_FILE}" 2>/dev/null || true
    fi
}
trap cleanup_on_exit EXIT

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

echo -n "[STEP 1/5] Pulling latest updates from origin/${TARGET_BRANCH}... "
git fetch origin "${TARGET_BRANCH}"
git checkout "${TARGET_BRANCH}"
git pull origin "${TARGET_BRANCH}"
echo "[ OK ]"

echo -n "[STEP 2/5] Syncing application code & systemd units... "
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
if [ -f "${REPO_ROOT}/scripts/palworld-cert-renew.service" ]; then
    cp "${REPO_ROOT}/scripts/palworld-cert-renew.service" /etc/systemd/system/palworld-cert-renew.service
fi
if [ -f "${REPO_ROOT}/scripts/palworld-cert-renew.timer" ]; then
    cp "${REPO_ROOT}/scripts/palworld-cert-renew.timer" /etc/systemd/system/palworld-cert-renew.timer
fi
systemctl daemon-reload
systemctl enable --now palworld-cert-renew.timer 2>/dev/null || true

# Provision certbot if not already present
if ! command -v certbot >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq && apt-get install -y -qq certbot >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q certbot >/dev/null 2>&1 || true
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm --needed certbot >/dev/null 2>&1 || true
    fi
fi

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

echo -n "[STEP 3/5] Enforcing cross-user POSIX ACLs and storage permissions... "
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
mkdir -p /var/lib/palmanager/certs
chown -R "${APP_USER}:${APP_USER}" /var/lib/palmanager
chmod -R 0775 /var/lib/palmanager
chmod 0750 /var/lib/palmanager/certs

# Ensure scoped sudoers rules for palworld-cert-manager.sh via dedicated drop-in
SUDOERS_CERTS_FILE="/etc/sudoers.d/palmanager-certs"
cat << SUDO_EOF > "${SUDOERS_CERTS_FILE}"
${APP_USER} ALL=(ALL) NOPASSWD: ${APP_DIR}/scripts/palworld-cert-manager.sh *
${APP_USER} ALL=(ALL) NOPASSWD: ${REPO_ROOT}/scripts/palworld-cert-manager.sh *
SUDO_EOF
chmod 0440 "${SUDOERS_CERTS_FILE}"
if command -v visudo >/dev/null 2>&1; then
    visudo -cf "${SUDOERS_CERTS_FILE}" >/dev/null 2>&1 || true
fi

# Register fallback daily root crontab for certificate renewal
if command -v crontab >/dev/null 2>&1; then
    ROOT_CRON="0 3 * * * ${APP_DIR}/scripts/palworld-cert-manager.sh renew >/dev/null 2>&1"
    EXISTING_ROOT_CRON=$(crontab -l 2>/dev/null || true)
    if ! echo "${EXISTING_ROOT_CRON}" | grep -q "palworld-cert-manager.sh"; then
        printf "%s\n%s\n" "${EXISTING_ROOT_CRON}" "${ROOT_CRON}" | sed '/^$/d' | crontab - 2>/dev/null || true
    fi
fi

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

echo -n "[STEP 4/5] Updating Python dependencies via uv... "
cd "${APP_DIR}"
su -s /bin/bash "${APP_USER}" -c "uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx websockets psutil > /dev/null"
echo "[ OK ]"

echo -n "[STEP 5/5] Restarting palworld-manager.service... "
systemctl restart palworld-manager.service
echo "[ OK ]"

# Restore repository ownership to non-root calling user
if [ -n "${SUDO_USER:-}" ]; then
    chown -R "${SUDO_USER}:${SUDO_USER}" "${REPO_ROOT}" 2>/dev/null || true
fi

# Serialize post-update summary record and release deployment lock
DEPLOY_END_TIME=$(date +%s 2>/dev/null || echo 0)
DEPLOY_DURATION=$((DEPLOY_END_TIME - DEPLOY_START_TIME))
if [ "${DEPLOY_DURATION}" -lt 0 ]; then
    DEPLOY_DURATION=0
fi

DEPLOYED_COMMIT="unknown"
DEPLOYED_COMMIT_SHORT="unknown"
COMMIT_MSG="Upstream update deployed successfully"
if [ -f "${APP_DIR}/.git_commit" ]; then
    DEPLOYED_COMMIT=$(cat "${APP_DIR}/.git_commit" 2>/dev/null || echo "unknown")
    DEPLOYED_COMMIT="${DEPLOYED_COMMIT//[$'\r\n']/}"
    DEPLOYED_COMMIT_SHORT="${DEPLOYED_COMMIT:0:7}"
fi
if command -v git >/dev/null 2>&1 && [ -d "${REPO_ROOT}/.git" ]; then
    RESOLVED_MSG=$(git -C "${REPO_ROOT}" log -1 --pretty=format:"%s" 2>/dev/null || echo "")
    if [ -n "${RESOLVED_MSG}" ]; then
        COMMIT_MSG="${RESOLVED_MSG}"
    fi
fi
# Escape JSON quotes in commit message
COMMIT_MSG_JSON=$(echo "${COMMIT_MSG}" | sed 's/"/\\"/g')
NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u +"%Y-%m-%d %H:%M:%S")

mkdir -p /var/lib/palmanager 2>/dev/null || true
cat <<EOF > "${POST_UPDATE_FILE}"
{
  "status": "success",
  "target_branch": "${TARGET_BRANCH}",
  "deployed_commit": "${DEPLOYED_COMMIT}",
  "deployed_commit_short": "${DEPLOYED_COMMIT_SHORT}",
  "deployed_at": "${NOW_ISO}",
  "duration_seconds": ${DEPLOY_DURATION},
  "summary": "${COMMIT_MSG_JSON}",
  "acknowledged": false
}
EOF
chmod 0644 "${POST_UPDATE_FILE}" 2>/dev/null || true
chown "${APP_USER}:${APP_USER}" "${POST_UPDATE_FILE}" 2>/dev/null || true

# Release lock file
rm -f "${LOCK_FILE}" 2>/dev/null || true

echo "========================================================================="
echo " Deployment Complete! Service status: $(systemctl is-active palworld-manager.service)"
echo "========================================================================="
