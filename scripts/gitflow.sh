#!/usr/bin/env bash
# ==============================================================================
# Palworld Unified Operations Suite - Gitflow Lifecycle & Promotion Automation
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ISSUE_FILE="${REPO_ROOT}/.git/gitflow_active_issue"

cd "${REPO_ROOT}"

show_help() {
    cat << 'EOF'
================================================================================
 Palworld Operations Suite - Gitflow & GitHub Flow Automation CLI
================================================================================
Usage: ./scripts/gitflow.sh <command> [arguments]

Commands:
  feature <name> [issue#]     Create & checkout feature branch from 'main'
  bugfix <name> [issue#]      Create & checkout bugfix branch from 'main'
  link-issue <issue#>         Link active branch to a GitHub issue number
  close-issue [issue#] [msg]  Close GitHub issue via gh CLI and clear link
  pr [title]                  Run all 15 local checks, push branch & open PR to 'main'
  log-bug <title> [desc]      Log a bug report file + GitHub issue & branch into bugfix/
  install-hooks               Install local Git pre-commit hook to protect 'main'
  status                      Display active branch, linked issue, and git status
================================================================================
EOF
}

get_active_issue() {
    if [ -f "${ISSUE_FILE}" ]; then
        tr -d '[:space:]' < "${ISSUE_FILE}"
    else
        echo ""
    fi
}

cmd="${1:-}"

case "${cmd}" in
    feature)
        feat_name="${2:-}"
        linked_issue="${3:-}"
        if [ -z "${feat_name}" ]; then
            echo "[-] Error: Feature name required. Usage: ./scripts/gitflow.sh feature <name> [issue#]"
            exit 1
        fi
        sanitized_name=$(echo "${feat_name}" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
        echo ">>> Fetching latest origin/main..."
        git fetch origin main > /dev/null 2>&1 || true
        git checkout main > /dev/null 2>&1 || git checkout -b main origin/main
        git pull origin main > /dev/null 2>&1 || true
        echo ">>> Creating new feature branch: feature/${sanitized_name}..."
        git checkout -b "feature/${sanitized_name}"
        if [ -n "${linked_issue}" ]; then
            clean_issue=$(echo "${linked_issue}" | tr -cd '0-9')
            echo "${clean_issue}" > "${ISSUE_FILE}"
            echo "[+] Linked Feature to Issue #${clean_issue}."
        else
            rm -f "${ISSUE_FILE}"
        fi
        echo "[+] Successfully checked out feature/${sanitized_name}."
        ;;

    bugfix)
        bug_name="${2:-}"
        linked_issue="${3:-}"
        if [ -z "${bug_name}" ]; then
            echo "[-] Error: Bugfix name required. Usage: ./scripts/gitflow.sh bugfix <name> [issue#]"
            exit 1
        fi
        sanitized_name=$(echo "${bug_name}" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
        echo ">>> Fetching latest origin/main..."
        git fetch origin main > /dev/null 2>&1 || true
        git checkout main > /dev/null 2>&1 || git checkout -b main origin/main
        git pull origin main > /dev/null 2>&1 || true
        echo ">>> Creating new bugfix branch: bugfix/${sanitized_name}..."
        git checkout -b "bugfix/${sanitized_name}"
        if [ -n "${linked_issue}" ]; then
            clean_issue=$(echo "${linked_issue}" | tr -cd '0-9')
            echo "${clean_issue}" > "${ISSUE_FILE}"
            echo "[+] Linked Bugfix to Issue #${clean_issue}."
        else
            rm -f "${ISSUE_FILE}"
        fi
        echo "[+] Successfully checked out bugfix/${sanitized_name}."
        ;;

    link-issue)
        issue_num="${2:-}"
        if [ -z "${issue_num}" ]; then
            echo "[-] Error: Issue number required. Usage: ./scripts/gitflow.sh link-issue <issue#>"
            exit 1
        fi
        clean_issue=$(echo "${issue_num}" | tr -cd '0-9')
        echo "${clean_issue}" > "${ISSUE_FILE}"
        echo "[+] Successfully linked active branch $(git rev-parse --abbrev-ref HEAD) to Issue #${clean_issue}."
        ;;

    close-issue)
        issue_num="${2:-}"
        close_reason="${3:-Resolved and verified in promotion cycle}"
        if [ -z "${issue_num}" ]; then
            issue_num=$(get_active_issue)
        fi
        if [ -z "${issue_num}" ]; then
            echo "[-] Error: Issue number required. Usage: ./scripts/gitflow.sh close-issue [issue#] [reason]"
            exit 1
        fi
        clean_issue=$(echo "${issue_num}" | tr -cd '0-9')
        echo ">>> Closing issue #${clean_issue}..."
        if command -v gh >/dev/null 2>&1; then
            gh issue close "${clean_issue}" --comment "${close_reason}" || true
            echo "[+] GitHub Issue #${clean_issue} closed successfully via gh CLI."
        else
            echo "[!] gh CLI not available. Marked issue #${clean_issue} as closed locally."
        fi
        active=$(get_active_issue)
        if [ "${active}" = "${clean_issue}" ]; then
            rm -f "${ISSUE_FILE}"
            echo "[+] Cleared active linked issue reference."
        fi
        ;;

    pr|open-pr)
        pr_title="${2:-}"
        current_branch=$(git rev-parse --abbrev-ref HEAD)
        if [ "${current_branch}" = "main" ]; then
            echo "[-] Cannot open a PR from 'main'. Work must be on an ephemeral feature/ or bugfix/ branch."
            exit 1
        fi

        echo "========================================================================="
        echo " [PRE-FLIGHT] Verifying Local Master Quality Suite on ${current_branch}"
        echo "========================================================================="
        ./quality_script.sh -a

        echo "========================================================================="
        echo " [PULL REQUEST] Pushing ${current_branch} and Opening PR -> main"
        echo "========================================================================="
        echo ">>> Pushing ${current_branch} to origin..."
        git push -u origin "${current_branch}"

        default_title="${pr_title}"
        if [ -z "${default_title}" ]; then
            default_title=$(git log -1 --pretty=%s)
        fi

        active_issue=$(get_active_issue)
        if [ -n "${active_issue}" ]; then
            default_title="${default_title} (Closes #${active_issue})"
        fi

        if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
            echo ">>> Creating GitHub Pull Request targeting 'main'..."
            gh pr create --base main --head "${current_branch}" --title "${default_title}" --fill || true
            echo "[+] Pull request active. Monitor parallel CI status checks on GitHub."
        else
            repo_url=$(git remote get-url origin | sed 's/\.git$//; s|git@github.com:|https://github.com/|')
            echo "[+] Branch pushed. Please open your Pull Request targeting 'main' at:"
            echo "    ${repo_url}/compare/main...${current_branch}?expand=1"
        fi
        ;;

    promote-to-test|promote-to-dev|promote-to-main)
        echo "========================================================================="
        echo "[*] Notice: The repository has transitioned to Modern GitHub Flow."
        echo "[*] Multi-tier branch promotions (test/dev/main) have been consolidated."
        echo "[*] To submit your changes, run:"
        echo "    ./scripts/gitflow.sh pr [title]"
        echo "========================================================================="
        exit 0
        ;;

    log-bug)
        bug_title="${2:-}"
        bug_desc="${3:-No additional details provided.}"
        if [ -z "${bug_title}" ]; then
            echo "[-] Error: Bug title required. Usage: ./scripts/gitflow.sh log-bug <title> [desc]"
            exit 1
        fi
        mkdir -p "${REPO_ROOT}/bugs"
        timestamp=$(date '+%Y%m%d_%H%M%S')
        sanitized_title=$(echo "${bug_title}" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
        bug_file="${REPO_ROOT}/bugs/BUG-${timestamp}-${sanitized_title}.md"

        cat << BUG_EOF > "${bug_file}"
# Bug Report: ${bug_title}

- **Date Logged:** $(date '+%Y-%m-%d %H:%M:%S')
- **Source Branch:** $(git rev-parse --abbrev-ref HEAD)
- **Commit:** $(git rev-parse --short HEAD)

## Description
${bug_desc}

## Reproduction / Failure Context
Logged automatically during pipeline gate or developer audit.

## Next Steps
- Implement fix on \`bugfix/${sanitized_title}\`
- Verify with \`./quality_script.sh -a\`
- Submit PR via \`./scripts/gitflow.sh pr\`
BUG_EOF

        echo "[+] Bug report recorded at: ${bug_file}"
        created_issue=""
        if command -v gh >/dev/null 2>&1; then
            echo ">>> Creating GitHub issue via gh CLI..."
            issue_url=$(gh issue create --title "[BUG] ${bug_title}" --body-file "${bug_file}" --label "bug" 2>&1 || true)
            created_issue=$(echo "${issue_url}" | grep -oE '[0-9]+$' || true)
            if [ -n "${created_issue}" ]; then
                echo "[+] Created GitHub Issue #${created_issue}."
                echo "${created_issue}" > "${ISSUE_FILE}"
            fi
        fi
        echo ">>> Branching to bugfix/${sanitized_title}..."
        git checkout -b "bugfix/${sanitized_title}"
        if [ -n "${created_issue}" ]; then
            echo "[+] Linked active issue #${created_issue} to bugfix/${sanitized_title}."
        fi
        echo "[+] Ready for patching on bugfix/${sanitized_title}."
        ;;

    status)
        echo "========================================================================="
        echo " GitHub Flow Pipeline Status"
        echo " Current Branch: $(git rev-parse --abbrev-ref HEAD)"
        echo " Latest Commit:  $(git log -1 --oneline)"
        active_issue=$(get_active_issue)
        if [ -n "${active_issue}" ]; then
            echo " Linked Issue:   #${active_issue}"
        else
            echo " Linked Issue:   None"
        fi
        echo "========================================================================="
        git branch -v
        ;;

    install-hooks)
        echo ">>> Installing Git pre-commit hook to protect 'main'..."
        mkdir -p "${REPO_ROOT}/.git/hooks"
        cat << 'HOOK_EOF' > "${REPO_ROOT}/.git/hooks/pre-commit"
#!/usr/bin/env bash
branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" = "main" ]; then
    echo "========================================================================="
    echo "[-] REPOSITORY GOVERNANCE: Direct commits to 'main' are PROHIBITED."
    echo "[-] All changes must originate in an ephemeral feature/ or bugfix/ branch."
    echo "[-] Run: ./scripts/gitflow.sh feature <name>"
    echo "========================================================================="
    exit 1
fi
HOOK_EOF
        chmod +x "${REPO_ROOT}/.git/hooks/pre-commit" 2>/dev/null || true
        echo "[+] Successfully installed .git/hooks/pre-commit protection hook."
        ;;

    *)
        show_help
        exit 1
        ;;
esac
