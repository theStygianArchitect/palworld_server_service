# Palworld Server Service - Repository Rules & Architecture Directives

## 1. Strict Main/Master Branch Merge Stopgap
The agent is **STRICTLY PROHIBITED** from executing pull request merges into main or master, pushing directly to main or master, or invoking GitHub/GitLab merge APIs autonomously.
- **Green CI is NOT a License to Merge**: Passing 100% of CI gates (all 18 quality jobs), static security scans (Bandit 0 issues), linters (Pylint 10.00/10, Ruff), and unit test suites is a prerequisite for human review—NEVER authorization to merge.
- **Deterministic Reporting Gate**: When all checks pass, the agent must present the exact PR link, commit SHA, and test summary to the operator, then STOP and yield control.
- **Explicit Authorization Required**: Merges may ONLY occur when the user issues an explicit, unambiguous instruction (e.g., \"Merge PR #X\", \"Merge it\").

## 2. Non-Negotiable Quality Gates
Every contribution must satisfy all gates before PR submission:
- **Pylint**: 10.00 / 10 rating across pp/ and 	ests/.
- **Bandit**: 0 security issues (andit -r app/ -c pyproject.toml).
- **Ruff**: Clean linting and import formatting.
- **Pycodestyle**: Zero violations (pycodestyle app/ tests/ --max-line-length=120).
- **Pytest**: 100% pass rate across the full suite with zero test regressions.

## 3. Bounded Blast Radius & GitHub Flow
- **Single-Purpose Branches**: All work is executed on short-lived branches anchored directly off main (eat/<slug>, ix/<slug>).
- **Atomic Tasks ($\le 2$ Files)**: Tasks must be bounded to at most 2 related files (e.g., 1 implementation file + 1 test file) to prevent context drift and regressions.
- **PR Issue Linking**: Pull requests must include explicit closing keywords (Closes #<id>, Resolves #<id>).

## 4. Specialized Architectural Directives
- **ui-ux-architect Parity**: All frontend and client-facing flows must implement the 6-state UX model (IDLE, DISPATCHING / LOADING, ACTIVE / STREAMING, SUCCESS, DEGRADED / RECONNECTING, ERROR / RECOVERY). Blind timers (setTimeout loops) and abrupt banner dismissals are strictly forbidden. All daemon restarts must feature closed-loop socket liveness probing.
- **	he-3am-debugger Radical Clarity**: Write flat linear logic, defensive physical type boundaries, explicit error logging, and self-documenting code. Never use clever syntax hacks or code golf.
- **dversarial-0day-auditor Security**: Strictly prohibit arbitrary shell execution (shell=True banned). Validate all inputs and parameters. Enforce zero-leak secret hygiene.

## 5. Test-First & No Silent Failures
- Never swallow exceptions or introduce empty catch/except blocks.
- Every bug fix must include a deterministic reproduction test committed to the permanent test suite to permanently eliminate silent regressions.

## 6. Release & Version Hygiene
- Strictly adhere to Semantic Versioning (MAJOR.MINOR.PATCH).
- Every user-facing change must update CHANGELOG.md under standard Keep a Changelog categories ([Added], [Changed], [Fixed], [Security]) before PR submission.