#!/bin/bash
SCHEMA="https://"
BASE_URL="www.duckdns.org"
ENDPOINT="/update"
DOMAINS="${DUCKDNS_DOMAIN:-your_subdomain}"
TOKEN="${DUCKDNS_TOKEN:-your_duckdns_token}"
IP=""
CONSTRUCTED_URL="${SCHEMA}${BASE_URL}${ENDPOINT}?domains=${DOMAINS}&token=${TOKEN}&ip=${IP}"
LOG_FILE="/home/steam/duckdns/duck.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "url=\"${CONSTRUCTED_URL}\"" | curl -k -s -o "$LOG_FILE" -K -
echo " [$(date '+%Y-%m-%d %H:%M:%S')] DuckDNS Sync Triggered" >> "$LOG_FILE"
