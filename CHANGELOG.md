# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- React 19 + Vite SPA (#22, #23)

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
