# 🚀 Palworld Operations Suite - Gitflow & Production Deployment Guide

This guide outlines how the automated CI/CD pipeline, branch qualification gates, and idempotent deployment scripts operate for **Palworld Operations Suite**.

---

## 🏗️ 1. Branch Hierarchy & Environments

```mermaid
flowchart TD
    A["Feature / Bugfix Branch (feature/*, bugfix/*)"] -->|PR / Qualifications Pass| B["Test Branch (test)"]
    B -->|Gate 1: Quality Check -a + Clean Install Idempotency| C["Staging Branch (dev)"]
    C -->|Gate 2: Multi-Python Matrix (3.10, 3.11, 3.12, 3.13)| D["Production Branch (main)"]
    D -->|Gate 3: Automated Production Deploy| E["Host Git Pull + uv sync + systemctl restart + /health probe"]
```

### Environment Definitions
1. **`feature/*` / `bugfix/*`**: Ephemeral development branches. Anyone can write features or patches here. Changes are committed incrementally as work progresses.
2. **`test` (Integration & Code Qualification Gate)**:
   - All features are merged here first.
   - **Gate 1**: Must pass `./quality_check.sh -a` (zero-leak secrets, AST exception unbundling, pip-audit, bandit, ruff, mypy, pydocstyle, pylint 10.00/10, pytest).
   - **Idempotency Gate**: Must execute `scripts/test_install_idempotency.sh`, verifying that clean installation and repeated installation produce zero state drift.
   - Upon success: Automatically promoted/merged into `dev`.
   - Upon failure: Automatically logs a GitHub bug issue and alerts the developer.
3. **`dev` (Staging Environment)**:
   - Staging environment immediately preceding production.
   - **Gate 2**: Must pass full test matrix on **all supported versions of Python (3.10, 3.11, 3.12, 3.13)**.
   - Upon success: Automatically promoted/merged into `main`.
   - Upon failure: Automatically logs a GitHub bug issue and alerts the developer.
4. **`main` (Production)**:
   - Stable production branch.
   - Triggers `deploy.yml` to pull changes, sync uv dependencies, restart `palworld-manager.service`, and verify `/health` liveness.

---

## 🛠️ 2. Gitflow Automation CLI (`scripts/gitflow.sh`)

Use the included helper script to automate common lifecycle actions:

```bash
# 1. Start a new feature off 'test'
./scripts/gitflow.sh feature add-combat-metrics

# 2. Start a bugfix branch
./scripts/gitflow.sh bugfix fix-udp-packet-drop

# 3. Check current pipeline and branch status
./scripts/gitflow.sh status

# 4. Promote current feature to 'test' (runs quality check + idempotency test)
./scripts/gitflow.sh promote-to-test

# 5. Promote 'test' to 'dev' (runs quality check)
./scripts/gitflow.sh promote-to-dev

# 6. Promote 'dev' to 'main' (runs multi-python matrix verification)
./scripts/gitflow.sh promote-to-main

# 7. Log a bug and create a patch branch
./scripts/gitflow.sh log-bug "A2S Timeout on High Load" "Detailed stack trace..."
```

---

## 🛡️ 3. Recommended Branch Protection Settings (GitHub UI)

To protect `main`, `dev`, and `test` from unverified or direct pushes:

1. In your GitHub repository: **`Settings` $\rightarrow$ `Branches` $\rightarrow$ `Add branch ruleset` / `Add rule`**.
2. Create rules for `main`, `dev`, and `test`:
   - ✅ **Require a pull request before merging**
   - ✅ **Require status checks to pass before merging**:
     - For `test`: `Code Qualifications & Security Audit`, `Clean Install & Idempotency Test`.
     - For `dev`: `Staging Matrix Verification (Python 3.10)`, `(3.11)`, `(3.12)`, `(3.13)`.
   - ✅ **Require branches to be up to date before merging**
   - ✅ **Do not allow bypassing the above settings**

---

## 🔐 4. Configuring Production Deployment Secrets (Optional)

To enable automated zero-downtime SSH deployments to your production server on push to `main`:

1. In your GitHub repository, navigate to **`Settings` $\rightarrow$ `Secrets and variables` $\rightarrow$ `Actions` $\rightarrow$ `New repository secret`**.
2. Configure:

| Secret Name | Description | Example Value |
| :--- | :--- | :--- |
| `PROD_HOST` | Hostname or Public IP of your Linux server | `palworld.yourdomain.duckdns.org` |
| `PROD_USER` | SSH Username on the server | `palmanager` or `steam` |
| `PROD_SSH_KEY` | Private SSH Key content with access to the host | `(Contents of your id_ed25519 private key)` |
| `PROD_PORT` | SSH Port (default: 22) | `22` |
| `PROD_APP_DIR` | Absolute path where the service is cloned | `/opt/palworld-web-manager` |
| `PROD_PUBLIC_URL` | Public base URL for automated `/health` probing | `https://palworld.yourdomain.duckdns.org:8080` |
