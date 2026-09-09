#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite & Lifecycle Automation Installer
# ==============================================================================
# Universal installer supporting Debian/Ubuntu, RHEL/Rocky, Arch, openSUSE, and Alpine.
# Supports standalone compiled ELF binary and source-level virtualenv deployment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_USER="palmanager"
APP_DIR="/opt/palworld-web-manager"
STEAM_USER="steam"
STEAM_HOME="/home/steam"
PAL_CONFIG_DIR="${STEAM_HOME}/.steam/steam/steamapps/common/PalServer/Pal/Saved/Config/LinuxServer"
PAL_SETTINGS_FILE="${PAL_CONFIG_DIR}/PalWorldSettings.ini"
PAL_SERVICE_FILE="/etc/systemd/system/palworld.service"
MAINTENANCE_SCRIPT="${STEAM_HOME}/palworld-maintenance.sh"
DUCKDNS_DIR="${STEAM_HOME}/duckdns"
DUCKDNS_SCRIPT="${DUCKDNS_DIR}/duck.sh"
MANAGER_SERVICE_FILE="/etc/systemd/system/palworld-manager.service"
SUDOERS_FILE="/etc/sudoers.d/palworld_manager_palmanager"
STANDALONE_BIN="/usr/local/bin/palworld-manager"
APP_PORT=8080

echo "========================================================================="
echo " Palworld Unified Operations Suite Deployment"
echo " Target Directory: ${APP_DIR} | UI Bind Port: ${APP_PORT}"
echo "========================================================================="

if [ "${EUID}" -ne 0 ]; then
    echo "[-] Please run as root or with sudo: sudo ./palworld-run.sh" >&2
    exit 1
fi

# 1. System Dependencies & Build Toolchain
echo -n "[1/8] Installing system dependencies, build toolchain, and certbot... "
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl git tar acl build-essential python3 python3-venv python3-pip iproute2 dnsutils ufw certbot >/dev/null 2>&1 || true
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q curl git tar acl gcc make python3 python3-pip python3-devel iproute bind-utils certbot >/dev/null 2>&1 || true
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q curl git tar acl gcc make python3 python3-pip python3-devel iproute bind-utils certbot >/dev/null 2>&1 || true
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm --needed curl git tar acl base-devel python python-pip iproute2 bind certbot >/dev/null 2>&1 || true
elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install -y curl git tar acl gcc make python3 python3-pip python3-devel iproute2 bind-utils certbot >/dev/null 2>&1 || true
elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache curl git tar acl build-base python3 py3-pip python3-dev iproute2 bind-tools certbot >/dev/null 2>&1 || true
else
    echo "[-] Warning: Unrecognized package manager. Ensure Python 3.10+, gcc, make, and certbot are available." >&2
fi
echo "[ OK ]"


# 2. Service Account Provisioning
echo -n "[2/8] Provisioning dedicated system users '${APP_USER}' and '${STEAM_USER}'... "
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -d "${APP_DIR}" -M "${APP_USER}"
fi
if ! id -u "${STEAM_USER}" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -d "${STEAM_HOME}" "${STEAM_USER}"
fi
echo "[ OK ]"

# 3. Directory Structures, DuckDNS Setup & POSIX ACLs
echo -n "[3/8] Setting up directories, DuckDNS, and POSIX ACLs... "
mkdir -p "${APP_DIR}"
mkdir -p "${STEAM_HOME}/Palworld_backups"
mkdir -p "${DUCKDNS_DIR}"

if [ -f "${SCRIPT_DIR}/duck.sh" ]; then
    cp "${SCRIPT_DIR}/duck.sh" "${DUCKDNS_SCRIPT}"
fi
chown -R "${STEAM_USER}:${STEAM_USER}" "${DUCKDNS_DIR}" 2>/dev/null || true
chmod 0755 "${DUCKDNS_SCRIPT}" 2>/dev/null || true

# Sync repository source to /opt/palworld-web-manager
if [ -d "${REPO_ROOT}/app" ]; then
    cp -r "${REPO_ROOT}/app" "${APP_DIR}/"
fi
if [ -f "${REPO_ROOT}/pyproject.toml" ]; then
    cp "${REPO_ROOT}/pyproject.toml" "${APP_DIR}/"
fi
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

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" 2>/dev/null || true

# Apply POSIX ACLs for shared cross-service interaction
setfacl -m u:"${APP_USER}":rx "${STEAM_HOME}" 2>/dev/null || true
setfacl -R -m u:"${APP_USER}":rwX "${DUCKDNS_DIR}" 2>/dev/null || true
setfacl -R -d -m u:"${APP_USER}":rwX "${DUCKDNS_DIR}" 2>/dev/null || true

if [ -d "${PAL_CONFIG_DIR}" ]; then
    setfacl -R -m u:"${APP_USER}":rwX "${PAL_CONFIG_DIR}" 2>/dev/null || true
    setfacl -R -d -m u:"${APP_USER}":rwX "${PAL_CONFIG_DIR}" 2>/dev/null || true
    if [ -f "${PAL_SETTINGS_FILE}" ]; then
        setfacl -m u:"${APP_USER}":rw "${PAL_SETTINGS_FILE}" 2>/dev/null || true
    fi
fi

touch "${STEAM_HOME}/.update_requested" 2>/dev/null || true
: > "${STEAM_HOME}/.update_requested" 2>/dev/null || true
chmod 0666 "${STEAM_HOME}/.update_requested" 2>/dev/null || true
setfacl -m u:"${APP_USER}":rw "${STEAM_HOME}/.update_requested" 2>/dev/null || true

mkdir -p /var/lib/palmanager/certs
touch /var/lib/palmanager/update_requested 2>/dev/null || true
: > /var/lib/palmanager/update_requested 2>/dev/null || true
chown -R "${APP_USER}:${APP_USER}" /var/lib/palmanager 2>/dev/null || true
chmod 0775 /var/lib/palmanager 2>/dev/null || true
chmod 0750 /var/lib/palmanager/certs 2>/dev/null || true
chmod 0666 /var/lib/palmanager/update_requested 2>/dev/null || true
setfacl -m u:steam:rw /var/lib/palmanager/update_requested 2>/dev/null || true
setfacl -m u:steam:rwx /var/lib/palmanager 2>/dev/null || true
echo "[ OK ]"

# 4. Scoped Sudoers Privileges
echo -n "[4/8] Configuring scoped sudoers rules for '${APP_USER}'... "
cat << SUDO_EOF > "${SUDOERS_FILE}"
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart palworld.service, /usr/bin/systemctl restart palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl status palworld.service, /usr/bin/systemctl status palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/systemctl is-active palworld.service, /usr/bin/systemctl is-active palworld.service
${APP_USER} ALL=(ALL) NOPASSWD: /bin/journalctl -u palworld.service *, /usr/bin/journalctl -u palworld.service *
${APP_USER} ALL=(ALL) NOPASSWD: /usr/sbin/ufw status
${APP_USER} ALL=(ALL) NOPASSWD: ${APP_DIR}/scripts/deploy.sh *
${APP_USER} ALL=(ALL) NOPASSWD: ${REPO_ROOT}/scripts/deploy.sh *
${APP_USER} ALL=(ALL) NOPASSWD: ${APP_DIR}/scripts/palworld-cert-manager.sh *
${APP_USER} ALL=(ALL) NOPASSWD: ${REPO_ROOT}/scripts/palworld-cert-manager.sh *
SUDO_EOF
chmod 0440 "${SUDOERS_FILE}"
if command -v visudo >/dev/null 2>&1; then
    visudo -cf "${SUDOERS_FILE}" >/dev/null 2>&1 || true
fi
cp "${SUDOERS_FILE}" /etc/sudoers.d/palmanager-certs 2>/dev/null || true
chmod 0440 /etc/sudoers.d/palmanager-certs 2>/dev/null || true
echo "[ OK ]"

# 5. Service Files & Maintenance Scripts
echo -n "[5/8] Installing systemd units and maintenance hooks... "
if [ -f "${SCRIPT_DIR}/palworld.service" ]; then
    cp "${SCRIPT_DIR}/palworld.service" "${PAL_SERVICE_FILE}"
fi
if [ -f "${SCRIPT_DIR}/palworld-maintenance.sh" ]; then
    cp "${SCRIPT_DIR}/palworld-maintenance.sh" "${MAINTENANCE_SCRIPT}"
    chown "${STEAM_USER}:${STEAM_USER}" "${MAINTENANCE_SCRIPT}" 2>/dev/null || true
    chmod +x "${MAINTENANCE_SCRIPT}" 2>/dev/null || true
fi

if [ -f "${SCRIPT_DIR}/palworld-manager.service" ]; then
    cp "${SCRIPT_DIR}/palworld-manager.service" "${MANAGER_SERVICE_FILE}"
fi
if [ -f "${SCRIPT_DIR}/palworld-cert-renew.service" ]; then
    cp "${SCRIPT_DIR}/palworld-cert-renew.service" "/etc/systemd/system/palworld-cert-renew.service"
fi
if [ -f "${SCRIPT_DIR}/palworld-cert-renew.timer" ]; then
    cp "${SCRIPT_DIR}/palworld-cert-renew.timer" "/etc/systemd/system/palworld-cert-renew.timer"
fi
echo "[ OK ]"


# 6. Standalone Binary Deployment or Virtualenv Environment Build
echo -n "[6/8] Building operations plane execution runtime... "
deployed_standalone=0
if [ -f "${REPO_ROOT}/dist/palworld-manager" ]; then
    cp "${REPO_ROOT}/dist/palworld-manager" "${STANDALONE_BIN}"
    chmod 0755 "${STANDALONE_BIN}"
    deployed_standalone=1
elif [ -f "${REPO_ROOT}/palworld-manager" ]; then
    cp "${REPO_ROOT}/palworld-manager" "${STANDALONE_BIN}"
    chmod 0755 "${STANDALONE_BIN}"
    deployed_standalone=1
fi

if [ "${deployed_standalone}" -eq 1 ]; then
    echo "[ OK (Standalone Binary Installed to ${STANDALONE_BIN}) ]"
else
    # Deploy source into isolated virtual environment
    if [ -f "${REPO_ROOT}/uv.lock" ]; then
        cp "${REPO_ROOT}/uv.lock" "${APP_DIR}/" 2>/dev/null || true
    fi
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" 2>/dev/null || true
    cd "${APP_DIR}"

    # Verify if uv is available and make accessible to service user
    host_uv=$(command -v uv || true)
    if [ -n "${host_uv}" ]; then
        if [ ! -x "/usr/local/bin/uv" ]; then
            cp "${host_uv}" /usr/local/bin/uv 2>/dev/null || true
            chmod 0755 /usr/local/bin/uv 2>/dev/null || true
        fi
        su -s /bin/bash "${APP_USER}" -c "export PATH='/usr/local/bin:/usr/bin:/bin'; uv venv --clear .venv --python python3 >/dev/null 2>&1 && uv pip install --python .venv/bin/python fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx websockets psutil >/dev/null 2>&1"
    else
        # Pure standard library and native compiler fallback (zero external binary downloads)
        su -s /bin/bash "${APP_USER}" -c "python3 -m venv --clear .venv >/dev/null 2>&1 && .venv/bin/python -m pip install --quiet --upgrade pip >/dev/null 2>&1 && .venv/bin/python -m pip install --quiet fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx websockets psutil >/dev/null 2>&1"
    fi
    echo "[ OK (Virtualenv Built) ]"
fi

# 7. Crontabs & DNS Initialization
echo -n "[7/8] Registering cron jobs and verifying DuckDNS... "
if command -v crontab >/dev/null 2>&1; then
    STEAM_CRON="*/5 * * * * /home/steam/duckdns/duck.sh >/dev/null 2>&1"
    EXISTING_STEAM_CRON=$(crontab -u steam -l 2>/dev/null || true)
    FILTERED_STEAM_CRON=$(echo "${EXISTING_STEAM_CRON}" | grep -v "/home/steam/duckdns/duck.sh" || true)
    printf "%s\n%s\n" "${FILTERED_STEAM_CRON}" "${STEAM_CRON}" | sed '/^$/d' | crontab -u steam - 2>/dev/null || true

    CRON_JOB="0 */4 * * * curl -s -X POST http://127.0.0.1:${APP_PORT}/api/service/reboot -H 'Content-Type: application/json' -d '{\"settings\":{}, \"countdown_seconds\":600, \"trigger_steam_update\":false}' > /dev/null 2>&1"
    CERT_CRON="0 3 * * * /opt/palworld-web-manager/scripts/palworld-cert-manager.sh renew >/dev/null 2>&1"
    EXISTING_CRON=$(crontab -l 2>/dev/null || true)
    FILTERED_CRON=$(echo "${EXISTING_CRON}" | grep -v "/api/service/reboot" | grep -v "palworld-cert-manager.sh" || true)
    printf "%s\n%s\n%s\n" "${FILTERED_CRON}" "${CRON_JOB}" "${CERT_CRON}" | sed '/^$/d' | crontab - 2>/dev/null || true
fi

if [ -f "/home/steam/duckdns/duck.sh" ]; then
    su - steam -c "/home/steam/duckdns/duck.sh" 2>/dev/null || true
fi
echo "[ OK ]"

# 8. Reload and Start Daemons
echo -n "[8/8] Reloading systemd and starting Palworld Manager... "
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload 2>/dev/null || true

    if systemctl is-active --quiet palworld.service 2>/dev/null; then
        :
    else
        systemctl start palworld.service >/dev/null 2>&1 || true
    fi

    systemctl restart palworld-manager.service >/dev/null 2>&1 || true
    systemctl enable palworld-manager.service >/dev/null 2>&1 || true
    systemctl enable palworld-cert-renew.timer >/dev/null 2>&1 || true
    systemctl start palworld-cert-renew.timer >/dev/null 2>&1 || true
fi
echo "[ OK ]"

ETH0_DETECTED=""
if command -v ip >/dev/null 2>&1; then
    ETH0_DETECTED=$(ip -4 -o addr show dev eth0 2>/dev/null | awk -F '[ /]+' '{print $4}' || true)
fi
if [ -z "${ETH0_DETECTED:-}" ] && command -v hostname >/dev/null 2>&1; then
    ETH0_DETECTED=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
fi

PORT_SCHEME="http"
if [ -f "/var/lib/palmanager/certs/fullchain.pem" ] && [ -f "/var/lib/palmanager/certs/privkey.pem" ]; then
    PORT_SCHEME="https"
fi

echo ""
echo "========================================================================="
echo " Palworld Operations Suite Deployed Successfully!"
echo " Web UI Dashboard:        ${PORT_SCHEME}://${ETH0_DETECTED:-localhost}:${APP_PORT}"
echo " Pages Available:         World Settings | Player Roster | Hardware | Backups | Observability"
echo " Game Server State:       $(systemctl is-active palworld.service 2>/dev/null || echo 'inactive')"
echo "========================================================================="


ADMIN_CRED_FILE="/etc/palmanager/initial_admin_credential.txt"
if [ ! -f "${ADMIN_CRED_FILE}" ] && [ -f "${HOME}/.palmanager/initial_admin_credential.txt" ]; then
    ADMIN_CRED_FILE="${HOME}/.palmanager/initial_admin_credential.txt"
fi

if [ -f "${ADMIN_CRED_FILE}" ]; then
    echo " [!] NOTICE: Initial administrator credentials generated:"
    echo "     Credential File: ${ADMIN_CRED_FILE} (mode 0600)"
    echo "     Username:        admin"
    echo "     Password:        [Stored in ${ADMIN_CRED_FILE}]"
    echo "========================================================================="
fi
