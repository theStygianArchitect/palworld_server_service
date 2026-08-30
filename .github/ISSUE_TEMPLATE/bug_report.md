---
name: Bug Report
about: Report a defect, unexpected behavior, or crash in Palworld Operations Suite
labels: bug
---

<!--- Provide a clear, concise summary of the issue in the title above -->

## 🐛 Expected Behavior
<!--- Describe what you expected to happen. -->

## 💥 Current Behavior
<!--- Describe what actually happened, including error codes or unexpected outputs. -->

## 📋 Steps to Reproduce
1. Start `palworld-manager.service` or run local development server with `uv run uvicorn app.main:app`.
2. Send request to endpoint or trigger action `...`
3. Observe error or unexpected state: `...`

## 🖥️ Environment & Host Diagnostics
- **OS & Kernel**: (e.g. Debian 12 / Ubuntu 24.04 / Windows 11)
- **Python Version**: (e.g. `python --version` -> Python 3.10 / 3.11 / 3.12 / 3.13)
- **Palworld Server Version**: (e.g. v0.3.5)
- **Deployment Mode**: (systemd service / manual CLI / container)

## 📜 Diagnostic Logs & Tracebacks
<!--- Paste relevant journalctl or palworld_manager.log entries below. Sensitive passwords/tokens are automatically redacted. -->
```text
(Paste logs here)
```

## 🛠️ Possible Root Cause / Proposed Solution
<!--- Optional: Suggest an architectural fix or code modification. -->
