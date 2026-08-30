"""AST Exception & Logging Audit Tool.

Inspects Python source code using Abstract Syntax Trees (AST) to mathematically enforce
the 3 AM Debugger standard: Zero silent exceptions (no `pass` statements) and mandatory
diagnostic logging inside every `except` handler.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def audit_exceptions_in_file(py_file: Path) -> list[str]:
    """Inspects a Python file's AST for unlogged or empty exception handlers.

    Args:
        py_file (Path): Path to the Python file to inspect.

    Returns:
        list[str]: List of violation descriptions found in the file.
    """
    violations: list[str] = []
    try:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
    except PermissionError as err:
        sys.stderr.write(f"Permission denied reading {py_file}: {err}\n")
        return [f"{py_file}: Permission denied: {err}"]
    except OSError as err:
        sys.stderr.write(f"OS error reading {py_file}: {err}\n")
        return [f"{py_file}: OS error reading file: {err}"]
    except SyntaxError as err:
        sys.stderr.write(f"Syntax error parsing AST for {py_file}: {err}\n")
        return [f"{py_file}: Syntax error parsing AST: {err}"]
    except UnicodeDecodeError as err:
        sys.stderr.write(f"Unicode decode error reading {py_file}: {err}\n")
        return [f"{py_file}: Unicode decode error reading file: {err}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            exc_name = ast.unparse(node.type) if node.type else "bare except"

            # 1. 3 AM Debugger Rule: Check for bundled exception tuples
            if isinstance(node.type, ast.Tuple):
                bundled_types = [ast.unparse(elt) for elt in node.type.elts]
                violations.append(
                    f"{py_file}:{node.lineno} -> [BUNDLED EXCEPTION] Handler 'except ({', '.join(bundled_types)})' bundles multiple exception types. Unbundle into distinct handlers."
                )

            # 2. Check for `pass` in exception handler
            has_pass = any(isinstance(stmt, ast.Pass) for stmt in node.body)
            if has_pass:
                violations.append(
                    f"{py_file}:{node.lineno} -> [EMPTY EXCEPTION] Handler 'except {exc_name}' contains silent 'pass'."
                )

            # 3. Check for explicit logging invocation (log.*, logger.*, self.handleError, sys.stderr.write)
            has_log = False
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call_str = ast.unparse(stmt.value)
                    if (
                        call_str.startswith("log.")
                        or call_str.startswith("logger.")
                        or call_str.startswith("self.handleError")
                        or call_str.startswith("sys.stderr.write")
                    ):
                        has_log = True
                        break

            if not has_log:
                violations.append(
                    f"{py_file}:{node.lineno} -> [UNLOGGED EXCEPTION] Handler 'except {exc_name}' does not invoke logging or error handling."
                )

    return violations


def main() -> int:
    """Entrypoint for AST exception audit scanner.

    Returns:
        int: Exit code 0 if all exception handlers are logged and non-empty; 1 otherwise.
    """
    target_dirs = sys.argv[1:] if len(sys.argv) > 1 else ["app"]
    all_violations: list[str] = []

    for target in target_dirs:
        target_path = Path(target)
        if target_path.is_file() and target_path.suffix == ".py":
            all_violations.extend(audit_exceptions_in_file(target_path))
        elif target_path.is_dir():
            for py_file in target_path.rglob("*.py"):
                all_violations.extend(audit_exceptions_in_file(py_file))

    if all_violations:
        print("[-] AST Exception Audit Failed with violations:")
        for v in all_violations:
            print(f"  - {v}")
        return 1

    print("[+] AST Exception Audit Passed: Zero silent or unlogged exception handlers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
