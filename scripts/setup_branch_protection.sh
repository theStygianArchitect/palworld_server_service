#!/usr/bin/env bash
# ==============================================================================
# Universal GitHub Branch Protection Automation CLI
# ==============================================================================
# Configures strict branch protection, required status checks, and PR reviews
# via GitHub REST API v3 using GitHub CLI (gh) or curl with GITHUB_TOKEN.
# ==============================================================================

set -euo pipefail

BRANCH="main"
DRY_RUN=0
REQUIRE_REVIEWS=0
ENFORCE_ADMINS="false"

show_help() {
  cat << 'EOF'
Usage: ./scripts/setup_branch_protection.sh [options] [branch]

Configures GitHub repository branch protection rules via REST API.

Arguments:
  branch                  Target branch to protect (default: main)

Options:
  --dry-run               Print payload and target without calling GitHub API
  --require-reviews <n>   Number of required approving reviews (default: 0)
  --enforce-admins        Apply protection rules to repository admins (default: false)
  -h, --help              Display this help message and exit

Required Status Checks Configured:
  - All CI Quality Gates Passed (ci-gate)
  - Zero-Leak Secret Scan
  - Project Suppression Audit
  - AST Exception & Logging Audit
  - Dependency Vulnerability Audit
  - Static Security Analysis (Bandit)
  - Code Linting & Formatting (Ruff)
  - Strict Type Analysis (Mypy)
  - Pylint Standard (10.00/10)
  - PEP 8 Formatting (pycodestyle)
  - Google Docstring Validation
  - Unit Tests & Coverage (pytest)
  - Clean Install & Idempotency Test
  - Matrix Test (Python 3.10)
  - Matrix Test (Python 3.11)
  - Matrix Test (Python 3.12)
  - Matrix Test (Python 3.13)
EOF
}

# 1. Parse CLI Options
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --require-reviews)
      REQUIRE_REVIEWS="${2:-0}"
      shift 2
      ;;
    --enforce-admins)
      ENFORCE_ADMINS="true"
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      BRANCH="$1"
      shift
      ;;
  esac
done

# 2. Universal Repository Detection from Git Remote
remote_url=$(git config --get remote.origin.url | tr -d '\r\n' || true)
if [ -z "${remote_url}" ]; then
  echo "[-] Error: No 'origin' remote found in git repository." >&2
  exit 1
fi

clean_url="${remote_url%.git}"
if [[ "${clean_url}" =~ github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$ ]]; then
  OWNER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
  REPO_SLUG="${OWNER}/${REPO}"
else
  echo "[-] Error: Remote origin '${remote_url}' is not a recognized GitHub URL." >&2
  exit 1
fi

# 3. Construct Granular Protection Payload (All 15 Checks + Aggregate Gate)
PAYLOAD=$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "All CI Quality Gates Passed",
      "Zero-Leak Secret Scan",
      "Project Suppression Audit",
      "AST Exception & Logging Audit",
      "Dependency Vulnerability Audit",
      "Static Security Analysis (Bandit)",
      "Code Linting & Formatting (Ruff)",
      "Strict Type Analysis (Mypy)",
      "Pylint Standard (10.00/10)",
      "PEP 8 Formatting (pycodestyle)",
      "Google Docstring Validation",
      "Unit Tests & Coverage (pytest)",
      "Clean Install & Idempotency Test",
      "Matrix Test (Python 3.10)",
      "Matrix Test (Python 3.11)",
      "Matrix Test (Python 3.12)",
      "Matrix Test (Python 3.13)"
    ]
  },
  "enforce_admins": ${ENFORCE_ADMINS},
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": ${REQUIRE_REVIEWS}
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
)

if [ "${DRY_RUN}" -eq 1 ]; then
  echo "========================================================================="
  echo " [DRY RUN] Target Repository: ${REPO_SLUG} | Branch: ${BRANCH}"
  echo "========================================================================="
  echo "${PAYLOAD}"
  exit 0
fi

# 4. Dual-Execution Engine (gh CLI or curl fallback)
echo ">>> Configuring branch protection for ${REPO_SLUG}:${BRANCH}..."

# Detect credentials: gh CLI -> GH_TOKEN/GITHUB_TOKEN -> git credential manager
TOKEN=$(echo "${GH_TOKEN:-${GITHUB_TOKEN:-}}" | tr -d '\r\n')
if [ -z "${TOKEN}" ] && command -v git >/dev/null 2>&1; then
  TOKEN=$(printf "protocol=https\nhost=github.com\n" | git credential fill 2>/dev/null | grep '^password=' | head -n1 | cut -d'=' -f2- | tr -d '\r\n' || true)
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo ">>> Using authenticated GitHub CLI (gh)..."
  echo "${PAYLOAD}" | gh api \
    --method PUT \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/${REPO_SLUG}/branches/${BRANCH}/protection" \
    --input -
  echo "[+] Branch protection successfully enabled via gh CLI."
elif [ -n "${TOKEN}" ]; then
  echo ">>> Using curl with GitHub API token..."
  RESPONSE_FILE=$(mktemp)
  trap 'rm -f "${RESPONSE_FILE}"' EXIT

  if echo "${PAYLOAD}" | curl -s -S -f \
    -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    --data-binary @- \
    -o "${RESPONSE_FILE}" \
    "https://api.github.com/repos/${REPO_SLUG}/branches/${BRANCH}/protection"; then
    echo "[+] Branch protection successfully enabled via GitHub API."
  else
    echo "[-] Error: GitHub API request failed." >&2
    if [ -s "${RESPONSE_FILE}" ]; then
      cat "${RESPONSE_FILE}" >&2
    fi
    exit 3
  fi
else
  echo "=========================================================================" >&2
  echo "[-] Error: GitHub credentials not found." >&2
  echo "[-] Please authenticate using one of the following methods:" >&2
  echo "    1. Log in with GitHub CLI: gh auth login" >&2
  echo "    2. Or export a token:     export GH_TOKEN=\"your_personal_access_token\"" >&2
  echo "=========================================================================" >&2
  exit 2
fi
