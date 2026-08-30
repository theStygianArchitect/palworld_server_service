#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Palworld Dedicated Server - Pre-Restart Maintenance & SteamCMD Updater
# ==============================================================================

LOG_FILE="/home/steam/palserver_maintenance.log"
SAVED_DIR="/home/steam/.steam/steam/steamapps/common/PalServer/Pal/Saved"
BACKUP_DIR="/home/steam/Palworld_backups"
APP_ID="2394010"
RETENTION_DAYS="10"
UPDATE_FLAG_FILE="/home/steam/.update_requested"

{
    echo "========================================================="
    echo "Maintenance started on: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================================="

    FREE_KB=$(df -k --output=avail "$SAVED_DIR" 2>/dev/null | tail -n1 || echo 99999999)
    if [ "$FREE_KB" -lt 5242880 ]; then
        echo "CRITICAL: Less than 5GB disk space available. Aborting maintenance."
        exit 1
    fi

    if [ -f "$UPDATE_FLAG_FILE" ]; then
        echo "[1/3] Update flag present. Executing SteamCMD update for AppID: $APP_ID..."
        /usr/games/steamcmd +login anonymous +app_update "$APP_ID" validate +quit
        if [ $? -eq 0 ]; then
            echo "--> SteamCMD update finished successfully."
            rm -f "$UPDATE_FLAG_FILE"
        else
            echo "ERROR: SteamCMD update failed. Halting."
            exit 1
        fi
    else
        echo "[1/3] Normal restart detected: bypassing SteamCMD update."
    fi

    echo "[2/3] Archiving world data to ${BACKUP_DIR}..."
    mkdir -p "$BACKUP_DIR"
    BACKUP_NAME="Palworld_$(date '+%Y-%m-%d_%H-%M-%S').tar.gz"
    if [ -d "$SAVED_DIR" ]; then
        tar -czvf "${BACKUP_DIR}/${BACKUP_NAME}" "$SAVED_DIR"
        echo "--> Backup generated: $BACKUP_NAME"
    fi

    echo "[3/3] Pruning backups older than $RETENTION_DAYS days..."
    find "$BACKUP_DIR" -mtime "+$RETENTION_DAYS" -type f -name "*.tar.gz" -delete -print
    echo "========================================================="
    echo "Maintenance completed on: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================================="
} >> "$LOG_FILE" 2>&1
