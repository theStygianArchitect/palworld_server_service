#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Palworld Unified Operations Suite & Lifecycle Automation Installer
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_USER="palmanager"
APP_DIR="/opt/palworld-web-manager"
BACKUP_REPO_DIR="/var/lib/palmanager/backups"
STEAM_USER="steam"
STEAM_HOME="/home/steam"
PAL_CONFIG_DIR="${STEAM_HOME}/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer"
PAL_SETTINGS_FILE="${PAL_CONFIG_DIR}/PalWorldSettings.ini"
PAL_SERVICE_FILE="/etc/systemd/system/palworld.service"
MAINTENANCE_SCRIPT="${STEAM_HOME}/palworld-maintenance.sh"
DUCKDNS_DIR="${STEAM_HOME}/duckdns"
DUCKDNS_SCRIPT="${DUCKDNS_DIR}/duck.sh"
DUCKDNS_LOG="${DUCKDNS_DIR}/duck.log"
MANAGER_SERVICE_FILE="/etc/systemd/system/palworld-manager.service"
SUDOERS_FILE="/etc/sudoers.d/palworld_manager_palmanager"
APP_PORT=8080

echo "========================================================================="
echo " Palworld Unified Operations Suite Deployment"
echo " Target Directory: ${APP_DIR} | UI Bind Port: ${APP_PORT}"
echo "========================================================================="

if [ "$EUID" -ne 0 ]; then
    echo "[-] Please run as root or with sudo: sudo ./palworld-run.sh"
    exit 1
fi

# 1. System Dependencies & UV
echo -n "[1/8] Installing system dependencies and uv... "
apt-get update -qq
apt-get install -y -qq curl git tar acl build-essential python3 python3-venv python3-pip iproute2 dnsutils ufw > /dev/null
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh > /dev/null 2>&1
fi
echo "[ OK ]"

# 2. Service Account Provisioning
echo -n "[2/8] Provisioning dedicated system user '${APP_USER}'... "
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -d "${APP_DIR}" -M "${APP_USER}"
fi
echo "[ OK ]"

# 3. Directory Structures, DuckDNS Setup & POSIX ACLs
echo -n "[3/8] Setting up directories, DuckDNS, and POSIX ACLs... "
mkdir -p "${APP_DIR}"
mkdir -p "${BACKUP_REPO_DIR}"
mkdir -p "${STEAM_HOME}/Palworld_backups"
mkdir -p "${DUCKDNS_DIR}"

cp "${SCRIPT_DIR}/duck.sh" "${DUCKDNS_SCRIPT}"
chown -R "${STEAM_USER}:${STEAM_USER}" "${DUCKDNS_DIR}"
chmod 0755 "${DUCKDNS_SCRIPT}"

# Sync repository code to /opt/palworld-web-manager
cp -r "${REPO_ROOT}/app" "${APP_DIR}/"
cp "${REPO_ROOT}/pyproject.toml" "${APP_DIR}/"
if [ -f "${REPO_ROOT}/README.md" ]; then
    cp "${REPO_ROOT}/README.md" "${APP_DIR}/"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "${BACKUP_REPO_DIR}"
chmod 0750 "${BACKUP_REPO_DIR}"

setfacl -m u:"${APP_USER}":rx "${STEAM_HOME}" || true
setfacl -R -m u:"${APP_USER}":rwX "${DUCKDNS_DIR}" || true
setfacl -R -d -m u:"${APP_USER}":rwX "${DUCKDNS_DIR}" || true

if [ -d "${PAL_CONFIG_DIR}" ]; then
    setfacl -R -m u:"${APP_USER}":rwX "${PAL_CONFIG_DIR}" || true
    setfacl -R -d -m u:"${APP_USER}":rwX "${PAL_CONFIG_DIR}" || true
    if [ -f "${PAL_SETTINGS_FILE}" ]; then
        setfacl -m u:"${APP_USER}":rw "${PAL_SETTINGS_FILE}" || true
    fi
fi

touch "${STEAM_HOME}/.update_requested" 2>/dev/null || true
setfacl -m u:"${APP_USER}":rw "${STEAM_HOME}/.update_requested" || true
echo "[ OK ]"

# 4. Scoped Sudoers Privileges
echo -n "[4/8] Configuring scoped sudoers rules for '${APP_USER}'... "
cat << SUDO_EOF > "${SUDOERS_FILE}"
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl status palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl is-active palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/journalctl -u palworld.service *
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/ufw status
SUDO_EOF
chmod 0440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}" > /dev/null
echo "[ OK ]"

# 5. Service Files & Maintenance Scripts
echo -n "[5/8] Installing systemd units and maintenance hooks... "
cp "${SCRIPT_DIR}/palworld.service" "${PAL_SERVICE_FILE}"
cp "${SCRIPT_DIR}/palworld-maintenance.sh" "${MAINTENANCE_SCRIPT}"
chown "${STEAM_USER}:${STEAM_USER}" "${MAINTENANCE_SCRIPT}"
chmod +x "${MAINTENANCE_SCRIPT}"

cp "${SCRIPT_DIR}/palworld-manager.service" "${MANAGER_SERVICE_FILE}"
echo "[ OK ]"

# 6. UV Virtualenv & Dependency Installation
echo -n "[6/8] Building Python environment with uv... "
cd "${APP_DIR}"
su -s /bin/bash "${APP_USER}" -c "uv venv --clear .venv --python python3 > /dev/null && uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic httpx websockets psutil > /dev/null"
echo "[ OK ]"

# 7. Crontabs & DNS Initialization
echo -n "[7/8] Registering cron jobs and verifying DuckDNS... "
STEAM_CRON="*/5 * * * * /home/steam/duckdns/duck.sh >/dev/null 2>&1"
EXISTING_STEAM_CRON=$(crontab -u steam -l 2>/dev/null || true)
FILTERED_STEAM_CRON=$(echo "$EXISTING_STEAM_CRON" | grep -v "/home/steam/duckdns/duck.sh" || true)
printf "%s\n%s\n" "$FILTERED_STEAM_CRON" "$STEAM_CRON" | sed '/^$/d' | crontab -u steam - || true

CRON_JOB="0 */4 * * * curl -s -X POST http://127.0.0.1:${APP_PORT}/api/service/reboot -H 'Content-Type: application/json' -d '{\"settings\":{}, \"countdown_seconds\":600, \"trigger_steam_update\":false}' > /dev/null 2>&1"
EXISTING_CRON=$(crontab -l 2>/dev/null || true)
FILTERED_CRON=$(echo "$EXISTING_CRON" | grep -v "/api/service/reboot" || true)
printf "%s\n%s\n" "$FILTERED_CRON" "$CRON_JOB" | sed '/^$/d' | crontab - || true

su - steam -c "/home/steam/duckdns/duck.sh" || true
echo "[ OK ]"

# 8. Reload and Start Daemons
echo -n "[8/8] Reloading systemd and starting Palworld Manager... "
systemctl daemon-reload

if ! systemctl is-active --quiet palworld.service; then
    systemctl start palworld.service > /dev/null 2>&1 || true
fi

systemctl restart palworld-manager.service > /dev/null 2>&1
systemctl enable palworld-manager.service > /dev/null 2>&1
echo "[ OK ]"

ETH0_DETECTED=$(ip -4 -o addr show dev eth0 2>/dev/null | awk -F '[ /]+' '{print $4}' || true)
if [ -z "${ETH0_DETECTED:-}" ]; then
    ETH0_DETECTED=$(hostname -I | awk '{print $1}')
fi

echo ""
echo "========================================================================="
echo " Palworld Operations Suite Deployed Successfully!"
echo " Web UI Dashboard:        http://${ETH0_DETECTED:-localhost}:${APP_PORT}"
echo " Pages Available:         World Settings | Player Roster | Hardware | Backups"
echo " Game Server State:       $(systemctl is-active palworld.service 2>/dev/null || echo 'inactive')"
echo "========================================================================="
