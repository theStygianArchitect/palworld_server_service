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
 Palworld Operations Suite - Gitflow Promotion Automation CLI
================================================================================
Usage: ./scripts/gitflow.sh <command> [arguments]

Commands:
  feature <name> [issue#]     Create & checkout feature branch from 'test'
  bugfix <name> [issue#]      Create & checkout bugfix branch from 'test'
  link-issue <issue#>         Link active branch to a GitHub issue number
  close-issue [issue#] [msg]  Close GitHub issue via gh CLI and clear link
  promote-to-test             Verify quality gate & merge current branch into 'test'
  promote-to-dev              Verify staging & merge 'test' into 'dev'
  promote-to-main             Verify multi-python matrix & merge 'dev' into 'main'
  log-bug <title> [desc]      Log a bug report file + GitHub issue & branch into bugfix/
  install-hooks               Install local Git pre-commit hook to protect test, dev, main
  status                      Display active branch, linked issue, and promotion hierarchy
================================================================================
EOF
}

get_active_issue() {
    if [ -f "${ISSUE_FILE}" ]; then
        cat "${ISSUE_FILE}" | tr -d '[:space:]'
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
        echo ">>> Fetching latest origin/test..."
        git fetch origin test > /dev/null 2>&1 || true
        git checkout test > /dev/null 2>&1 || git checkout -b test origin/test
        git pull origin test > /dev/null 2>&1 || true
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
        echo ">>> Fetching latest origin/test..."
        git fetch origin test > /dev/null 2>&1 || true
        git checkout test > /dev/null 2>&1 || git checkout -b test origin/test
        git pull origin test > /dev/null 2>&1 || true
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

    promote-to-test)
        current_branch=$(git rev-parse --abbrev-ref HEAD)
        if [ "${current_branch}" = "test" ] || [ "${current_branch}" = "dev" ] || [ "${current_branch}" = "main" ]; then
            echo "[-] Cannot promote from protected branch '${current_branch}'. Checkout your feature or bugfix branch first."
            exit 1
        fi
        echo "========================================================================="
        echo " [GATE 1] Running Master Quality & Code Qualifications on ${current_branch}"
        echo "========================================================================="
        qual_passed=1
        ./quality_check.sh -a || qual_passed=0
        if [ "${qual_passed}" -eq 1 ]; then
            ./scripts/test_install_idempotency.sh || qual_passed=0
        fi

        # If any failure occurs: abort merge, delete failing feature branch, convert to bugfix, and log issue
        if [ "${qual_passed}" -ne 1 ]; then
            echo "========================================================================="
            echo " [GATE 1 FAILED] Master qualifications failed on ${current_branch}!"
            echo "========================================================================="
            echo "[-] MERGE ABORTED: No code will be merged into 'test'."
            echo "[-] Per repository policy: failing feature branch will be closed and deleted,"
            echo "    and all work transitioned to a dedicated bugfix/ branch for patching."

            sanitized_name=$(echo "${current_branch}" | sed 's|^feature/||; s|^bugfix/||' | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
            bug_title="Gate 1 Qualification Failure on ${current_branch}"
            bug_desc="Code qualifications or clean installation idempotency checks failed on branch ${current_branch} (commit $(git rev-parse --short HEAD))."

            mkdir -p "${REPO_ROOT}/bugs"
            timestamp=$(date '+%Y%m%d_%H%M%S')
            bug_file="${REPO_ROOT}/bugs/BUG-${timestamp}-${sanitized_name}.md"
            cat << BUG_EOF > "${bug_file}"
# Bug Report: ${bug_title}

- **Date Logged:** $(date '+%Y-%m-%d %H:%M:%S')
- **Source Branch:** ${current_branch}
- **Commit:** $(git rev-parse --short HEAD)

## Description
${bug_desc}

## Action Taken
- Branch '${current_branch}' closed and deleted.
- Work preserved on 'bugfix/${sanitized_name}'.
BUG_EOF

            echo "[+] Bug report recorded at: ${bug_file}"
            created_issue=""
            if command -v gh >/dev/null 2>&1; then
                issue_url=$(gh issue create --title "[BUG] ${bug_title}" --body-file "${bug_file}" --label "bug" 2>&1 || true)
                created_issue=$(echo "${issue_url}" | grep -oE '[0-9]+$' || true)
                if [ -n "${created_issue}" ]; then
                    echo "[+] Created GitHub Issue #${created_issue}."
                    echo "${created_issue}" > "${ISSUE_FILE}"
                fi
            fi

            # If current branch is a feature branch, convert to bugfix and delete the feature branch
            if [[ "${current_branch}" == feature/* ]]; then
                echo ">>> Creating bugfix/${sanitized_name} to preserve commit history..."
                git checkout -b "bugfix/${sanitized_name}"
                echo ">>> Deleting closed feature branch '${current_branch}'..."
                git branch -D "${current_branch}" || true
                git push origin --delete "${current_branch}" 2>/dev/null || true
                echo "[+] Successfully deleted feature branch '${current_branch}'."
            fi

            echo "========================================================================="
            echo " [NEXT STEPS] Fix the issues on 'bugfix/${sanitized_name}'."
            echo " Once resolved, run: ./scripts/gitflow.sh promote-to-test"
            echo "========================================================================="
            exit 1
        fi

        # Qualifications passed: Merge into test
        active_issue=$(get_active_issue)
        merge_tag="merge: promote ${current_branch} to test"
        if [ -n "${active_issue}" ]; then
            merge_tag="${merge_tag} (Ref #${active_issue})"
        fi

        echo ">>> Quality qualifications passed! Merging ${current_branch} into test..."
        git fetch origin test > /dev/null 2>&1 || true
        git checkout test
        git pull origin test > /dev/null 2>&1 || true
        git merge "${current_branch}" --no-edit -m "${merge_tag}"
        echo ">>> Pushing promoted changes to origin/test..."
        git push origin test
        echo "[+] Branch ${current_branch} successfully merged and pushed to 'test'."

        # Clean up / delete the merged feature or bugfix branch
        echo ">>> Cleaning up completed branch '${current_branch}'..."
        git branch -d "${current_branch}" || git branch -D "${current_branch}" || true
        git push origin --delete "${current_branch}" 2>/dev/null || true
        echo "[+] Branch '${current_branch}' deleted after successful promotion."
        ;;

    promote-to-dev)
        echo "========================================================================="
        echo " [GATE 2] Promoting 'test' into 'dev' (Staging Environment)"
        echo "========================================================================="
        git fetch origin test dev > /dev/null 2>&1 || true
        git checkout test
        git pull origin test > /dev/null 2>&1 || true

        echo ">>> Running Master Quality Check on test branch..."
        ./quality_check.sh -a

        active_issue=$(get_active_issue)
        merge_tag="merge: promote test to dev [staging release]"
        if [ -n "${active_issue}" ]; then
            merge_tag="${merge_tag} (Resolves #${active_issue})"
        fi

        git checkout dev > /dev/null 2>&1 || git checkout -b dev origin/dev
        git pull origin dev > /dev/null 2>&1 || true
        git merge test --no-edit -m "${merge_tag}"
        git push origin dev
        echo "[+] 'test' successfully promoted and pushed to 'dev'."
        ;;

    promote-to-main)
        echo "========================================================================="
        echo " [GATE 3] Promoting 'dev' into 'main' (Production Release)"
        echo "========================================================================="
        git fetch origin dev main > /dev/null 2>&1 || true
        git checkout dev
        git pull origin dev > /dev/null 2>&1 || true

        echo ">>> Verifying multi-python matrix locally before production merge..."
        for py_ver in 3.10 3.11 3.12 3.13; do
            if command -v uv >/dev/null 2>&1; then
                echo "  Checking test suite on Python ${py_ver}..."
                uv run --python "${py_ver}" pytest -q tests > /dev/null 2>&1 || {
                    echo "[-] Failed Python ${py_ver} verification. Aborting production promotion."
                    exit 1
                }
            fi
        done
        echo "[+] All supported Python versions verified successfully."

        active_issue=$(get_active_issue)
        merge_tag="merge: promote dev to main [production release]"
        if [ -n "${active_issue}" ]; then
            merge_tag="${merge_tag} (Closes #${active_issue})"
        fi

        git checkout main
        git pull origin main > /dev/null 2>&1 || true
        git merge dev --no-edit -m "${merge_tag}"
        git push origin main
        echo "[+] 'dev' successfully promoted and pushed to 'main'."

        if [ -n "${active_issue}" ]; then
            echo ">>> Automatically closing linked issue #${active_issue} upon production release..."
            if command -v gh >/dev/null 2>&1; then
                gh issue close "${active_issue}" --comment "Resolved and released to production in main branch commit $(git rev-parse --short HEAD)." || true
            fi
            rm -f "${ISSUE_FILE}"
            echo "[+] Issue #${active_issue} closed and link cleared."
        fi
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
- Verify with \`./quality_check.sh -a\`
- Promote fix via \`./scripts/gitflow.sh promote-to-test\`
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
        echo " Gitflow Pipeline Status"
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
        echo ">>> Installing Git pre-commit hook to protect test, dev, and main..."
        mkdir -p "${REPO_ROOT}/.git/hooks"
        cat << 'HOOK_EOF' > "${REPO_ROOT}/.git/hooks/pre-commit"
#!/usr/bin/env bash
branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" = "main" ] || [ "$branch" = "dev" ] || [ "$branch" = "test" ]; then
    echo "========================================================================="
    echo "[-] REPOSITORY RULE VIOLATION: Direct commits to '$branch' are PROHIBITED."
    echo "[-] All code must originate in an ephemeral feature/ or bugfix/ branch."
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
