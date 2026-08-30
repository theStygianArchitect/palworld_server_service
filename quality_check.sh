#!/usr/bin/env bash
# ==============================================================================
# Palworld Dedicated Server Operations Suite - Quality & Security Runner
# ==============================================================================

app_directory_list=(
  app/*.py
  tests/*.py
)
test_directory="tests"

run_bandit_check() {
  echo ">>> [1/7] Starting bandit security check..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run bandit -c pyproject.toml -r -q "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        echo "[-] Bandit check failed on ${python_file}"
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed bandit check."
}

run_dependency_check() {
  echo ">>> [2/7] Starting dependency vulnerability audit (pip-audit)..."
  uv run pip-audit
  exit_code=$?
  if [ ${exit_code} -ne 0 ]; then
    echo "[-] pip-audit detected vulnerable dependencies."
    exit ${exit_code}
  fi
  echo "[+] Passed pip-audit."
}

run_ruff_check() {
  echo ">>> [3/7] Starting ruff code linting..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run ruff check --quiet "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        echo "[-] Ruff check failed on ${python_file}"
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed ruff check."
}

run_mypy_check() {
  echo ">>> [4/7] Starting mypy type analysis..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run mypy "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        echo "[-] mypy check failed on ${python_file}"
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed mypy check."
}

run_pylint_check() {
  echo ">>> [5/7] Starting pylint check..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run pylint -s no "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        echo "[-] Pylint check failed on ${python_file}"
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed pylint check."
}

run_pycodestyle_check() {
  echo ">>> Starting pycodestyle check..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run pycodestyle --max-line-length 120 "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed pycodestyle check."
}

run_pydocstyle_check() {
  echo ">>> Starting pydocstyle check..."
  for python_file in "${app_directory_list[@]}"; do
    if [ -f "${python_file}" ]; then
      echo "  Checking ${python_file}"
      uv run pydocstyle "${python_file}"
      exit_code=$?
      if [ ${exit_code} -ne 0 ]; then
        exit ${exit_code}
      fi
    fi
  done
  echo "[+] Passed pydocstyle check."
}

run_pytest() {
  echo ">>> Starting project unit tests..."
  uv run pytest "${test_directory}"
  exit_code=$?
  if [ ${exit_code} -ne 0 ]; then
    echo "[-] Tests failed."
    exit ${exit_code}
  fi
  echo "[+] Passed unit tests."
}

run_coverage() {
  echo ">>> Starting project coverage analysis..."
  uv run pytest --cov=app --cov-report=term-missing "${test_directory}"
  exit_code=$?
  if [ ${exit_code} -ne 0 ]; then
    echo "[-] Coverage analysis failed."
    exit ${exit_code}
  fi
  echo "[+] Coverage analysis completed."
}

run_multi_python_matrix() {
  echo "========================================================================="
  echo " Running Multi-Python Matrix Test Suite (3.10, 3.11, 3.12, 3.13)"
  echo "========================================================================="
  VERSIONS=("3.10" "3.11" "3.12" "3.13")

  echo ">>> Ensuring all Python versions are installed via uv..."
  uv python install 3.10 3.11 3.12 3.13

  for ver in "${VERSIONS[@]}"; do
    echo "-------------------------------------------------------------------------"
    echo ">>> Running PyTest against Python ${ver}..."
    echo "-------------------------------------------------------------------------"
    uv run --isolated --python "${ver}" pytest "${test_directory}"
    exit_code=$?
    if [ ${exit_code} -ne 0 ]; then
      echo "[-] Multi-version test failed on Python ${ver}!"
      exit ${exit_code}
    fi
    echo "[+] Passed on Python ${ver}."
  done
  echo "========================================================================="
  echo "[+] All Python matrix versions (3.10-3.13) passed successfully!"
  echo "========================================================================="
}

run_update_dependencies() {
  echo ">>> Upgrading uv lockfile and dependencies..."
  uv lock --upgrade
  uv sync --all-groups
  echo "[+] Dependencies upgraded successfully."
}

run_security_check() {
  echo "========================================================================="
  echo " Running Security Checks"
  echo "========================================================================="
  run_bandit_check
  run_dependency_check
}

run_linting_check() {
  echo "========================================================================="
  echo " Running Linting Checks"
  echo "========================================================================="
  run_ruff_check
  run_mypy_check
  run_pylint_check
}

quality_check() {
  echo "========================================================================="
  echo " Running Master Quality Suite"
  echo "========================================================================="
  run_dependency_check
  run_bandit_check
  run_ruff_check
  run_mypy_check
  run_coverage
  run_multi_python_matrix
  echo "========================================================================="
  echo " [SUCCESS] All Master Quality & Security Checks Passed!"
  echo "========================================================================="
}

show_usage() {
  echo "Palworld Server Service - Quality Check Manual"
  echo "USAGE"
  echo "  quality_check.sh [-a] [-c] [-h] [-l] [-m] [-q <check type>] [-s] [-t] [-u]"
  echo ""
  echo "OPTIONS"
  echo "  -a  Run all quality, security, and multi-version checks. (DEFAULT)"
  echo "  -c  Run pytest code coverage report."
  echo "  -h  Show usage information."
  echo "  -l  Run linting checks (ruff, mypy, pylint)."
  echo "  -m  Run multi-Python matrix test (auto-installs 3.10-3.13 via uv)."
  echo "  -s  Run security checks (bandit, pip-audit)."
  echo "  -t  Run project unit tests."
  echo "  -u  Upgrade dependencies and uv.lock."
  echo "  -q  Run specific check: [bandit | pip-audit | ruff | mypy | pylint | pytest | coverage | matrix]"
  echo ""
}

if [ $# -eq 0 ]; then
  quality_check
  exit 0
fi

while getopts 'achlmstuq:' flag; do
  case "${flag}" in
    a) quality_check ;;
    c) run_coverage ;;
    h) show_usage ;;
    l) run_linting_check ;;
    m) run_multi_python_matrix ;;
    s) run_security_check ;;
    t) run_pytest ;;
    u) run_update_dependencies ;;
    q) case "${OPTARG}" in
      bandit) run_bandit_check ;;
      pip-audit) run_dependency_check ;;
      ruff) run_ruff_check ;;
      mypy) run_mypy_check ;;
      pylint) run_pylint_check ;;
      pytest) run_pytest ;;
      coverage) run_coverage ;;
      matrix) run_multi_python_matrix ;;
      *)
        echo "Unknown check type: ${OPTARG}"
        show_usage
        exit 1
        ;;
    esac ;;
    *) show_usage ;;
  esac
done
