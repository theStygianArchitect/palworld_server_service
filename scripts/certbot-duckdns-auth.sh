#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Certbot DuckDNS DNS-01 Auth Hook
# ==============================================================================
# Invoked by Certbot manual DNS challenge mode. Updates the DuckDNS TXT record
# with the ACME challenge validation string via the DuckDNS HTTPS API.
# Enables zero-port-forwarding SSL/TLS issuance behind NAT/firewalls.

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

# Domain Resolution from Certbot environment or config
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

if [ -z "${DOMAINS}" ]; then
    echo "[-] [DuckDNS Auth Hook Error] Domain could not be resolved from CERTBOT_DOMAIN or .env." >&2
    exit 1
fi

if [ -z "${TOKEN}" ] || [ "${TOKEN}" = "your_duckdns_token" ]; then
    echo "[-] [DuckDNS Auth Hook Error] DUCKDNS_TOKEN is missing or set to placeholder in .env." >&2
    exit 1
fi

if [ -z "${CERTBOT_VALIDATION:-}" ]; then
    echo "[-] [DuckDNS Auth Hook Error] CERTBOT_VALIDATION environment variable is empty." >&2
    exit 1
fi

# Set TXT record on DuckDNS
UPDATE_URL="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&txt=${CERTBOT_VALIDATION}"
RESPONSE=$(curl -s -f -m 20 "${UPDATE_URL}" || echo "ERROR")

if [ "${RESPONSE}" != "OK" ]; then
    echo "[-] [DuckDNS Auth Hook Error] DuckDNS API update failed. Response: '${RESPONSE}'" >&2
    exit 1
fi

echo "[+] [DuckDNS Auth Hook] Successfully published TXT record for ${DOMAINS}.duckdns.org"

# Allow DNS propagation
PROPAGATION_WAIT="${DUCKDNS_PROPAGATION_WAIT:-30}"
echo "[*] [DuckDNS Auth Hook] Waiting ${PROPAGATION_WAIT}s for DNS-01 challenge propagation..."
sleep "${PROPAGATION_WAIT}"
echo "[+] [DuckDNS Auth Hook] Ready for ACME challenge verification."
exit 0
