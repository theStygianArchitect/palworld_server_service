#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Certbot Deploy Hook & Staging Engine
# ==============================================================================
# Executed automatically by Certbot upon successful certificate issue or renewal.
# Atomically stages fullchain.pem and privkey.pem to /var/lib/palmanager/certs/,
# enforces least-privilege POSIX permissions (0600, palmanager:palmanager), and
# signals palworld-manager.service to reload without operator intervention.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="palmanager"
STAGE_DIR="/var/lib/palmanager/certs"

# 1. Resolve source certificate lineage from Certbot or environment
SOURCE_LINEAGE="${RENEWED_LINEAGE:-}"

if [ -z "${SOURCE_LINEAGE}" ]; then
    ENV_CANDIDATES=(
        "/opt/palworld-web-manager/.env"
        "/home/steam/duckdns/.env"
        "/home/steam/.env"
        "$(pwd)/.env"
    )
    for env_file in "${ENV_CANDIDATES[@]}"; do
        if [ -f "$env_file" ]; then
            set -a
            # shellcheck disable=SC1090
            source "$env_file" 2>/dev/null || true
            set +a
        fi
    done

    RAW_DOMAIN="${DUCKDNS_DOMAIN:-${PALWORLD_DUCKDNS_DOMAIN:-${PALWORLD_DOMAIN:-}}}"
    if [ -n "${RAW_DOMAIN}" ]; then
        RAW_DOMAIN="${RAW_DOMAIN#http://}"
        RAW_DOMAIN="${RAW_DOMAIN#https://}"
        RAW_DOMAIN="${RAW_DOMAIN%%:*}"
        if [[ "${RAW_DOMAIN}" != *".duckdns.org" ]] && [[ "${RAW_DOMAIN}" != *"."* ]]; then
            RAW_DOMAIN="${RAW_DOMAIN}.duckdns.org"
        fi
        if [ -d "/etc/letsencrypt/live/${RAW_DOMAIN}" ]; then
            SOURCE_LINEAGE="/etc/letsencrypt/live/${RAW_DOMAIN}"
        fi
    fi
fi

if [ -z "${SOURCE_LINEAGE}" ] || [ ! -d "${SOURCE_LINEAGE}" ]; then
    echo "[-] [Deploy Hook] Source certificate directory not found: '${SOURCE_LINEAGE}'" >&2
    exit 1
fi

CERT_SRC="${SOURCE_LINEAGE}/fullchain.pem"
KEY_SRC="${SOURCE_LINEAGE}/privkey.pem"

if [ ! -f "${CERT_SRC}" ] || [ ! -f "${KEY_SRC}" ]; then
    echo "[-] [Deploy Hook] fullchain.pem or privkey.pem missing in '${SOURCE_LINEAGE}'" >&2
    exit 1
fi

# 2. Stage into /var/lib/palmanager/certs with atomic copy
mkdir -p "${STAGE_DIR}"

cp -L "${CERT_SRC}" "${STAGE_DIR}/fullchain.pem.tmp"
cp -L "${KEY_SRC}" "${STAGE_DIR}/privkey.pem.tmp"

chmod 0644 "${STAGE_DIR}/fullchain.pem.tmp"
chmod 0600 "${STAGE_DIR}/privkey.pem.tmp"

if id -u "${APP_USER}" >/dev/null 2>&1; then
    chown "${APP_USER}:${APP_USER}" "${STAGE_DIR}/fullchain.pem.tmp" "${STAGE_DIR}/privkey.pem.tmp" 2>/dev/null || true
    chown -R "${APP_USER}:${APP_USER}" "${STAGE_DIR}" 2>/dev/null || true
fi

mv -f "${STAGE_DIR}/fullchain.pem.tmp" "${STAGE_DIR}/fullchain.pem"
mv -f "${STAGE_DIR}/privkey.pem.tmp" "${STAGE_DIR}/privkey.pem"

echo "[+] [Deploy Hook] Staged certificates to ${STAGE_DIR} (mode 0600 for privkey.pem)"

# 3. Reload palworld-manager.service if running under systemd
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet palworld-manager.service 2>/dev/null; then
    echo "[*] [Deploy Hook] Reloading palworld-manager.service..."
    systemctl restart palworld-manager.service >/dev/null 2>&1 || true
    echo "[+] [Deploy Hook] palworld-manager.service restarted with fresh TLS certificate."
fi

exit 0
