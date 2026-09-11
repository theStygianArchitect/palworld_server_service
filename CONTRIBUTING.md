# Contributing to Palworld Operations Suite

Thank you for your interest in contributing! We follow the **Modern GitHub Flow** model with strict, automated quality and security gates.

---

## 🏛️ Branching Model & Workflow

1. **`main` is the primary production branch**.
   - Direct commits to `main` are strictly prohibited.
   - All contributions must originate in an ephemeral topic branch (`feature/<name>` or `bugfix/<name>`) and be submitted via a **Pull Request (PR)** targeting `main`.

2. **Branch Naming Conventions**:
   - Features: `feature/<descriptive-name>`
   - Bug fixes: `bugfix/<descriptive-name>`
   - Documentation: `docs/<descriptive-name>`

---

## 🚀 Quickstart: Opening a Pull Request

```bash
# 1. Ensure you have latest main
git checkout main
git pull origin main

# 2. Create your topic branch using our CLI helper
./scripts/gitflow.sh feature my-new-feature

# 3. Develop your changes and write unit tests...

# 4. Verify the complete 15-check quality suite locally
./quality_script.sh -a

# 5. Push and submit your Pull Request to main
./scripts/gitflow.sh pr "feat: add my new feature"
```

---

## 🧪 The 16 Parallel CI Quality Gates

When your Pull Request is submitted against `main`, GitHub Actions automatically runs **16 parallel verification jobs**:

| Category | Status Check | Tool Executed | Pass Criteria |
| :--- | :--- | :--- | :--- |
| **Security** | Zero-Leak Secret Scan | `scripts/scan_secrets.py` | 0 hardcoded credentials or tokens |
| | Project Suppression Audit | `scripts/audit_suppressions.py` | 0 unauthorized suppressions |
| | AST Exception & Logging Audit | `scripts/audit_exceptions.py` | 100% typed exceptions with logging |
| | Dependency Vulnerability Audit | `pip-audit` | 0 known CVEs |
| | Static Security Analysis (Bandit)| `bandit` | 0 security flaws |
| **Linting** | Code Linting & Formatting | `ruff` | Clean format & imports |
| | Strict Type Analysis | `mypy` | Strict type safety |
| | Pylint Standard | `pylint` | **10.00 / 10** score |
| | PEP 8 Formatting | `pycodestyle` | Max line length 120, clean style |
| | Google Docstring Validation | `pydocstyle` | Full Google-style docstrings |
| **Testing** | Unit Tests & Coverage | `pytest --cov` | 100% test pass rate |
| | Clean Install & Idempotency | `test_install_idempotency.sh` | Clean multi-distro install |
| **Packaging** | Package Build & Distribution | `uv build` | Clean wheel and sdist build |
| **Matrix** | Matrix Test (Python 3.10) | `pytest` on 3.10 | 100% pass rate |
| | Matrix Test (Python 3.11) | `pytest` on 3.11 | 100% pass rate |
| | Matrix Test (Python 3.12) | `pytest` on 3.12 | 100% pass rate |
| | Matrix Test (Python 3.13) | `pytest` on 3.13 | 100% pass rate |
| **Gate** | All CI Quality Gates Passed | `ci-gate` | Evaluates all 16 checks |

---

## 🔒 Branch Protection

`main` is protected by repository rules:
- **Pull Requests Required**: All code must merge through a PR.
- **Strict Status Checks**: All 15 checks must pass 100% green before the merge button unlocks.
- **Branches Up-to-Date**: PRs must be updated with latest `main` before merging.
- **Force Pushes & Deletions Prohibited**: History on `main` is immutable.
