# GitHub Branch Protection Policy (Unified GitHub Flow)

This repository follows **Modern GitHub Flow**. All direct commits to `main` are prohibited. All development occurs on ephemeral topic branches (`feature/*`, `bugfix/*`) and merges into `main` strictly through Pull Requests that pass all automated status checks.

---

## 🏛️ Branch Hierarchy & Governance

| Branch | Purpose | Permitted Ingress | Egress / Deployment | Gate Enforcement |
| :--- | :--- | :--- | :--- | :--- |
| `feature/*`, `bugfix/*` | Feature & patch development | Local developer workstations | Pull Request to `main` | `./quality_script.sh -a` (local) |
| `main` | Production & Release Source of Truth | Pull Requests only | `deploy.yml` on PR merge | 15 parallel status checks + `ci-gate` |

---

## ⚡ Automated Setup via Script

You can configure this branch protection policy instantly on GitHub using our universal automation script:

```bash
# Preview the JSON API payload (no credentials needed)
./scripts/setup_branch_protection.sh --dry-run

# Apply branch protection to main via GitHub CLI or GH_TOKEN
./scripts/setup_branch_protection.sh
```

---

## 🔒 Exact GitHub Repository Settings (`main`)

- **Target Branch**: `main`
- [x] **Require a pull request before merging**
  - Required approvals: `0` (solo/status-check gate) or `1+` (team reviews)
  - Dismiss stale pull request approvals when new commits are pushed: `true`
- [x] **Require status checks to pass before merging**
  - Require branches to be up to date before merging: `true`
  - **Required Status Check Contexts**:
    1. `All CI Quality Gates Passed` (`ci-gate`)
    2. `Zero-Leak Secret Scan`
    3. `Project Suppression Audit`
    4. `AST Exception & Logging Audit`
    5. `Dependency Vulnerability Audit`
    6. `Static Security Analysis (Bandit)`
    7. `Code Linting & Formatting (Ruff)`
    8. `Strict Type Analysis (Mypy)`
    9. `Pylint Standard (10.00/10)`
    10. `PEP 8 Formatting (pycodestyle)`
    11. `Google Docstring Validation`
    12. `Unit Tests & Coverage (pytest)`
    13. `Clean Install & Idempotency Test`
    14. `Matrix Test (Python 3.10)`
    15. `Matrix Test (Python 3.11)`
    16. `Matrix Test (Python 3.12)`
    17. `Matrix Test (Python 3.13)`
- [x] **Block force pushes (`--force`)**
- [x] **Block branch deletions**
