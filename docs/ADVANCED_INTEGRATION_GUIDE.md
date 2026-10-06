# Advanced Integration Guide
**Multi-Process Lock-Guards, Shared Storage, Heuristic Adaptation, and Pipeline Operations**  
*Last Updated: 2026-10-06*

---

## 1. Overview & Operational Scope

This guide defines optional customization and concurrency locking recipes for integrating the TensorBoard log reduction utility ([`tb_log_reducer.py`](../tb_log_reducer_v1.2.3/tb_log_reducer.py)) into shared server environments, multi-user setups, or automated CI/CD and MLOps pipelines.

```text
Operational Model & Workflow Pattern:

Raw / high-resolution profiling data
        │
        ▼
Lightweight overview trace (tb_log_reducer.py)
        │
        ▼
Visual dashboard inspection (Routine Monitoring)
        │
        ▼
Suspicious time / thread region identified
        │
        ▼
Raw data collected or inspected separately (Detailed Analysis)
```

The reduced output created by this tool is an intentionally lossy, low-resolution visualization artifact designed to improve dashboard scalability and reduce browser rendering load. **It is not a replacement for raw profiling data.** Integration recipes in this guide focus on operational convenience—locking, staging, periodic snapshots, and pipeline integration—rather than forensic recovery or full semantic preservation.

### 1.1 Architectural Scope & Responsibility Boundary

The recipes presented in this guide do not modify the internal code of the core utility itself; rather, they serve as **external wrapper extension examples** designed to integrate the tool into your operational pipeline:

* **Layer 1: Core Engine & Verification Wrapper ([`tb_log_reducer.py`](../tb_log_reducer_v1.2.3/tb_log_reducer.py) / [`run_with_check.sh`](../tb_log_reducer_v1.2.3/run_with_check.sh))**  
  The core components responsible for data downsampling, tile consolidation, zero-dependency structural verification, and basic rollback support. Applying the recipes in this guide requires no direct modifications to this core codebase.
* **Layer 2: Operational Extension Wrapper (User-Defined Shell / CI/CD / Cron)**  
  Recipes such as concurrency lock-guards (`flock` / `mkdir`), staging buffers (`rsync`), or cloud synchronization reside entirely outside of Layer 1 as operator-defined integration logic.

### 1.2 Functional Scope & Misuse Prevention

* **Lightweight Overview Generation:**  
  This utility performs deterministic spatial downsampling and adjacent tile consolidation on trace logs (`.trace.json.gz`) to reduce event density, lowering browser rendering overhead (V8 engine) and memory pressure during routine monitoring.
* **Distinction from Debugging & Live Tracing Tools:**  
  This tool is NOT a live inspection debugger or tracing tool (such as `strace` or `lsof`) meant to capture low-level kernel metrics in real time. Passing active, unfinalized traces directly to the reducer may lead to incomplete parsing or unintended downsampling.

### 1.3 Disclaimer & Operational Responsibility

All scripts, shell wrappers, and pipeline customization recipes presented in this guide (Layer 2) are provided as optional reference examples. Operators should evaluate retention, concurrency, filesystem boundaries, and raw-data requirements for their specific environments. The author assumes no liability for data modifications, operational disruptions, or system losses resulting from active pipeline interventions or streaming adaptations.

---

## 2. Multi-Process Lock-Guards

These implementation recipes provide concurrency control (file locking) to reduce read/write conflict risks on target log directories when multiple users or parallel automated jobs access them simultaneously on shared servers.

### 2.1 File Locking Specifications & Limitations

* **Lock Granularity:**  
  Generates and maintains an invisible lock file (`.tb_reducer.lock`) inside the target `--logdir` directory to perform directory-level exclusive locking (`Exclusive Lock`).
* **Limitations of Advisory Locks:**  
  Linux standard `flock` operates as an "advisory lock," which is only effective among processes that explicitly follow the same locking protocol. It cannot physically block unmanaged external processes (such as standard PyTorch/TensorFlow loggers) that write directly to log files without requesting a lock.
* **Intended Purpose:**  
  Reduces double-writing and race condition risks if another managed user or wrapper process attempts to run this utility on the same directory concurrently.

---

### 2.2 Standard Implementation: Locking via `flock`

This recipe uses the standard `flock` command in Linux environments to prevent concurrent execution conflicts in target directories.

```bash
# 1. Define the lock file and configure cleanup on handled termination paths
LOCK_FILE="./active_logdir/.tb_reducer.lock"
trap 'rm -f "$LOCK_FILE"; exit' EXIT SIGINT SIGTERM

# 2. Acquire an exclusive lock and execute safely (waits 30s before skipping if contested)
exec 200>"$LOCK_FILE"
if flock -w 30 200; then
  bash tb_log_reducer_v1.2.3/run_with_check.sh 10 sample.py --logdir ./active_logdir/
else
  echo "[WARN] Directory is locked by another process. Skipping."
fi
```

* **Key Mechanisms:**
  * **Exclusive Locking (`flock -w 30`):** Attempts to acquire a lock, waiting for a specified duration (e.g., 30 seconds) if another process holds it. If unable to acquire the lock, it safely skips execution.
  * **Resource Cleanup (`trap`):** Traps handled signals (`SIGINT`, `SIGTERM`, `EXIT`) to attempt lock file cleanup upon normal execution termination or handled errors.

---

### 2.3 POSIX / Portable Fallback: Atomic `mkdir` Locking

An alternative concurrency lock recipe utilizing atomic directory creation via POSIX-standard `mkdir` for environments where `flock` is unavailable (e.g., macOS, BusyBox, lightweight containers).

```bash
# 1. Define the lock directory and configure cleanup on handled termination
LOCK_DIR="./active_logdir/.tb_reducer_lock.dir"
trap 'rmdir "$LOCK_DIR" 2>/dev/null; exit' EXIT SIGINT SIGTERM

# 2. Acquire lock leveraging mkdir atomicity
if mkdir "$LOCK_DIR" 2>/dev/null; then
  bash tb_log_reducer_v1.2.3/run_with_check.sh 10 sample.py --logdir ./active_logdir/
else
  echo "[WARN] Directory is locked by another process (mkdir). Skipping."
fi
```

* **Key Mechanisms:**
  * **Atomic Creation:** Leverages kernel-level atomicity guaranteed by `mkdir` to construct a basic lock state without relying on external utilities.
  * **Portable Compatibility:** Operates using standard POSIX shell features across environments lacking `flock`.
  * **Cleanup Notes:** If the executing process is abruptly force-killed (`SIGKILL / kill -9`), stale lock directories may remain and require manual removal.

---

### 2.4 Active Process Inspection: Guarding Against Direct Writers (`fuser` / `lsof`)

A recipe that inspects the system prior to execution to safely skip processing if an unmanaged external logger is actively writing to the target trace files.

```bash
# 1. Safely inspect active file handles via array boundary guard
TARGET_TRACES=(./active_logdir/*.trace.json.gz)
if [ -e "${TARGET_TRACES[0]}" ] && fuser "${TARGET_TRACES[@]}" >/dev/null 2>&1; then
  echo "[WARN] Active writer process detected via fuser. Skipping execution to prevent partial reads."
  exit 0
fi

# 2. Execute post-processing safely when no active writers hold handles
bash tb_log_reducer_v1.2.3/run_with_check.sh 10 sample.py --logdir ./active_logdir/
```

* **Key Mechanisms & Limitations:**
  * **Kernel File Handle Inspection:** Scans OS file descriptors to determine whether active processes (such as PyTorch or TensorFlow loggers) maintain open handles on `.trace.json.gz` files.
  * **Environment Dependency:** `fuser` and `lsof` are OS-dependent Linux/Unix utilities and may require appropriate user permissions to inspect handles owned by other processes.

---

## 3. Shared Storage & Container Environment Notes

Operational recommendations and staging options for network file systems (NFS/SMB) and containerized environments (Docker / Kubernetes).

### 3.1 Network File Systems (NFS/SMB)
* **Lock Semantics & Mount Options:** On certain network storage clients, `flock` semantics or atomic rename operations may behave inconsistently depending on protocol version and mount options. In network storage setups, isolating execution via a local staging directory (Section 4) or using atomic `mkdir` locking is recommended.

### 3.2 Container & Multi-User Permission Boundaries
* **Permission Isolation:** When running inside containers with elevated privileges, modified files may inherit root ownership, blocking host-side non-root access. Explicitly configure `umask` prior to execution or use staging copies to avoid host volume permission mismatches.

---

## 4. Real-Time Staging & Oscilloscope-Style Workflows

A staging loop example for building a "dashcam" or "oscilloscope-style" overview workflow, as outlined in the primary README.

```bash
# Snapshot active logs to staging every 10 seconds, generate overview traces, and serve via TensorBoard
while true; do
  rsync -a --include='*/' --include='*.trace.json.gz' --exclude='*' ./active_logdir/ ./staging_logdir/
  bash tb_log_reducer_v1.2.3/run_with_check.sh 10 sample.py --logdir ./staging_logdir/
  sleep 10
done
```

* **Operational Concept:** Rather than passing active training log directories directly to the reducer, the pipeline snapshots trace files to a separate staging directory (`./staging_logdir/`). The reduced overview traces are then served to TensorBoard for fluid, real-time visual inspection without risking active writer corruption.

---

## 5. Heuristic Resolution Adaptation Recipe

An example wrapper script that inspects trace file size prior to processing, dynamically assigning a heuristic `--resolution` percentage based on payload scale.

### 5.1 Concept & Operational Value
Processing efficiency and tile consolidation behavior depend heavily on underlying trace structure (e.g., continuous duration streams versus high-frequency transient spikes). Operating at a single fixed resolution across wildly varying trace scales may result in under-reduction on ultra-dense logs or unnecessary over-smoothing on small traces.

This recipe provides an example policy that pre-scans file metadata to adjust target resolution parameters based on payload size heuristics.

### 5.2 Example Policy Implementation Script

```bash
#!/usr/bin/env bash
# [HEURISTIC ADAPTIVE WRAPPER] Assigns example resolution parameters based on trace payload scale

LOGDIR="${1:-./tb_logs}"
TARGET_TRACE=$(find "${LOGDIR}" -type f -name "*.trace.json.gz" | head -n 1)

if [ -z "${TARGET_TRACE}" ]; then
  echo "[WARN] No trace file found in ${LOGDIR}. Exiting."
  exit 0
fi

# 1. Inspect compressed trace file size in Megabytes (MB)
FILE_SIZE_MB=$(du -m "${TARGET_TRACE}" | cut -f1)

# 2. Apply heuristic resolution policy (Example thresholds - adjust per workload needs)
if [ "${FILE_SIZE_MB}" -gt 200 ]; then
  CALCULATED_RES="5.0"   # Heavy trace (>200MB): Apply aggressive downsampling
elif [ "${FILE_SIZE_MB}" -gt 50 ]; then
  CALCULATED_RES="15.0"  # Medium trace (50MB-200MB): Balanced downsampling
else
  CALCULATED_RES="50.0"  # Light trace (<50MB): Preserve higher granularity
fi

echo "[INFO] Detected trace payload: ${FILE_SIZE_MB} MB -> Assigned heuristic resolution: ${CALCULATED_RES}%"

# 3. Execute post-processing pipeline with assigned heuristic parameter
bash tb_log_reducer_v1.2.3/run_with_check.sh "${CALCULATED_RES}" tb_log_reducer_v1.2.3/sample.py --logdir "${LOGDIR}"
```

---

## 6. Batch Overview Generation & Artifact Sync Examples

An example batch processing script designed to generate lightweight overview artifacts across legacy log directories and synchronize reduced visualization outputs to cloud storage buckets (AWS S3 / Google Cloud Storage).

### 6.1 Archival Policy Notice

> **IMPORTANT ARCHIVAL NOTICE:**  
> Reduced traces generated by this utility are **low-resolution visualization artifacts**, not archival replacements for raw profiling data. If uncompressed high-resolution traces are required for future forensic analysis or deep performance debugging, **retain or archive raw profiling files separately** according to your organization's data retention policy.

### 6.2 Implementation Example: Recursive Batch Processing & Cloud Artifact Sync

```bash
#!/usr/bin/env bash
# [BATCH OVERVIEW REDUCER] Recursively processes trace directories and syncs overview artifacts

ARCHIVE_ROOT="${1:-./historical_logs}"
GCS_TARGET_BUCKET="${2:-gs://my-mlops-profile-archive/tb_overviews}"
RESOLUTION="${3:-10.0}"

echo "[INFO] Starting batch overview generation across: ${ARCHIVE_ROOT}"

# 1. Recursively locate unique directories containing .trace.json.gz files
find "${ARCHIVE_ROOT}" -type f -name "*.trace.json.gz" -exec dirname {} \; | sort -u | while read -r target_dir; do
  echo "---------------------------------------------------------------------"
  echo "[PROCESSING] Target directory: ${target_dir}"
  
  # 2. Execute reduction wrapper sequentially (provides 0-dep structural verification & handled rollback support)
  bash tb_log_reducer_v1.2.3/run_with_check.sh "${RESOLUTION}" tb_log_reducer_v1.2.3/sample.py --logdir "${target_dir}"
  
  # 3. Optional: Sync reduced overview artifacts to Cloud Storage (GCS / S3)
  if command -v gcloud >/dev/null 2>&1; then
    echo "[CLOUD SYNC] Syncing overview artifacts in ${target_dir} to ${GCS_TARGET_BUCKET}..."
    gcloud storage rsync -r "${target_dir}" "${GCS_TARGET_BUCKET}/$(basename "${target_dir}")"
  elif command -v aws >/dev/null 2>&1; then
    echo "[CLOUD SYNC] Syncing overview artifacts in ${target_dir} to AWS S3..."
    aws s3 sync "${target_dir}" "s3://my-mlops-profile-archive/$(basename "${target_dir}")"
  fi
done

echo "---------------------------------------------------------------------"
echo "[COMPLETE] Batch overview generation and artifact synchronization completed."
```

---

## 7. Operational Summary & Core Principles

1. **Keep the Core Lightweight:** The core engine ([`tb_log_reducer.py`](../tb_log_reducer_v1.2.3/tb_log_reducer.py) / [`run_with_check.sh`](../tb_log_reducer_v1.2.3/run_with_check.sh)) strictly maintains a zero-dependency design, focusing on spatial downsampling, basic structural verification, and handled rollback support.
2. **Treat Reduced Traces as Overview Artifacts:** Reduced outputs are low-resolution visualization artifacts designed to lower browser rendering load, not replacements for raw profiling data.
3. **Decouple Pipeline Extensions:** Implement concurrency locking, staging buffers, periodic streaming, and cloud sync externally via outer wrapper scripts as required by your operating environment.
4. **Separate Raw Data Retention:** Retain or collect uncompressed high-resolution profiling traces separately whenever detailed forensic investigation is required.
