# PRAXEON v1.0.0 Release Notes

**Runtime Supervision for Autonomous AI Agents**  
*Official Production Release — October 2, 2026*

---

## 1. Executive Summary

PRAXEON v1.0.0 marks the formal production milestone of the runtime supervision and physical execution boundary system for autonomous AI agents. Originally conceived as JEV Reasoning Navigator, version 1.0.0 solidifies the platform into a production-oriented, deterministic, and cryptographically verified control plane that decouples LLM reasoning from operating system execution authority.

Key accomplishments sealed in this milestone:
- **Formal Verification Matrix (`REL-01` through `REL-15`)**: 151 dedicated release specification tests achieving 100% pass rate.
- **Fail-Closed API & Network Hardening**: Mandatory authentication for sensitive endpoints and strict restrictions on non-loopback network bindings (`0.0.0.0`, LAN, public hostnames).
- **Physical Enforcement & Anti-Replay Architecture**: HMAC-SHA256 capability tokens bound multidimensionally to session, state, action, and execution mode, backed by atomic consume-once durable nonce stores immune to 20, 50, and 100-thread concurrent race conditions.
- **Full Access Mode Governance (`FULL_ACCESS_POLICY.md`)**: Formalized architecture where `BLOCK` veredicts unconditionally override execution, `REPLAN` states cannot be bypassed, and autonomous host executions require cryptographically verified operator authorization and audit logging.
- **Multi-Database Crash Recovery**: Transactional SQLite WAL architecture resilient against unexpected process termination, preserving pending reviews, blocking replayed actions, and maintaining strictly monotonic event ordering.
- **Resilient Realtime Streaming**: Monotonic sequence-tracked WebSocket broadcasting with `after_sequence` gap recovery and interactive synchronization (`sync`).
- **Clean Configuration Architecture**: Complete codebase migration from deprecated `JEVConfig` to canonical `PraxeonConfig` with zero runtime warnings.
- **Implemented Context Management & Caching (`praxeon.context`)**: L1 typed fragment cache + L2 snapshot cache with runtime context prefix reuse, strict 8-level token budgeting, DAG-aware structural selection, and verifiable invalidation.
- **Comprehensive Test Regression**: 539 automated tests passing at 100% (540 collected: 539 passed, 1 skipped, 0 failed) across unit, integration, security, context invalidation, execution mode binding, and benchmark suites.

---

## 2. Release Verification Matrix (REL-01 to REL-15)

The v1.0.0 release candidate was subjected to a rigorous 15-point specification matrix implemented under `tests/release/`:

| Spec ID | Category | Description | Test Count | Status |
| :--- | :--- | :--- | :---: | :---: |
| **`REL-01`** | Security / Auth | **Sensitive Endpoint Protection**: Fail-closed API key enforcement across mutation, execution, and inspection routes. Unauthenticated requests are rejected with HTTP 401/403. | 97 | **PASSED** |
| **`REL-02`** | Security / Net | **Network & CORS Hardening**: Server binds to non-loopback addresses (`0.0.0.0`, LAN, hostnames) strictly require configured API keys. Production profile rejects CORS wildcard (`*`) and empty origin configurations. | (incl. in 97) | **PASSED** |
| **`REL-03`** | Governance | **`BLOCK` Precedence Invariant**: In Full Access mode, critical security violations and static preflight blocks unconditionally prevent physical OS execution (0 physical executions guaranteed). | 2 | **PASSED** |
| **`REL-04`** | Governance | **`REPLAN` Preservation**: Trajectory replans and loop remediations cannot be bypassed or executed directly, forcing trajectory backtracking. | 1 | **PASSED** |
| **`REL-05`** | Governance | **Audited Full Access Governance**: Interactive sessions require explicit human operator confirmation. Autonomous execution requires verified operator credentials, and every host action is immutably recorded in the EventStore. | 5 | **PASSED** |
| **`REL-06`** | Cryptography | **Multidimensional Capability Binding**: Capability tokens are cryptographically bound to `session_id`, `action_hash`, and `state_hash`. Mismatched signatures are rejected deterministically. | 3 | **PASSED** |
| **`REL-07`** | Cryptography | **Capability TTL Expiration**: Capabilities contain strict `expires_at` timestamps. Expired tokens are rejected by both `SecureExecutor` and API endpoints. | 2 | **PASSED** |
| **`REL-08`** | Cryptography | **HMAC-SHA256 Integrity Verification**: Payloads with missing signatures, forged keys, or tampered parameters are mathematical-proof rejected prior to OS dispatch. | 3 | **PASSED** |
| **`REL-09`** | Concurrency | **Atomic Replay & Race Condition Immunity**: `NonceStore` consumes execution nonces atomically. Evaluated under 100 concurrent threads competing for the same capability, yielding exactly 1 execution and 99 deterministic rejections. | 4 | **PASSED** |
| **`REL-10`** | Resiliency | **Multi-Database Crash Recovery**: Validated against sudden server crashes. Pending `REVIEW` actions survive restarts, previously executed nonces permanently prevent replays post-crash, and `BLOCK` decisions remain immutable. | 4 | **PASSED** |
| **`REL-11`** | Streaming | **WebSocket Gap Recovery & Liveness**: Monotonic event sequencing with `after_sequence` query parameter, `sync` command over WS, and heartbeat ping/pong keepalives. | 4 | **PASSED** |
| **`REL-12`** | Streaming | **Multi-Client Broadcast & Session Isolation**: Simultaneous event delivery to multiple connected WebSocket subscribers with zero sequence skew and cross-session isolation. | 3 | **PASSED** |
| **`REL-13`** | Supervision | **Canonical Provider Conformity & Circuit Breaker**: TypeSafe and LAYA adapters adhere to standard `ProviderAssessment` contracts with fail-safe error handling and three-state circuit breakers (`CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`). | 13 | **PASSED** |
| **`REL-14`** | Agent E2E | **Live Agent Smoke Test**: End-to-end multi-step agent lifecycle execution (inspection -> grounded mutation -> terminal finish) enforcing evidence collection before completion (`PREMATURE_COMPLETION_WITHOUT_EVIDENCE` defense). | 4 | **PASSED** |
| **`REL-15`** | Reproducibility | **Benchmark Metadata Audit Trail**: Benchmark runner (`scripts/run_benchmarks.py`) injects deterministic execution metadata (`git_commit`, `timestamp`, `praxeon_version`, `model`, `seed`), producing reproducible artifacts. | 6 | **PASSED** |
| **TOTAL** | — | **All 15 Release Specifications Validated** | **151** | **100% PASSED** |

---

## 3. Core Architectural Highlights

### 3.1. Physical Enforcement & Execution Decoupling
PRAXEON enforces a hard physical boundary between agent planning and system execution:
- **`PolicyEngine`**: Evaluates proposed actions against deterministic rules, security bounds, and semantic risk scores.
- **`DecisionReceipt` & Cryptographic Capability**: Emits an HMAC-SHA256 signed capability token containing `execution_mode`, `session_id`, `action_hash`, `state_hash`, and `nonce`.
- **`SecureExecutor`**: Verifies cryptographic integrity, checks non-expired TTL, atomically claims the nonce in `NonceStore`, and dispatches to the configured sandbox or full-access adapter.

### 3.2. Governance of Full Access Mode
As detailed in [`docs/FULL_ACCESS_POLICY.md`](docs/FULL_ACCESS_POLICY.md), `ExecutionMode.FULL_ACCESS` is a deliberate architectural feature designed for real-world software engineering workflows where container or chroot sandboxes introduce artificial friction. 

Under Full Access:
1. **`BLOCK` Always Wins**: Preflight security filters (destructive commands, privilege escalation) can never be overridden.
2. **`REPLAN` Is Uncompromising**: Agent reasoning loops or ungrounded steps are halted for plan revision.
3. **Verified Operator Autonomy**: Unattended execution requires cryptographic proof of operator delegation (`full_access_authorized_by_operator=True`).
4. **Complete Audit Trail**: Every command executed on host OS records an unforgeable event sequence in SQLite WAL storage.

### 3.3. Multi-Database SQLite WAL Resilience
State is segregated across dedicated SQLite databases running in Write-Ahead Logging (`WAL`) mode:
- `praxeon_events.db`: Immutable, monotonically increasing event ledger.
- `praxeon_state.db`: Session state snapshots, active node tracking, and decision tree checkpoints.
- `praxeon_nonces.db`: High-throughput atomic nonce tracking for replay prevention.

In the event of an unexpected SIGKILL or host reboot, PRAXEON recovers state consistently without losing pending human reviews or allowing re-execution of previously executed actions.

### 3.4. Multi-Client Realtime Web Architecture
- **FastAPI Modular Server**: Clean layered structure (`praxeon/server/routes/`, `praxeon/server/dependencies.py`).
- **Modern React + Vite Frontend**: High-fidelity dashboard featuring dark-mode technical styling, interactive Bézier decision trees, and realtime terminal event logs.
- **WebSocket Streaming with Gap Recovery**: Automatically resumes interrupted streams using sequence checkpoints without dropping or duplicating events.

---

## 4. Configuration Migration Guide

### 4.1. Deprecation of `JEVConfig`
Starting with v1.0.0, `JEVConfig` has been formally replaced by `PraxeonConfig`. 

- **Canonical Configuration**:
  ```python
  from praxeon import PraxeonConfig
  
  config = PraxeonConfig(
      api_key="your-api-key",
      storage_dir="./data/praxeon",
      execution_mode="local_restricted",
  )
  ```
- **Backward Compatibility**: `JEVConfig` remains exported as an alias in `praxeon` and `praxeon.config`, emitting a standard `DeprecationWarning` to guide library consumers.

### 4.2. Environment Variables Reference

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `PRAXEON_ENV` | `development` | Deployment profile (`development`, `testing`, `production`). In `production`, strict security audits and CORS restrictions are enforced. |
| `PRAXEON_API_KEY` | `""` | Master API key for Bearer authentication on protected REST endpoints. Mandatory when binding to non-loopback addresses or in `production`. |
| `PRAXEON_OPERATOR_KEY` | `""` | Dedicated operator key for authorizing autonomous Full Access execution. |
| `PRAXEON_SECRET_KEY` | `""` | Secret key used for signing and verifying HMAC-SHA256 decision capabilities. |
| `PRAXEON_CORS_ORIGINS` | `""` | Comma-delimited list of allowed CORS origins. Wildcard `*` is strictly blocked in production. |
| `PRAXEON_HOST` | `127.0.0.1` | Network interface host binding. |
| `PRAXEON_PORT` | `8000` | Port for the FastAPI server. |
| `PRAXEON_STORAGE_DIR` | `./data/praxeon` | Directory for SQLite WAL databases and session persistence. |

---

## 5. Verification & Test Metrics

The v1.0.0 release baseline and post-release context optimization were validated with the entire test suite:

```bash
$ pytest tests/
==================== 539 passed, 1 skipped in ~121s ====================
```

```bash
$ pytest tests/release/ -v
=========================== 151 passed in 34.25s ===========================
```

### Breakdown of Test Suites
- **Unit & Core Invariants**: 199 tests verifying `StateGraph`, `CommandClassifier`, `EvidenceEngine`, `EventBus`, and `CheckpointManager`.
- **API & Security Boundary**: 97 tests covering Bearer auth, CORS policies, path traversal defense, and MCP bridge isolation.
- **Over-Restriction Benchmarks**: 6 test families ensuring 0% false block rate on safe developer operations and 100% block rate on malicious syntax.
- **Context Caching & Invariants (`tests/context/`)**: 9 dedicated tests proving typed fragment immutability, SHA-256 fingerprint determinism, 8-level token budget allocation, runtime context prefix reuse, and the formal security invariant (cache hits never grant capabilities or OS execution).
- **Release Matrix (`tests/release/`)**: 151 tests certifying REL-01 through REL-15 under real network, SQLite WAL, and multi-threaded stress conditions.

---

## 6. Installation & Quick Start

### Installation
```bash
pip install praxeon
```

Or install from source with development dependencies:
```bash
git clone https://github.com/AndreuVM/praxeon.git
cd praxeon
uv sync --all-extras
```

### Launch Web Server & Dashboard
```bash
# Launch server with web UI
praxeon-web --host 127.0.0.1 --port 8000

# Or launch standalone server
praxeon-server --host 127.0.0.1 --port 8000
```

### Run Multi-Step Agent Supervision
```bash
praxeon-live --task "Analyze codebase security and summarize findings" --provider typesafe
```

---

## 7. Status & Future Scope

With the completion of **PRAXEON v1.0.0** and the **Context Caching & Token Optimization** milestone, the runtime supervision baseline and in-memory context management subsystem are fully validated and sealed.

### Implemented & Validated in v1.0 Post-Hardening:
- **`praxeon.context` Subsystem**: L1 fragment cache, L2 snapshot cache with runtime context prefix reuse, DAG-aware structural selector, and token budget priority engine.
- **Strict Authority Invariant**: Context cache is an optimization layer only; `Cache Hit ≠ ALLOW ≠ Capability ≠ Execution`.
- **Reproducible Token Optimization**: Demonstrated 58.2% token reduction (CRR) and 66.7% cache hit rate (CHR) with 100% decision preservation (DP) in 30-step agent trajectories.

### Out of Scope for v1.x (Future Milestones):
- **Provider-Side KV/Prefix Caching**: Direct integration with remote model provider KV-cache APIs.
- **Persistent / Distributed Context Cache**: External distributed caching layer (Redis / SQLite L2 across processes).
- **Cross-Session Semantic Memory & Learned Summarization**: Learned neural compression models.
- **Multi-Agent Context Sharing & Top-K Branching**: Coordination across heterogeneous distributed agent swarms.
