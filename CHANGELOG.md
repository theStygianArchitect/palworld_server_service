# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- React 19 + Vite SPA (#22, #23)

## [0.5.0] - 2026-09-15

### Added
- Root Privileged IPC Sidecar Daemon (`palworld-supervisor`) — Unix Domain Socket JSON-RPC 2.0 server for isolated host operations. Closes #62.
- `app/supervisor/` package: `protocol.py` (Pydantic v2 schemas), `auth.py` (SO_PEERCRED kernel credential validation), `handlers.py` (whitelisted RPC methods), `server.py` (async UDS server), `client.py` (async IPC client), `main.py` (CLI daemon entrypoint).
- `scripts/palworld-supervisor.service` — systemd unit running as `root` with `ProtectSystem=strict` hardening and `RuntimeDirectory=palmanager`.
- `palmanager` user granted `systemd-journal` group membership for unprivileged journalctl access.
- 36 new tests across `test_supervisor_auth.py`, `test_supervisor_protocol.py`, and `test_supervisor_server.py`.

### Changed
- **BREAKING**: All privileged operations (service restart, deploy, reboot) now route through supervisor IPC instead of direct `sudo` subprocess invocation.
- `app/engine/service.py`: Systemctl restart replaced with `SupervisorClient.restart_service()`.
- `app/routers/system.py`: Manager service restart replaced with supervisor IPC dispatch.
- `app/engine/updater.py`: Deploy execution replaced with `SupervisorClient.trigger_deploy()`.
- `app/monitoring/log_scraper.py`: Journalctl commands no longer use `sudo` prefix (authorized via `systemd-journal` group).
- `scripts/palworld-manager.service`: Added `Wants=palworld-supervisor.service` dependency; removed `/etc/sudoers.d` from `ReadWritePaths`.
- `scripts/deploy.sh` and `scripts/install.sh`: Removed sudoers provisioning, added supervisor daemon installation and legacy sudoers cleanup.

### Removed
- `/etc/sudoers.d/palmanager` drop-in — superseded by supervisor daemon architecture.
- All `sudo -n` subprocess calls from application code (zero sudo static audit verified).

## [0.4.5] - 2026-09-14

### Added
- Closed-loop game server reboot lifecycle states in `app/templates/index.html`: added `#rebootSuccessToast` for confirmed online readiness and $>90$s stall watchdog `#rebootStallWarning` with direct diagnostic log navigation. Closes #60.
- Real-time WebSocket connection health status pill (`#wsConnectionBadge`) in `app/templates/index.html` header featuring closed-loop bounded exponential backoff (1s, 2s, 4s, 8s, 16s) and 1-click manual reconnection escape hatch.
- Insecure plaintext HTTP detection and 1-click HTTPS upgrade banners (`#insecureHttpBanner`) across auxiliary portals `app/templates/feedback.html` and `app/templates/metrics.html`.
- Regression UI contract test coverage in `tests/test_rbac_ui_contract.py` asserting DOM structure, handlers, and state machines across all templates.
- Strict pull request merge stopgap rules (`git-merge-stopgap.md`, `GEMINI.md`, `implementation-architect` v1.8.0) forbidding autonomous merges to `main` without human authorization.

## [0.4.4] - 2026-09-14

### Added
- ACME DNS-01 challenge client (`app/engine/acme_client.py`) implementing RFC 8555 for automated Let's Encrypt certificate provisioning via DuckDNS TXT records.
- Tiered TLS provisioning strategy: Tier 1 ACME/Let's Encrypt → Tier 2 self-signed fallback with loud warning logs on ACME failure.
- Resilient `ImportError` guard in `app/main.py` — service boots in degraded mode if `cryptography` is temporarily unavailable during upgrade window.
- Regression tests for ACME provisioning flow, self-signed fallback, and token-absent skip behavior.
- Closed-loop TLS reactivation overlay (`pollTlsReconnection`) with cross-origin socket liveness probing, visual stage indicators, and attempt telemetry in `app/templates/index.html`.
- 1-click "Switch to Secure HTTPS" upgrade banners across insecure plaintext HTTP sessions in `app/templates/index.html` (dismissible with session storage persistence) and `app/templates/login.html` (instant pre-auth `/canonical` redirect).
- Created `ui-ux-architect` skill and updated `implementation-architect` v1.7.0 to mandate 6-state UX lifecycle parity and eliminate blind timers.

### Fixed
- `provision_tls_certificates()` now executes ACME DNS-01 flow before falling through to self-signed (previously always generated self-signed, ignoring the `token` parameter). Closes #54.
- Deployment step order inverted: Python dependencies (`cryptography`) now install (Step 2) before application code sync (Step 3), preventing `ImportError` crash loops. Closes #51.
- `POST /api/system/tls/renew` respects `payload.force` instead of hardcoding `force=True`.
- `GET /api/system/tls/status` reports `auto_renew_active=True` reflecting the active `palworld-cert-renew.timer`.
- `palworld-cert-renew.service` now runs as `User=palmanager` / `Group=palmanager` instead of root.
- Sudoers expanded for `palworld-manager.service` restart; TLS renewal handler dispatches delayed restart and auto-redirects web client to canonical HTTPS URL. Closes #58.
- `scripts/install.sh` includes `cryptography` in venv pip install (previously omitted).

### Removed
- Certbot OS package installation from `scripts/deploy.sh` and `scripts/install.sh` (decommissioned — replaced by native Python ACME client).
- Legacy DuckDNS `.env` sync to `/home/steam/duckdns/` from `scripts/deploy.sh` (obsolete `duck.sh` remnant).
- Legacy `/etc/letsencrypt` and `/var/log/letsencrypt` from `palworld-manager.service` `ReadWritePaths`.

### Security
- Production domains now receive trusted Let's Encrypt certificates instead of self-signed, enabling external browser access. Closes #55.

## [0.4.3] - 2026-09-14

### Added
- Pure Python TLS Certificate Engine (`app/engine/tls_manager.py`) built with `cryptography` and `httpx`.
- Automatic dual-tier resilience with self-signed certificate fallback ensuring port 8080 is guaranteed to bind with HTTPS.
- Native in-process DuckDNS dynamic DNS updater (`sync_duckdns_ip`).
- CLI entrypoints for TLS management: `python -m app.engine.tls_manager renew`, `sync-dns`, `status`.

### Changed
- Portal "Renew Certificate Now" button directly invokes Python in-process renewal without `sudo` or shell scripts (#21).
- System startup lifespan provisions TLS certificates and syncs DuckDNS dynamic IP directly via Python `httpx`.
- Systemd timer `palworld-cert-renew.service` executes `.venv/bin/python -m app.engine.tls_manager renew`.

### Removed
- Decommissioned 5 legacy shell scripts: `scripts/palworld-cert-manager.sh`, `scripts/certbot-duckdns-auth.sh`, `scripts/certbot-duckdns-cleanup.sh`, `scripts/palworld-cert-deploy-hook.sh`, and `scripts/duck.sh`.
- Removed `palworld-cert-manager.sh` from `/etc/sudoers.d/palmanager` configuration.

## [0.4.2] - 2026-09-13

### Changed
- Decomposed monolithic `app/main.py` into 7 modular domain APIRouters under `app/routers/` (`auth`, `settings`, `players`, `telemetry`, `system`, `feedback`, `ui`) with a centralized dependency injection hub (`app/routers/deps.py`) and reduced `app/main.py` from 2,496 lines to 442 lines (#21).

## [0.4.1] - 2026-09-13

### Added
- Automated Let's Encrypt TLS provisioning during deployment, installation, and application startup lifecycle (#40)
- End-to-end atomic HTTPS redirection and login navigation contract tests (#40)
- Logged backlog Issue #46 for test architecture formalization, "One Concept Per Test" guidelines, and Hypothesis property-based testing

### Changed
- Adaptive header branding navigation reflecting live TLS state to prevent client-side handshake rejections (#40)
- Symmetrical header branding anchor and TLS status indicator on login view (#40)
- Dynamic SemVer synchronization and cross-file equality contracts in governance test suite

## [0.4.0] - 2026-09-12

### Added
- Canonical Header Navigation Link & HTTP-to-HTTPS Redirection Validation (#45, closes #40)

## [0.3.1] - 2026-09-11

### Fixed
- Deployer In-Place Self-Overwrite Elimination via Out-of-Tree Runner Staging (#44, closes #43)

## [0.3.0] - 2026-09-11

### Added
- Atomic Config Persistence Engine with crash-resilient INI writes, multi-threaded locking, and rotating backups (#42, closes #20)
- Release deployment primitives and safe archive staging engine (`app/engine/deployer.py`) with path traversal guards and atomic directory swapping (#42, closes #20)
- Comparative defect reproducer test suite contrasting naive `write_text()` mid-write truncation vs atomic persistence (#42, closes #20)

### Changed
- Converted settings persistence in `app/main.py` and reboot sync in `app/engine/service.py` to use `atomic_write_ini` (#42, closes #20)

## [0.2.1] - 2026-09-11

### Added
- Package Build & Distribution (uv build) CI Gate (#39, closes #38)

### Fixed
- Deployer Self-Overwrite & Atomic Script Staging (#39, closes #38)

## [0.2.0] - 2026-09-11

### Added
- Issue-to-Test Traceability & Governance Contracts (#35, closes #34)
- Staged Settings Reboot Persistence (#31, closes #30)

### Changed
- Deployer Zero-Drift & Step Harmonization (#33, closes #32)
- Systemd Mount Sandbox Relaxation (#31, closes #30)

### Fixed
- Git Dubious Ownership Exit 128 (#33, closes #32)
- Sudoers Drop-In Harmonization (#31, closes #30)
- Frontend HTTP 422 Error Formatting (#31, closes #30)

## [0.1.2] - 2026-09-10

### Added
- Automated HTTPS/TLS via Let's Encrypt (#25, closes #24)
- Unified Real-Time Deployment Progression Engine (#27, closes #26)

### Fixed
- TLS Renewal Sudo Privilege & RBAC Gating (#29, closes #28)

## [0.1.1] - 2026-09-09

### Added
- REST API Hardening & In-Game Moderation (#15, closes #13)
- Game Server Probe Diagnostics & System Updates Tab (#17, closes #16)

### Fixed
- Inline JavaScript Syntax Error & Git Discovery (#18, closes #19)
- RBAC UI Visibility Matrix (#17, closes #16)
- First-Spin Setup Modal & Security Hardening (#14, closes #12)

## [0.1.0] - 2026-09-08

### Added
- Dynamic Admin Credentials Bootstrap (#10, closes #9)
- In-App Portal Self-Updater (#11, closes #8)
