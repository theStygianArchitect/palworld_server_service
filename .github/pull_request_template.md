<!--- Provide a general summary of your changes in the Title above with a suggested maximum of 50 Characters -->

> [!IMPORTANT]
> **TARGET BRANCH POLICY**: All Pull Requests MUST target the `main` branch (`base: main`).

## Description
<!--- Describe your changes in detail. Focus on the 'Why' (rationale/intent), not just the 'What'. -->

## Motivation and Context (including Issue that is closed (Required))
<!--- Why is this change required? What problem does it solve? -->
<!--- Please link to closed issue here: Closes #<issue_number> -->

## How Has This Been Tested?
<!--- Please describe in detail how you tested your changes. -->
<!--- Include details of your testing environments, tests ran to see how your change affects other areas of the code, etc. -->
<!--- Include details of unit testing, code quality, and vulnerability linting. -->
- [ ] `./quality_script.sh -a` executed and passed on local environment.
- [ ] Automated multi-Python matrix verified on **Python 3.10, 3.11, 3.12, and 3.13**.
- [ ] Clean install and idempotency verified (`test_install_idempotency.sh`).
- [ ] Repository secret scan (`scripts/scan_secrets.py`) verified 0 leaked credentials.
- [ ] AST Exception Audit (`scripts/audit_exceptions.py`) verified 0 unlogged exceptions.

## Screenshots / CLI Output (if appropriate):

## Types of Changes
<!--- What types of changes does your code introduce? Put an `x` in all the boxes that apply: -->
- [ ] Documentation update (changes to documentation only)
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] Security fix (vulnerability mitigation or credential protection)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)

## 12-Factor & 3 AM Resilience Checklist:
<!--- Please review the following architectural standards: -->
- [ ] **Physical Type Isolation**: Domain TypedDicts / schemas are physically separated into `app/types.py` or `app/schemas.py`.
- [ ] **Zero Silent Exceptions**: Explicit diagnostic logging (`log.*`) is present in every `except` block.
- [ ] **Zero Hardcoded Secrets**: All tokens, webhooks, and passwords use environment variables or `AppSettings`.
- [ ] **Defensive Boundary Validation**: Inputs are validated and sanitized via Pydantic bounds and regex clamping.
- [ ] **Google Docstring Standard**: All public functions and classes have Google-style docstrings (`pydocstyle` compliant).

## Checklist:
<!--- Go over all the following points, and put an `x` in all the boxes that apply: -->
<!--- If you're unsure about any of these, don't hesitate to ask. We're here to help! -->

### Documentation Checklist:
- [ ] I verified a link to a closed issue.
- [ ] I rebuilt the docs / updated markdown files.
- [ ] I verified that there are no duplicate pull requests.
- [ ] My commit message is descriptive and follows guidelines.
- [ ] I have read and followed guidance in the CONTRIBUTING document.

### All Other Checklist (Engineering / Code):
- [ ] I verified a link to a closed issue (`Closes #<id>`).
- [ ] I verified that there are no duplicate pull requests.
- [ ] I have committed my changes to a separate branch with a descriptive name.
- [ ] My commit message is descriptive and follows Conventional Commits (`feat(...)`, `fix(...)`).
- [ ] I have read and followed guidance in the CONTRIBUTING document.
- [ ] My code follows the code style and formatting standards of this project (`ruff`, `mypy`, `pylint`).
- [ ] If my change requires a change to documentation, I have updated the documentation accordingly.
- [ ] All tests described above executed successfully.
- [ ] For New Features and Breaking changes I added new tests.
- [ ] For Bug Fixes, I added an automated regression test in `tests/` reproducing and verifying the fix for Issue #<id>.

