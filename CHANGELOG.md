# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Atomic Config Persistence Engine with crash-resilient INI writes, multi-threaded locking, and rotating backups (#20)
- Release deployment primitives and safe archive staging engine (`app/engine/deployer.py`) with path traversal guards and atomic directory swapping (#20)
- Comparative defect reproducer test suite contrasting naive `write_text()` mid-write truncation vs atomic persistence (`#20`)
- Package Build & Distribution (uv build) CI Gate (#38)

### Changed
- Converted settings persistence in `app/main.py` and reboot sync in `app/engine/service.py` to use `atomic_write_ini` (#20)

### Fixed
- Deployer In-Place Self-Overwrite Elimination via Out-of-Tree Runner Staging (#43, closes #43)
- Deployer Self-Overwrite & Atomic Script Staging (#38)

### Planned
- Modular APIRouters (#21)
- React 19 + Vite SPA (#22, #23)

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
