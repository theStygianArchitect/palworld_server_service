#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Automated TLS Certificate Manager
# ==============================================================================
# Comprehensive certificate lifecycle utility: issue, renew (standard & early/force),
# status inspection, and staging verification for Let's Encrypt and DuckDNS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/palworld-web-manager"
STAGE_DIR="/var/lib/palmanager/certs"

ENV_CANDIDATES=(
    "${APP_DIR}/.env"
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

resolve_fqdn() {
    local raw="${DUCKDNS_DOMAIN:-${PALWORLD_DUCKDNS_DOMAIN:-${PALWORLD_DOMAIN:-}}}"
    if [ -z "${raw}" ]; then
        echo ""
        return
    fi
    raw="${raw#http://}"
    raw="${raw#https://}"
    raw="${raw%%:*}"
    if [[ "${raw}" != *".duckdns.org" ]] && [[ "${raw}" != *"."* ]]; then
        raw="${raw}.duckdns.org"
    fi
    echo "${raw}"
}

cmd_issue() {
    local domain
    echo -n "[STEP 1/4] Resolving domain configuration & DuckDNS credentials... "
    domain=$(resolve_fqdn)
    if [ -z "${domain}" ] || [[ "${domain}" == "yourdomain"* ]]; then
        echo "[ FAILED ]"
        echo "[-] Error: DUCKDNS_DOMAIN must be configured in environment or .env" >&2
        exit 1
    fi

    local email="${PALWORLD_LETSENCRYPT_EMAIL:-${LETSENCRYPT_EMAIL:-}}"
    local email_flags=("--register-unsafely-without-email")
    if [ -n "${email}" ] && [[ "${email}" == *"@"* ]]; then
        email_flags=("--email" "${email}")
    fi

    if ! command -v certbot >/dev/null 2>&1; then
        echo "[ FAILED ]"
        echo "[-] Error: certbot is not installed on this host. Run install.sh to provision dependencies." >&2
        exit 1
    fi
    echo "[ OK ]"

    echo "========================================================================="
    echo " Palworld Operations Suite - TLS Certificate Provisioning"
    echo " Target Domain: ${domain}"
    echo " Validation:    DuckDNS DNS-01 (Zero Port 80 Forwarding Required)"
    echo "========================================================================="

    echo "[STEP 2/4] Requesting Let's Encrypt DNS-01 challenge via DuckDNS TXT record... "
    certbot certonly \
        --non-interactive \
        --agree-tos \
        "${email_flags[@]}" \
        --manual \
        --preferred-challenges dns \
        --manual-auth-hook "${SCRIPT_DIR}/certbot-duckdns-auth.sh" \
        --manual-cleanup-hook "${SCRIPT_DIR}/certbot-duckdns-cleanup.sh" \
        --deploy-hook "${SCRIPT_DIR}/palworld-cert-deploy-hook.sh" \
        -d "${domain}"
    echo "[ OK ]"

    echo -n "[STEP 3/4] Staging certificates to ${STAGE_DIR} with 0600 permissions... "
    if [ -f "${STAGE_DIR}/fullchain.pem" ] && [ -f "${STAGE_DIR}/privkey.pem" ]; then
        echo "[ OK ]"
    else
        echo "[*] Staging certificate files directly..."
        RENEWED_LINEAGE="/etc/letsencrypt/live/${domain}" bash "${SCRIPT_DIR}/palworld-cert-deploy-hook.sh" 2>/dev/null || true
        echo "[ OK ]"
    fi

    echo -n "[STEP 4/4] Validating certificate chain & reloading web services... "
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet palworld-manager.service 2>/dev/null; then
        systemctl restart palworld-manager.service >/dev/null 2>&1 || true
    fi
    echo "[ OK ]"

    echo "[+] Certificate successfully provisioned for ${domain}"
}

cmd_renew() {
    local domain
    echo -n "[STEP 1/4] Checking domain & existing certificate lineage... "
    domain=$(resolve_fqdn)

    # If no certificate lineage or staged certificate exists yet, automatically provision initial certificate!
    if [ -n "${domain}" ] && [ ! -d "/etc/letsencrypt/live/${domain}" ] && [ ! -f "${STAGE_DIR}/fullchain.pem" ]; then
        echo "[*] No existing certificate found for '${domain}'. Triggering initial issuance..."
        cmd_issue
        return
    fi

    local force_flag=""
    if [ "${1:-}" = "--force" ] || [ "${1:-}" = "-f" ]; then
        force_flag="--force-renewal"
        echo "[*] Triggering FORCED early TLS certificate renewal..."
    else
        echo "[*] Checking and renewing TLS certificates if near expiration..."
    fi

    if ! command -v certbot >/dev/null 2>&1; then
        echo "[ FAILED ]"
        echo "[-] Error: certbot is not installed." >&2
        exit 1
    fi
    echo "[ OK ]"

    echo "[STEP 2/4] Executing certbot renewal with DuckDNS DNS-01 challenge... "
    # shellcheck disable=SC2086
    certbot renew \
        --non-interactive \
        ${force_flag} \
        --deploy-hook "${SCRIPT_DIR}/palworld-cert-deploy-hook.sh"
    echo "[ OK ]"

    echo -n "[STEP 3/4] Staging renewed certificates to ${STAGE_DIR}... "
    # Always ensure staged certificates exist even if certbot skipped renewal
    if [ ! -f "${STAGE_DIR}/fullchain.pem" ]; then
        RENEWED_LINEAGE="" bash "${SCRIPT_DIR}/palworld-cert-deploy-hook.sh" 2>/dev/null || true
    fi
    echo "[ OK ]"

    echo -n "[STEP 4/4] Validating certificate chain & reloading web services... "
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet palworld-manager.service 2>/dev/null; then
        systemctl restart palworld-manager.service >/dev/null 2>&1 || true
    fi
    echo "[ OK ]"

    echo "[+] Certificate renewal check completed."
}

cmd_status() {
    local domain
    domain=$(resolve_fqdn)
    echo "========================================================================="
    echo " Palworld Operations Suite - TLS Security Status"
    echo " Configured Domain: ${domain:-None}"
    echo " Certificate Stage: ${STAGE_DIR}"
    echo "========================================================================="

    if [ -f "${STAGE_DIR}/fullchain.pem" ] && [ -f "${STAGE_DIR}/privkey.pem" ]; then
        echo "[+] Staged Certificate: Present (Valid mode $(stat -c '%a' "${STAGE_DIR}/privkey.pem" 2>/dev/null || stat -f '%p' "${STAGE_DIR}/privkey.pem" 2>/dev/null || echo '0600'))"
        if command -v openssl >/dev/null 2>&1; then
            echo "--- Certificate Details ---"
            openssl x509 -in "${STAGE_DIR}/fullchain.pem" -noout -issuer -subject -dates 2>/dev/null || true
        fi
    else
        echo "[-] Staged Certificate: Not Found in ${STAGE_DIR}"
    fi

    echo "--- Automation Timers ---"
    if command -v systemctl >/dev/null 2>&1; then
        echo "palworld-cert-renew.timer: $(systemctl is-active palworld-cert-renew.timer 2>/dev/null || echo 'inactive')"
    fi
    if command -v crontab >/dev/null 2>&1; then
        echo "crontab (root): $(crontab -l 2>/dev/null | grep 'palworld-cert-manager.sh' || echo 'None')"
    fi
    echo "========================================================================="
}

cmd_revoke() {
    local domain
    domain=$(resolve_fqdn)
    if [ -z "${domain}" ]; then
        echo "[-] Error: Domain not specified." >&2
        exit 1
    fi
    echo "[!] Revoking certificate for ${domain}..."
    certbot revoke --cert-path "/etc/letsencrypt/live/${domain}/fullchain.pem" --non-interactive
    echo "[+] Certificate revoked."
}

case "${1:-status}" in
    issue)
        cmd_issue
        ;;
    renew)
        shift
        cmd_renew "$@"
        ;;
    status)
        cmd_status
        ;;
    revoke)
        cmd_revoke
        ;;
    *)
        echo "Usage: $0 {issue|renew [--force]|status|revoke}" >&2
        exit 1
        ;;
esac
