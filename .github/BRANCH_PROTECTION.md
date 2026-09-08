# GitHub Branch Protection & Automated Promotion Policy

This repository implements a multi-tier automated promotion pipeline. Direct manual commits to `test`, `dev`, and `main` are strictly prohibited.

---

## 🏛️ Branch Hierarchy & Governance Rules

| Branch | Purpose | Permitted Ingress | Egress / Promotion | Update Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| `feature/*`, `bugfix/*` | Developer work | Local developer workstations | PR or `./scripts/gitflow.sh promote-to-test` | Manual Git commits |
| `test` | Integration & Quality Gate | PRs from feature/bugfix branches, CLI promotion | Auto-promotes to `dev` upon Gate 1 pass | Merges via PR or Gitflow CLI |
| `dev` | Staging & Multi-Python Matrix | Automated only | Auto-promotes to `main` upon Gate 2 pass | **Bot only** (`github-actions[bot]`) |
| `main` | Production & Release Deploy | Automated only | Deployment (`deploy.yml`) | **Bot only** (`github-actions[bot]`) |

---

## 🔒 Recommended GitHub Repository Settings

To enforce this governance policy on GitHub (`Settings` -> `Branches` -> `Branch protection rules`):

### 1. Branch Protection Rule: `test`
- **Branch name pattern**: `test`
- [x] **Require a pull request before merging**
  - Require approvals: `1` (or optional for solo maintainer)
  - Dismiss stale pull request approvals when new commits are pushed: `true`
- [x] **Require status checks to pass before merging**
  - Require branches to be up to date before merging: `true`
  - Required checks:
    - `Code Qualifications & Security Audit`
    - `Clean Install & Idempotency Test`
- [x] **Do not allow bypassing the above settings**
- [x] **Restrict deletions**

### 2. Branch Protection Rule: `dev` (Staging - Automated Only)
- **Branch name pattern**: `dev`
- [x] **Restrict who can push to matching branches**:
  - Allow only: `github-actions[bot]` (or Repository Admins)
- [x] **Require status checks to pass before merging**:
  - Required checks:
    - `Staging Matrix Verification (Python 3.10)`
    - `Staging Matrix Verification (Python 3.11)`
    - `Staging Matrix Verification (Python 3.12)`
    - `Staging Matrix Verification (Python 3.13)`
- [x] **Block force pushes and deletions**
- [x] **Reject PRs**: Any PR opened targeting `dev` will be automatically rejected by the `PR Target Policy Enforcement` workflow (`pr_target_enforcement.yml`).

### 3. Branch Protection Rule: `main` (Production - Automated Only)
- **Branch name pattern**: `main`
- [x] **Restrict who can push to matching branches**:
  - Allow only: `github-actions[bot]` (or Repository Admins)
- [x] **Require status checks to pass before merging**:
  - Required checks:
    - `Verify Quality & Security Gate`
- [x] **Block force pushes and deletions**
- [x] **Reject PRs**: Any PR opened targeting `main` will be automatically rejected by the `PR Target Policy Enforcement` workflow (`pr_target_enforcement.yml`).

---

## ⚙️ Automated Workflow Chain (`workflow_run`)

The promotion pipeline flows automatically through GitHub Actions event chaining:

```
[Push / Merge to test]
        │
        ▼
   quality_gate.yml (Gate 1: Code Qualifications, Secret Scan, Idempotency)
        │
        ├── (Success on push event)
        ▼
   Auto-Promote to dev (Git push by bot)
        │
        ▼
   staging_matrix.yml (Gate 2: Python 3.10, 3.11, 3.12, 3.13 Matrix)
        │
        ├── (Success on workflow_run event)
        ▼
   Auto-Promote to main (Git push by bot)
        │
        ▼
   deploy.yml (Gate 3: Production Deployment & Health Probes)
```
