"""Physical data type contracts, TypedDict definitions, and domain aliases.

This module houses all pure type definitions and schema-less data structures
used across the Palworld Operations Suite, isolating them from runtime validation logic
in compliance with 3 AM Architecture & Type Isolation standards.
"""

from __future__ import annotations

from typing import TypedDict

# =========================================================================
# 1. Community & Network Discovery Types
# =========================================================================


class TopBadgeInfo(TypedDict):
    """Header badge styling and text configuration for the dashboard.

    Attributes:
        label (str): Human-readable badge text displayed in the header.
        style (str): Tailwind CSS class styling for background and border.
        dot (str): Tailwind CSS class styling for the status indicator dot.
    """

    label: str
    style: str
    dot: str


class LogScraperInfo(TypedDict):
    """Telemetry extracted from systemd journalctl for EOS console lobbies.

    Attributes:
        registered (bool): Whether an EOS public lobby registration was found.
        session_id (str | None): Active EOS lobby session identifier string.
        first_seen (str | None): Formatted timestamp when session was first logged.
        last_line (str): Raw matched journal line.
        status_label (str): Status string for UI display.
        status_color (str): Tailwind color theme string.
        crossplay_platforms (str): Comma-separated list of confirmed platforms.
    """

    registered: bool
    session_id: str | None
    first_seen: str | None
    last_line: str
    status_label: str
    status_color: str
    crossplay_platforms: str


class PocketpairMasterInfo(TypedDict):
    """Official Pocketpair master server directory listing probe data.

    Attributes:
        listed (bool): Whether the server was found in Pocketpair's public API.
        server_id (str): Unique Pocketpair server identifier or 'Unlisted'.
        name (str): Server name indexed in the master directory.
        version (str): Game build version reported by the directory.
        status_label (str): Display text for directory listing status.
        status_color (str): Tailwind color theme string.
    """

    listed: bool
    server_id: str
    name: str
    version: str
    status_label: str
    status_color: str


class NetworkMatrixInfo(TypedDict):
    """Network alignment and connection address matrix.

    Attributes:
        public_ip (str): Detected external WAN IPv4 address.
        dns_ip (str): Resolved IPv4 address from DuckDNS hostname.
        domain (str): DuckDNS domain name.
        is_aligned (bool): True if public WAN IP matches DuckDNS DNS record.
        lan_ip (str): Local network IP address.
        public_port (int): UDP game port.
        direct_connect_addr (str): Formatted public address string (host:port).
        lan_connect_addr (str): Formatted LAN address string (host:port).
    """

    public_ip: str
    dns_ip: str
    domain: str
    is_aligned: bool
    lan_ip: str
    public_port: int
    direct_connect_addr: str
    lan_connect_addr: str


class DiscoveryHubPayload(TypedDict):
    """Complete 3-card discovery and networking payload.

    Attributes:
        log_scraper (LogScraperInfo): EOS journal scraper telemetry.
        pocketpair_master (PocketpairMasterInfo): Official directory probe data.
        network_matrix (NetworkMatrixInfo): WAN/DNS alignment matrix.
    """

    log_scraper: LogScraperInfo
    pocketpair_master: PocketpairMasterInfo
    network_matrix: NetworkMatrixInfo


# =========================================================================
# 2. Server Telemetry, Security & Hardware Types
# =========================================================================


class SteamA2SInfo(TypedDict):
    """Valve Steam A2S_INFO binary UDP query telemetry.

    Attributes:
        responsive (bool): Whether the UDP socket replied to A2S_INFO.
        ping_ms (float | None): Measured round-trip latency in milliseconds.
        server_name (str): Server name parsed from response packet.
        map_name (str): Active map name parsed from response packet.
        folder (str): Game folder directory name.
        game (str): Game name identifier string.
    """

    responsive: bool
    ping_ms: float | None
    server_name: str
    map_name: str
    folder: str
    game: str


class SecurityMatrixInfo(TypedDict):
    """Server access control and security configuration status.

    Attributes:
        is_password_protected (bool): Whether a player join password is set.
        password_status_label (str): Status label with emoji for the UI.
        server_password (str): Current plaintext server join password.
        rcon_port (int): Admin RCON listening port.
        rest_port (int): Admin REST API listening port.
        max_players (int): Maximum player slot capacity.
        current_players (int): Number of currently connected tamers.
        slot_capacity_label (str): Formatted capacity string (e.g. '4 / 32 Tamers').
    """

    is_password_protected: bool
    password_status_label: str
    server_password: str
    rcon_port: int
    rest_port: int
    max_players: int
    current_players: int
    slot_capacity_label: str


class PlayerCoordinates(TypedDict):
    """In-game spatial coordinates of a connected player.

    Attributes:
        x (float): World space X coordinate.
        y (float): World space Y coordinate.
    """

    x: float
    y: float


class PlayerRecord(TypedDict):
    """Individual player ledger record for active and historical tamers.

    Attributes:
        playerId (str): Unique in-game player ID string.
        userId (str): Platform account identifier.
        name (str): Character or Steam account display name.
        level (int): Current player character level.
        ping (float): Latency in milliseconds to the server.
        location (PlayerCoordinates): Current or last-known world coordinates.
        status (str): Connection status ('ONLINE' or 'OFFLINE').
        last_seen (str): Timestamp string when last active on server.
    """

    playerId: str
    userId: str
    name: str
    level: int
    ping: float
    location: PlayerCoordinates
    status: str
    last_seen: str


class PlayerLedgerMatrix(TypedDict):
    """Aggregate player roster including active and offline historic tamers.

    Attributes:
        active_count (int): Count of currently connected tamers.
        total_registered (int): Total count of unique tamers seen.
        active_players (list[PlayerRecord]): List of online player records.
        offline_players (list[PlayerRecord]): List of offline player records.
    """

    active_count: int
    total_registered: int
    active_players: list[PlayerRecord]
    offline_players: list[PlayerRecord]


class HardwareTelemetryInfo(TypedDict):
    """Host bare-metal resource consumption and cgroup memory limits.

    Attributes:
        host_ram_used_gb (float): Host physical RAM used in gigabytes.
        host_ram_total_gb (float): Total host physical RAM in gigabytes.
        host_ram_pct (float): Total host RAM utilization percentage.
        cgroup_ram_used_gb (float): Memory used by palworld.service cgroup in GB.
        cgroup_limit_gb (float): Memory limit enforced on the cgroup in GB.
        cgroup_ram_pct (float): Percentage of cgroup memory limit consumed.
        swap_used_gb (float): Swap space used in gigabytes.
        swap_total_gb (float): Total swap space allocated in gigabytes.
        swap_pct (float): Percentage of swap memory used.
        cpu_cores (list[float]): Per-core CPU utilization percentage list.
        cpu_avg_pct (float): Average CPU utilization across all cores.
        disk_used_gb (float): Root filesystem disk space used in gigabytes.
        disk_total_gb (float): Total root filesystem capacity in gigabytes.
        disk_pct (float): Percentage of disk capacity used.
        net_bytes_sent (int): Total network bytes transmitted.
        net_bytes_recv (int): Total network bytes received.
    """

    host_ram_used_gb: float
    host_ram_total_gb: float
    host_ram_pct: float
    cgroup_ram_used_gb: float
    cgroup_limit_gb: float
    cgroup_ram_pct: float
    swap_used_gb: float
    swap_total_gb: float
    swap_pct: float
    cpu_cores: list[float]
    cpu_avg_pct: float
    disk_used_gb: float
    disk_total_gb: float
    disk_pct: float
    net_bytes_sent: int
    net_bytes_recv: int


class CombinedTelemetryPayload(TypedDict):
    """Aggregate combined telemetry matrix dispatched to dashboard clients.

    Attributes:
        top_badge (TopBadgeInfo): Header status badge info.
        discovery_hub (DiscoveryHubPayload): 3-card discovery telemetry.
        a2s_telemetry (SteamA2SInfo): Steam A2S UDP query data.
        security_matrix (SecurityMatrixInfo): Access control and slot info.
    """

    top_badge: TopBadgeInfo
    discovery_hub: DiscoveryHubPayload
    a2s_telemetry: SteamA2SInfo
    security_matrix: SecurityMatrixInfo


# =========================================================================
# 3. Engine & Orchestration Types
# =========================================================================


class EngineMetrics(TypedDict):
    """In-engine operational metrics returned by the REST API.

    Attributes:
        server_fps (int): Server tick rate in frames per second.
        server_frame_time_ms (float): Tick processing duration in milliseconds.
        uptime_seconds (int): Engine uptime in seconds.
        days (int): In-game world days elapsed.
        current_players (int): Number of connected players.
        max_players (int): Maximum player slot capacity.
    """

    server_fps: int
    server_frame_time_ms: float
    uptime_seconds: int
    days: int
    current_players: int
    max_players: int


class ReadinessInfo(TypedDict):
    """Server initialization readiness check response.

    Attributes:
        ready (bool): True if server REST API is accepting commands.
        version (str | None): Palworld engine release build version string.
        server_name (str): Server name reported by the engine.
    """

    ready: bool
    version: str | None
    server_name: str


class LifecycleState(TypedDict):
    """Server reboot countdown and maintenance lifecycle state.

    Attributes:
        phase (str): Current lifecycle phase ('IDLE', 'COUNTDOWN', 'SAVING', 'MAINTENANCE', 'PROBING').
        remaining_seconds (int): Seconds remaining until restart.
        total_seconds (int): Total duration of scheduled countdown.
        current_broadcast (str): Active in-game broadcast message text.
        is_updating (bool): Whether a SteamCMD update will be executed.
    """

    phase: str
    remaining_seconds: int
    total_seconds: int
    current_broadcast: str
    is_updating: bool


# =========================================================================
# 4. Git Backup & Versioning Types
# =========================================================================


class GitCommitInfo(TypedDict):
    """Historical snapshot commit entry from isolated Git repository.

    Attributes:
        hash (str): Short Git commit hash identifier.
        message (str): Commit summary message describing the change.
        date (str): ISO 8601 formatted commit timestamp.
    """

    hash: str
    message: str
    date: str
