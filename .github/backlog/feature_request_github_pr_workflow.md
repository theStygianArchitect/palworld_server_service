---
name: Feature Request: Standard Open Source GitHub Pull Request (PR) Workflow
about: Transition Gitflow promotion automation to standard GitHub Pull Requests with branch protection and automated review gates
labels: enhancement, ci-cd, gitflow, backlog
---

# Feature Request: Transition to Standard Open Source GitHub Pull Request (PR) Workflow

## 🚀 Feature Proposal
Transition the repository promotion lifecycle from local/bot direct-push branches into a standardized GitHub **Pull Request (PR)** workflow. This introduces gh pr create, GitHub Actions PR status checks, branch protection policies on 	est, dev, and main, auto-merge queues (gh pr merge --auto), and complete inline code review audit trails matching top-tier open-source projects.

## 🎯 Problem / User Story

### Current State
- The workflow currently relies on direct-push promotions:
  - Developers run ./scripts/gitflow.sh promote-to-test, which verifies the code locally, merges into 	est, and pushes directly to origin/test.
  - GitHub Actions runs on push to 	est, and upon success, a bot step pushes directly to origin/dev.
- **Limitations**:
  - While this design maximizes velocity for solo development, it bypasses standard GitHub Pull Request interfaces where external contributors, maintainers, and reviewers collaborate.
  - No inline line-by-line review comments or formal approvals before code merges into 	est or dev.
  - Standard GitHub branch protection rules (e.g. *Require pull request before merging*) cannot currently be enabled because direct git push commands from workstations and bot tokens would be blocked with GH006: Protected branch update failed.

### User Story
*As an open-source contributor and repository maintainer, I want all feature branches and release promotions to flow through GitHub Pull Requests with automated status checks and branch protection so that our collaboration model follows industry standards, enables peer code reviews, and maintains an immutable audit trail of discussions and approvals.*

## 💡 Proposed Solution & Architecture

### 1. Unified Gitflow CLI Pull Request Integration (scripts/gitflow.sh)
Update scripts/gitflow.sh commands to create and manage Pull Requests via GitHub CLI (gh):
`ash
# 1. Feature -> Test (Gate 1 Integration)
./scripts/gitflow.sh promote-to-test
# Action: Runs local quality gate -> Pushes feature/<name> to origin -> Creates PR to 'test' via 'gh pr create' -> (Optional) Sets auto-merge

# 2. Test -> Dev (Gate 2 Staging)
./scripts/gitflow.sh promote-to-dev
# Action: Opens PR 'test -> dev' with changelog and test suite summary

# 3. Dev -> Main (Gate 3 Production Release)
./scripts/gitflow.sh promote-to-main
# Action: Opens release PR 'dev -> main' with matrix verification report
`

### 2. GitHub Actions PR Check Matrix (.github/workflows/quality_gate.yml)
- Trigger CI on pull_request events targeting 	est, dev, and main:
  `yaml
  on:
    pull_request:
      branches: [test, dev, main]
  `
- Post actionable status checks directly to the PR interface:
  - Code Qualifications & Security Audit (
uff, mypy, pylint, andit, pip-audit, pytest)
  - Clean Install & Idempotency Gate (multi-distro verification)
  - Multi-Python Matrix (Python 3.10 - 3.13)
- Enable automated PR merge upon status check success:
  - gh pr merge <pr_number> --auto --squash or --merge

### 3. GitHub Branch Protection Policies
Configure repository branch protection rules for 	est, dev, and main:
- [x] **Require a pull request before merging**
- [x] **Require status checks to pass before merging**:
  - Code Qualifications & Security Audit
  - Clean Install & Idempotency Test
- [x] **Require branches to be up to date before merging**
- [x] **Block force pushes and deletions**

## 🧱 12-Factor & Governance Compliance
- **Zero Token Hardcoding**: All PR interactions utilize ephemeral secrets.GITHUB_TOKEN within CI or local authenticated gh session.
- **Fail-Closed Security**: If GitHub CLI is unavailable or unauthenticated locally, gitflow.sh outputs clear instructions and the direct GitHub Web PR URL for one-click manual creation.
- **Auditability**: Pull requests permanently retain CI logs, coverage diffs, reviewer approvals, and commit history.

## 🔄 Current Alternative Retained
- **Direct-Push Promotion**: Retained for current development phase to maintain maximum development iteration speed while enforcing local qualification gates and automated branch deletion on failure.
