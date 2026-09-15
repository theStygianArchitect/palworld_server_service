---
name: "Feature Request: Root Privileged IPC Sidecar Daemon (palworld-supervisor) (#62)"
about: "Isolate privileged host operations (systemctl, reboot, network probes) into a hardened, memory-safe Unix Domain Socket sidecar daemon"
labels: enhancement, architecture, security, backlog
---

# Feature Request: Root Privileged IPC Sidecar Daemon (`palworld-supervisor`) (#62)

> 🔗 **Tracked GitHub Issue**: [#62](https://github.com/theStygianArchitect/palworld_server_service/issues/62)  
> 🏷️ **Labels**: `type:feature`, `priority:p2`, `architecture`, `security`

---

## 🚀 Feature Proposal
Transition the Palworld Operations Suite from the existing `sudoers` + `subprocess.run(["sudo", ...])` model to a dedicated, unexported **Root Privileged IPC Sidecar Daemon (`palworld-supervisor`)**.

The web application (`palworld-manager.service`) will run as an unprivileged, non-root system user (`palmanager`) with zero entries in `/etc/sudoers.d/`. All privileged host operations—such as restarting systemd units, executing delayed system reboots, and performing privileged network diagnostics—will be dispatched over a local **Unix Domain Socket (UDS)** using a **Newline-Delimited JSON (NDJSON)** stream and **JSON-RPC 2.0** protocol, strictly validated via **Pydantic v2** schemas and protected by Linux kernel **`SO_PEERCRED`** DAC credentials.

---

## 🎯 Problem / User Story

### Current State
- The FastAPI management application runs under the `palmanager` user.
- To perform privileged tasks (restarting `palworld-manager.service` post-TLS renewal, rebooting the dedicated game server or host, probing network ports), the host relies on `/etc/sudoers.d/palmanager` grants.
- Commands are invoked synchronously or in background threads via Python `subprocess.run(["sudo", "/usr/bin/systemctl", ...], ...)`.

### Threat Model & Limitations
1. **Sudoers Attack Surface**: Granting an unprivileged web application account passwordless sudo privileges to binaries like `/usr/bin/systemctl` or shell scripts creates a potential privilege escalation vector if the web application suffers a remote code execution (RCE) or path injection flaw.
2. **Command Injection Risks**: Passing dynamic arguments across `subprocess.run(["sudo", ...])` requires meticulous command-line escaping. Any quoting error can lead to shell injection.
3. **Process Spawning Overhead**: Forking `sudo` and invoking the shell/exec machinery introduces process spawning latency (10-30ms per invocation) and leaves untracked child processes.
4. **Fragile Error Handling**: Raw subprocess exits only return an integer returncode and raw stderr text, making structured error handling, retry logic, and diagnostic inspection difficult.

### User Story
> *As a system administrator and security engineer, I want all privileged host operations isolated behind a strictly validated, non-networked local IPC boundary running as a dedicated systemd daemon, so that the web-facing application cannot be leveraged for privilege escalation, sudoers rules are completely decommissioned, and operations are strictly type-checked and audited.*

---

## 💡 Proposed Solution & Architecture

```
+-----------------------------------------------------------------------------------+
| Host: aburame-shino (Ubuntu Linux)                                                |
|                                                                                   |
|  +-------------------------------------+                                          |
|  | Web Service: palworld-manager       |                                          |
|  | - UID: palmanager (Non-Root)        |                                          |
|  | - Port: 8080 (HTTPS / WSS)          |                                          |
|  | - Zero sudo privileges in sudoers   |                                          |
|  +------------------+------------------+                                          |
|                     |                                                             |
|                     | Unix Domain Socket (UDS) Client                             |
|                     | Path: /run/palmanager/supervisor.sock                       |
|                     | Framing: NDJSON \n | Protocol: JSON-RPC 2.0                 |
|                     |                                                             |
|                     v                                                             |
|  +------------------+------------------+                                          |
|  | Sidecar Daemon: palworld-supervisor |                                          |
|  | - UID: root (Managed by systemd)    |                                          |
|  | - DAC Permissions: 0660 root:palmgr |                                          |
|  | - Auth: Kernel SO_PEERCRED check    |                                          |
|  | - Validation: Pydantic v2 schemas   |                                          |
|  +------------------+------------------+                                          |
|                     |                                                             |
|                     +---> systemd D-Bus / systemctl (Restart, Status, Reload)     |
|                     +---> OS Kernel Syscalls (reboot, sync, network ingress)      |
+-----------------------------------------------------------------------------------+
```

### 1. Process & Privilege Boundary
- **`palworld-manager.service`**: Runs under UID `palmanager:palmanager`. Binds port `8080`. Absolutely zero sudoers configuration (`/etc/sudoers.d/palmanager` is removed).
- **`palworld-supervisor.service`**: Runs under UID `root:root`. Managed as an internal systemd daemon (or socket-activated unit). It exposes no TCP/UDP listeners and listens strictly on a local Unix socket.

### 2. Transport Mechanism: Unix Domain Socket (UDS)
- **Filesystem Path**: `/run/palmanager/supervisor.sock` (created inside a dedicated `/run/palmanager/` directory managed by systemd `RuntimeDirectory=palmanager`).
- **Discretionary Access Control (DAC)**: File permissions set to `0660` with ownership `root:palmanager`. Only `root` and members of the `palmanager` group can open or write to the socket.
- **Kernel-Enforced Peer Authentication (`SO_PEERCRED`)**:
  - Upon accepting a connection, the supervisor calls `getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, ...)` to retrieve the peer's PID, UID, and GID directly from the Linux kernel.
  - The supervisor unconditionally rejects any connection where `peer_uid != palmanager_uid` and `peer_uid != 0`.
- **Zero Network Exposure**: Bypasses the network stack entirely (2x throughput, 0.5x latency compared to loopback TCP). Completely immune to SSRF, external port scanning, and DNS rebinding attacks.

### 3. Encryption & Wire Protocol Rationale
- **Why TLS/Encryption is Explicitly Excluded**:
  - The socket communicates exclusively through Linux kernel memory buffers in RAM on the same physical machine.
  - Network packet sniffers (`tcpdump`) cannot capture local Unix domain sockets without root privileges.
  - If an adversary has root privileges to inspect host RAM or use `ptrace`, TLS encryption provides zero cryptographic protection.
  - Omitting TLS eliminates handshake latency, TLS certificate expiration risks, and OpenSSL overhead for local IPC.
- **Framing**: Newline-Delimited JSON (`\n`). Each request and response is framed as a single UTF-8 JSON object terminated by a newline byte (`0x0A`), natively supported by `asyncio.StreamReader.readline()`.
- **Protocol**: JSON-RPC 2.0 specification (`jsonrpc: "2.0"`, `id`, `method`, `params`).

### 4. Schema Contracts & Supported RPC Methods

```json
// Request: Restart service
{
  "jsonrpc": "2.0",
  "id": "req-101",
  "method": "service.restart",
  "params": {
    "service_name": "palworld-dedicated.service",
    "timeout_seconds": 30
  }
}

// Response: Success
{
  "jsonrpc": "2.0",
  "id": "req-101",
  "result": {
    "status": "success",
    "service_name": "palworld-dedicated.service",
    "active_state": "active",
    "sub_state": "running"
  }
}
```

#### Supported RPC Method Whitelist
The supervisor enforces a strict whitelist of callable methods and allowed targets:
1. `service.restart`: Restarts an allowed unit (`palworld-dedicated.service`, `palworld-manager.service`). Rejects arbitrary service names.
2. `service.status`: Queries unit active/sub state without spawning subprocesses.
3. `service.reload`: Reloads systemd daemon or target unit.
4. `system.reboot`: Dispatches a scheduled, delayed host reboot (`reboot_in_seconds`, `reason`).
5. `network.probe_port`: Tests external or internal port reachability using native non-blocking socket probes.

---

## 🛡️ Memory Safety & Language Evaluation

During architectural evaluation, language selection for the supervisor daemon was audited across three candidates:

| Criterion | C | Python 3.12 (Selected) | Rust (Alternative) |
| :--- | :--- | :--- | :--- |
| **Memory Safety Model** | ❌ **Unsafe**: Manual pointer arithmetic, malloc/free, no bounds checks | ✅ **Safe**: Managed runtime, garbage collection, strict bounds checks | ✅ **Safe**: Compile-time borrow checker, zero GC, strict bounds checks |
| **Exploit Vulnerability** | **Severe**: Buffer overflows, heap corruption, use-after-free lead to root RCE | **Near-Zero**: CPython runtime abstracts memory; pure Python cannot smash stack | **Zero**: Compiler prevents memory bugs at compile-time |
| **Parser Engine** | Manual parsing (`cJSON`) vulnerable to malformed payloads | **Rust**: Pydantic v2 is powered by `pydantic-core` (compiled Rust) | Native `serde_json` (compiled Rust) |
| **RAM Footprint** | ~2-4 MB | ~30-40 MB | ~4-8 MB |
| **Build & Toolchain** | Make/GCC; fragile C dependency management | Standard Python virtualenv & Poetry (`pyproject.toml`) | Requires `cargo`, `rustc`, and multi-stage CI builds |
| **Model Drift Risk** | High (must manually sync C structs with Python Pydantic) | **Zero** (FastAPI and Supervisor share identical `app/supervisor/protocol.py`) | Medium (must keep Rust `serde` models synced with Python) |

### Language Decision: Python 3.12 with Pydantic v2
- **C was explicitly rejected**: Deploying a root-privileged daemon written in C that parses socket strings introduces an unacceptable memory-corruption attack surface into the root domain.
- **Python was selected as the optimal baseline**: It is fully memory-safe, shares the exact same Pydantic schemas with FastAPI (eliminating contract drift), requires zero additional build toolchains, and its JSON validation layer is already compiled Rust via `pydantic-core`.
- **Rust remains a documented future alternative**: If host memory pressure on low-tier VPS environments warrants optimizing the daemon's RAM from 35MB down to 5MB, the JSON-RPC 2.0 NDJSON protocol specification allows a drop-in Rust binary replacement without changing a single line of client code in FastAPI.

---

## 📦 Repository Topology: Monorepo vs. Separate Project

### Architectural Decision: Keep Bundled in Monorepo (`app/supervisor/`)
1. **Single Source of Truth**: The Pydantic RPC models (`app/supervisor/protocol.py`) are imported directly by both the client caller in FastAPI (`app/services/supervisor_client.py`) and the daemon server (`app/supervisor/daemon.py`). This guarantees compile-time/typecheck-time synchronization with zero drift.
2. **Unified Quality Gates**: The supervisor is checked by the exact same static analysis pipeline: Pylint (10.00/10), Bandit, Pytest, and Mypy.
3. **Synchronized Host Deployments**: `scripts/deploy.sh` installs and restarts both `palworld-manager.service` and `palworld-supervisor.service` atomically in a single execution.
4. **Clear Process Boundary**: Process isolation is enforced by the operating system kernel and systemd (separate processes, different UIDs, socket boundary), not by arbitrary Git repository splits.

---

## 🧱 12-Factor & Resilience Considerations

- **Factor I. One Codebase**: Maintained within the single repository under `app/supervisor/`.
- **Factor III. Store Config in the Environment**: Socket path (`SUPERVISOR_SOCKET_PATH=/run/palmanager/supervisor.sock`) and log level are passed via systemd `EnvironmentFile` or unit environment directives.
- **Factor VI. Stateless Processes**: The supervisor maintains zero state across requests. Every JSON-RPC call is discrete and atomic.
- **Factor IX. Disposability**: Supervisor installs `SIGTERM` and `SIGINT` handlers to drain active requests, close client connections, and cleanly remove the socket file from `/run/palmanager/`.
- **Factor XI. Logs as Event Streams**: Supervisor writes structured JSON logs directly to `stdout`/`stderr`, collected natively by systemd `journald` (`journalctl -u palworld-supervisor.service`).

---

## 🔄 Alternatives Considered

1. **Retain Status Quo (`sudoers` + `subprocess.run`)**:
   - *Verdict*: Rejected. Violates principle of least privilege; maintains long-term command-line injection risk and sudoers configuration complexity.
2. **Localhost TCP Socket (`127.0.0.1:9090`)**:
   - *Verdict*: Rejected. TCP ports on localhost can be bound or probed by any local user or malicious container, and are subject to port conflicts and SSRF. UDS provides kernel-enforced file permissions and `SO_PEERCRED` validation.
3. **gRPC / Protocol Buffers**:
   - *Verdict*: Rejected. Requires compiling `.proto` files, managing code generators, and makes manual debugging from the shell via `socat` difficult. NDJSON + JSON-RPC 2.0 is human-readable, easily debugged via CLI, and thoroughly validated by Pydantic v2.

---

## 🔐 Role-Based Access Control (RBAC) & Target Audience

- **Target Audience**: Internal infrastructure component (Admin-Only backing service).
- **Socket Permissions**: `0660` owned by `root:palmanager`.
- **Caller Restriction**: Only authenticated requests originating from the `palmanager` user (validated via `SO_PEERCRED`) are executed. All other attempts are logged as security alerts and terminated immediately.
- **Application RBAC**: The FastAPI REST endpoints that trigger supervisor actions (`POST /api/service/reboot`, `/api/service/restart`) remain strictly guarded by `admin_required` session dependencies.
