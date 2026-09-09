#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Certbot DuckDNS DNS-01 Cleanup Hook
# ==============================================================================
# Invoked by Certbot manual DNS challenge mode after challenge validation.
# Clears the temporary TXT challenge record via the DuckDNS HTTPS API.

set -euo pipefail

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

RAW_DOMAIN="${CERTBOT_DOMAIN:-${DUCKDNS_DOMAIN:-${PALWORLD_DUCKDNS_DOMAIN:-${PALWORLD_DOMAIN:-}}}}"
DOMAINS=""

if [ -n "${RAW_DOMAIN}" ]; then
    RAW_DOMAIN="${RAW_DOMAIN#http://}"
    RAW_DOMAIN="${RAW_DOMAIN#https://}"
    RAW_DOMAIN="${RAW_DOMAIN%%:*}"
    if [[ "${RAW_DOMAIN}" == *".duckdns.org"* ]]; then
        DOMAINS="${RAW_DOMAIN%%.duckdns.org*}"
    elif [[ "${RAW_DOMAIN}" == *"."* ]]; then
        DOMAINS="${RAW_DOMAIN%%.*}"
    else
        DOMAINS="${RAW_DOMAIN}"
    fi
fi

TOKEN="${DUCKDNS_TOKEN:-${PALWORLD_DUCKDNS_TOKEN:-}}"

if [ -z "${DOMAINS}" ] || [ -z "${TOKEN}" ] || [ "${TOKEN}" = "your_duckdns_token" ]; then
    echo "[*] [DuckDNS Cleanup Hook] Incomplete configuration, skipping TXT record clearance."
    exit 0
fi

# Clear TXT record on DuckDNS
CLEAR_URL="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&clear=true"
RESPONSE=$(curl -s -f -m 15 "${CLEAR_URL}" || echo "ERROR")

if [ "${RESPONSE}" = "OK" ]; then
    echo "[+] [DuckDNS Cleanup Hook] Successfully cleared TXT record for ${DOMAINS}.duckdns.org"
else
    echo "[*] [DuckDNS Cleanup Hook] DuckDNS API clearance returned '${RESPONSE}'"
fi

exit 0
