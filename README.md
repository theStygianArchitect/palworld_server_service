# Palworld Dedicated Server Operations Suite & Web Management Plane

An automated, non-disruptive management suite and real-time dashboard for Linux-hosted Palworld dedicated servers.

---

## 🌟 Features

- **🎛️ World Settings Management**:
  - Live Unreal Engine `OptionSettings` INI parser and serializer.
  - Granular multipliers (EXP, capture rates, damage, stamina, structure durability, egg hatching).
  - Isolated Git configuration snapshots with instant diff inspection and one-click rollback.
  - Complete protection of sensitive server parameters (`AdminPassword`, `ServerPassword`, RCON ports).

- **👥 Real-Time Player Roster**:
  - Live session tracking (Level, Ping, Coordinates, User ID).
  - In-game administration tools: Instant broadcast notices, player kick, and permanent ID ban.
  - Persistent player history ledger (`players.json`).

- **📊 Engine & Hardware Telemetry**:
  - Unreal Engine simulation tick rate (FPS), frame delivery times, world in-game day counter, and uptime.
  - Bare-metal metrics: Cgroup slice RAM utilization vs. 14GB cap, NVMe disk allocations, network I/O, and per-core CPU load.

- **🔄 Lifecycle & Reboot Automation**:
  - Automated 4-hour scheduled reboots with progressive in-game HUD countdown warnings (10m, 5m, 3m, 1m, 30s).
  - Automatic world state save before process restart.
  - SteamCMD update orchestration flag handling.

- **🌐 Network & Matchmaking Matrix**:
  - BattleMetrics directory tracking with exponential backoff for 429 rate limits.
  - Automatic DuckDNS 5-minute synchronization and DNS A-record verification.

---

## 🌳 Git Branching & Promotion Strategy

This repository follows a strict promotion pipeline:

```
[feature/<name>] ──(Unit tests pass)──> [dev] ──(Integration tests pass)──> [test] ──(E2E pass)──> [main]
                                                                                                    │
                                                                                           (Production Deploy)
```

1. **`main` (Production)**: Stable production code deployed directly to the dedicated server.
2. **`test` (Staging)**: Multi-version Python testing and integration staging before promoting to production.
3. **`dev` (Development Base)**: Integration branch for ongoing feature development.
4. **`feature/*`**: Isolated feature branches branching from `dev`.

---

## 🚀 Quickstart & Local Development

This project is managed with [uv](https://astral.sh/uv).

### 1. Install Dependencies
```bash
uv sync --extra dev
```

### 2. Run Test Suite
```bash
uv run pytest
```

### 3. Start Local Development Server
```bash
uv run uvicorn app.main:app --reload --port 8000
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 🖥️ Server Installation & Deployment

### Initial Installation (Linux Server)
Run the root installation script as root / sudo:
```bash
chmod +x palworld-run.sh
sudo ./palworld-run.sh
```

This will automatically:
1. Install system prerequisites and `uv`.
2. Provision the isolated `palmanager` service user and POSIX ACLs.
3. Configure scoped `sudoers` privileges.
4. Install systemd services (`palworld-manager.service` on port 8080 and hardened `palworld.service`).
5. Register maintenance crontabs for DuckDNS and automated reboot cycles.

### Zero-Drift Server Updates
To update the server to the latest production release without disrupting in-game players:
```bash
sudo ./scripts/deploy.sh main
```
