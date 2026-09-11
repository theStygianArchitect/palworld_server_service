#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Palworld Dedicated Server - Pre-Restart Maintenance & SteamCMD Updater
# ==============================================================================

LOG_FILE="/home/steam/palserver_maintenance.log"
BACKUP_DIR="/home/steam/Palworld_backups"
APP_ID="2394010"
RETENTION_DAYS="10"

# Multi-path update flags (web manager POSIX ACL + steam home fallback)
UPDATE_FLAG_1="/var/lib/palmanager/update_requested"
UPDATE_FLAG_2="/home/steam/.update_requested"

# Locate steamcmd binary dynamically
STEAMCMD_BIN=""
for candidate in "/usr/games/steamcmd" "/usr/bin/steamcmd" "/home/steam/steamcmd/steamcmd.sh" "/home/steam/.steam/steamcmd/steamcmd.sh"; do
    if [ -x "$candidate" ]; then
        STEAMCMD_BIN="$candidate"
        break
    fi
done
if [ -z "$STEAMCMD_BIN" ]; then
    STEAMCMD_BIN=$(command -v steamcmd 2>/dev/null || true)
fi

# Locate PalServer installation directory
INSTALL_DIR="/home/steam/.steam/steam/steamapps/common/PalServer"
if [ ! -d "$INSTALL_DIR" ]; then
    for candidate in "/home/steam/Steam/steamapps/common/PalServer" "/home/steam/PalServer" "/opt/palworld"; do
        if [ -d "$candidate" ]; then
            INSTALL_DIR="$candidate"
            break
        fi
    done
fi
SAVED_DIR="${INSTALL_DIR}/Pal/Saved"

{
    echo "========================================================="
    echo "Maintenance started on: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Install Directory:      ${INSTALL_DIR}"
    echo "SteamCMD Binary:        ${STEAMCMD_BIN:-NOT FOUND}"
    echo "========================================================="

    FREE_KB=$(df -k --output=avail "${INSTALL_DIR}" 2>/dev/null | tail -n1 || echo 99999999)
    if [ "$FREE_KB" -lt 5242880 ]; then
        echo "CRITICAL: Less than 5GB disk space available. Aborting maintenance."
        exit 1
    fi

    UPDATE_REQUESTED=0
    if [ -s "$UPDATE_FLAG_1" ] || [ -s "$UPDATE_FLAG_2" ]; then
        UPDATE_REQUESTED=1
    elif [ -f "$UPDATE_FLAG_1" ] || [ -f "$UPDATE_FLAG_2" ]; then
        if [ -f "$UPDATE_FLAG_1" ] && [ -n "$(find "$UPDATE_FLAG_1" -mmin -15 2>/dev/null)" ]; then
            UPDATE_REQUESTED=1
        elif [ -f "$UPDATE_FLAG_2" ] && [ -n "$(find "$UPDATE_FLAG_2" -mmin -15 2>/dev/null)" ]; then
            UPDATE_REQUESTED=1
        fi
    fi

    if [ "$UPDATE_REQUESTED" -eq 1 ]; then
        echo "[1/3] Update flag present. Executing SteamCMD update for AppID: ${APP_ID} into ${INSTALL_DIR}..."
        if [ -z "$STEAMCMD_BIN" ]; then
            echo "ERROR: steamcmd executable not found in system or home paths. Halting update."
            exit 1
        fi

        "$STEAMCMD_BIN" +login anonymous +force_install_dir "${INSTALL_DIR}" +app_update "${APP_ID}" validate +quit
        STEAM_EXIT=$?
        if [ $STEAM_EXIT -eq 0 ]; then
            echo "--> SteamCMD update finished successfully."
            : > "$UPDATE_FLAG_1" 2>/dev/null || true
            : > "$UPDATE_FLAG_2" 2>/dev/null || true
            rm -f "$UPDATE_FLAG_1" "$UPDATE_FLAG_2" 2>/dev/null || true
        else
            echo "ERROR: SteamCMD update returned exit code: ${STEAM_EXIT}. Halting."
            exit 1
        fi
    else
        echo "[1/3] Normal restart detected: bypassing SteamCMD update."
    fi

    echo "[2/3] Archiving world data to ${BACKUP_DIR}..."
    mkdir -p "$BACKUP_DIR"
    BACKUP_NAME="Palworld_$(date '+%Y-%m-%d_%H-%M-%S').tar.gz"
    if [ -d "$SAVED_DIR" ]; then
        tar -czvf "${BACKUP_DIR}/${BACKUP_NAME}" -C "${INSTALL_DIR}/Pal" Saved
        echo "--> Backup generated: $BACKUP_NAME"
    fi

    echo "[3/3] Pruning backups older than $RETENTION_DAYS days..."
    find "$BACKUP_DIR" -mtime "+$RETENTION_DAYS" -type f -name "*.tar.gz" -delete -print 2>/dev/null || true

    echo "[4/4] Checking for staged world configuration..."
    STAGED_INI_1="/var/lib/palmanager/staged_PalWorldSettings.ini"
    STAGED_INI_2="/home/steam/.staged_PalWorldSettings.ini"
    TARGET_INI="${SAVED_DIR}/Config/LinuxServer/PalWorldSettings.ini"

    for staged in "$STAGED_INI_1" "$STAGED_INI_2"; do
        if [ -f "$staged" ]; then
            echo "--> Staged configuration detected at $staged. Synchronizing to $TARGET_INI..."
            mkdir -p "$(dirname "$TARGET_INI")"
            cp -f "$staged" "$TARGET_INI"
            chown steam:steam "$TARGET_INI" 2>/dev/null || true
            chmod 0644 "$TARGET_INI" 2>/dev/null || true
            rm -f "$staged"
            echo "--> Staged configuration synchronized successfully."
            break
        fi
    done
    echo "========================================================="
    echo "Maintenance completed on: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================================="
} >> "$LOG_FILE" 2>&1
