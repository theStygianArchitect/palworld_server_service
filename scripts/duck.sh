#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Palworld Unified Operations Suite - DuckDNS Dynamic IP Updater
# Automatically consumes DUCKDNS_DOMAIN & DUCKDNS_TOKEN from environment or .env
# ==============================================================================

# 1. Automatically load .env configuration files if available
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

# 2. Resolve Domain Subdomain (Support full FQDN or pure subdomain)
RAW_DOMAIN="${DUCKDNS_DOMAIN:-${PALWORLD_DUCKDNS_DOMAIN:-${PALWORLD_DOMAIN:-}}}"
DOMAINS=""

if [ -n "${RAW_DOMAIN}" ]; then
    # Strip any http/https protocol prefix if present
    RAW_DOMAIN="${RAW_DOMAIN#http://}"
    RAW_DOMAIN="${RAW_DOMAIN#https://}"
    # Strip port if present
    RAW_DOMAIN="${RAW_DOMAIN%%:*}"
    # Extract base subdomain (e.g. "myserver.duckdns.org" -> "myserver")
    if [[ "${RAW_DOMAIN}" == *".duckdns.org"* ]]; then
        DOMAINS="${RAW_DOMAIN%%.duckdns.org*}"
    elif [[ "${RAW_DOMAIN}" == *"."* ]]; then
        DOMAINS="${RAW_DOMAIN%%.*}"
    else
        DOMAINS="${RAW_DOMAIN}"
    fi
fi

# 3. Resolve DuckDNS API Token
TOKEN="${DUCKDNS_TOKEN:-${PALWORLD_DUCKDNS_TOKEN:-}}"

LOG_FILE="/home/steam/duckdns/duck.log"
mkdir -p "$(dirname "$LOG_FILE")"

# 4. Guard against missing or placeholder configuration
if [ -z "${DOMAINS}" ] || [ "${DOMAINS}" = "your_subdomain" ] || [ "${DOMAINS}" = "yourdomain" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP: DUCKDNS_DOMAIN or PALWORLD_DOMAIN not configured in environment/.env" >> "$LOG_FILE"
    exit 0
fi

if [ -z "${TOKEN}" ] || [ "${TOKEN}" = "your_duckdns_token" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP: DUCKDNS_TOKEN not configured in environment/.env" >> "$LOG_FILE"
    exit 0
fi

# 5. Execute Dynamic DNS Update (empty &ip= instructs DuckDNS to auto-detect WAN IP)
RESPONSE=$(curl -k -s "https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ip=" || echo "ERROR")
echo "${RESPONSE} [$(date '+%Y-%m-%d %H:%M:%S')] DuckDNS Sync Triggered (Domain: ${DOMAINS}.duckdns.org)" >> "$LOG_FILE"
