<!--- Provide a concise summary of your changes in the Title above (e.g. 'feat(tracker): add a2s udp probe') -->

## Description
<!--- Describe your changes in detail. Focus on the 'Why' (rationale/intent), not just the 'What'. -->

## Motivation & Context
<!--- Why is this change required? What issue or feature does it address? -->
<!--- Closes #<issue_number> -->

## 12-Factor & 3 AM Resilience Checklist
<!--- Please review the following architectural standards: -->
- [ ] **Physical Type Isolation**: Domain TypedDicts / schemas are physically separated into `app/types.py` or `app/schemas.py`.
- [ ] **Zero Silent Exceptions**: No `pass` in exception handlers; explicit diagnostic logging (`log.*`) is present in every `except` block.
- [ ] **Zero Hardcoded Secrets**: All tokens, webhook URLs, and passwords use environment variables or `AppSettings`.
- [ ] **Defensive Boundary Validation**: Inputs are validated and sanitized via Pydantic bounds and regex clamping.
- [ ] **Google Docstring Standard**: All public functions and classes have Google-style docstrings (`pydocstyle` compliant).

## How Has This Been Tested?
<!--- Describe the unit tests, integration tests, or manual verification steps conducted. -->
- [ ] `./quality_check.sh -a` executed and passed on local environment.
- [ ] Automated multi-Python matrix tested on **Python 3.10, 3.11, 3.12, and 3.13**.
- [ ] Parallel test execution (`pytest -n auto`) completed with 100% pass rate.
- [ ] Repository secret scan (`scripts/scan_secrets.py`) verified 0 leaked credentials.
- [ ] AST Exception Audit (`scripts/audit_exceptions.py`) verified 0 unlogged exceptions.

## Screenshots / CLI Output (if applicable):

## Types of Changes
<!--- What types of changes does your code introduce? Put an `x` in all the boxes that apply: -->
- [ ] 🐛 Bug fix (non-breaking change fixing an issue)
- [ ] ✨ New feature (non-breaking change adding functionality)
- [ ] 🛡️ Security fix (vulnerability mitigation or credential protection)
- [ ] 💥 Breaking change (fix or feature that causes existing functionality to change)
- [ ] 📝 Documentation update (docstrings, markdown docs, or guide updates)

## Contributor Checklist:
- [ ] My code adheres to the project's formatting and linting rules (`ruff`, `mypy`).
- [ ] I have committed changes to a descriptive feature branch.
- [ ] My commit messages follow Conventional Commits standard (`feat(...)`, `fix(...)`, `refactor(...)`).
- [ ] I have added new unit tests for newly introduced logic and edge cases.
- [ ] All existing and new tests pass without regressions.
