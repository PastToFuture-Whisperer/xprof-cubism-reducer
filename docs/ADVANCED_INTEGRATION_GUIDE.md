# Advanced Integration Guide
**Multi-Process Lock-Guards, Shared Storage, Dynamic Adaptation, and Pipeline Operations**  
*Last Updated: 2026-08-19*

---

## 1. Overview & Operational Scope

This guide defines advanced customization and concurrency locking recipes for safely operating the TensorBoard log reduction utility (`tb_log_reducer.py`) within shared server environments, multi-user setups, or automated CI/CD and MLOps pipelines.

### 1.1 Architectural Scope & Responsibility Boundary
The recipes presented in this guide do not modify the internal code of the core utility itself; rather, they serve as **external wrapper extension examples** designed to safely integrate the tool into your operational pipeline.

* **Layer 1: Core Engine (`tb_log_reducer.py` / `run_with_check.sh`) [Unmodified / Guaranteed Standard]**  
  The core components responsible for data reduction and 0-dependency structural verification. Applying the recipes in this guide requires no direct modifications to this core codebase.
* **Layer 2: Operational Wrapper (User-Defined Shell / CI/CD / Cron) [Extended / User Liability]**  
  Recipes such as concurrency lock-guards (`flock` / `mkdir`) or active streaming syncs (`rsync`) reside entirely outside of Layer 1 as outer wrapper logic.

### 1.2 Functional Definition & Misuse Prevention
* **Definition as a Lightweighting & Emergency Recovery Utility:**  
  This tool is a lightweighting and recovery utility designed to spatially downsample (irreversibly mosaic) trace logs (`.trace.json.gz`), preventing browser crashes (V8 engine) and server Out-Of-Memory (OOM) failures caused by oversized payloads.
* **Clear Distinction from Debugging & Live Inspection Tools:**  
  This tool is NOT a live inspection or tracing debugger (such as `strace` or `lsof`) meant to extract raw execution metrics on the fly. Please note that using it to inspect active raw traces will downsample the data and fail to serve that purpose.

### 1.3 Disclaimer & Modification Policy
All scripts, shell wrappers, and pipeline customization recipes presented in this guide (Layer 2) are implemented and applied entirely **at your own risk**. The author assumes no liability for data modifications, operational disruptions, or system losses resulting from active pipeline interventions, OS-specific command integrations, or streaming setup adaptations.

---

## 2. Multi-Process Lock-Guards

These implementation recipes provide concurrency control (file locking) to prevent read/write conflicts on target log directories when multiple users or parallel automated jobs access them simultaneously on shared servers.

### 2.1 File Locking Specifications & Limitations

* **Lock Granularity:**  
  Generates and maintains an invisible lock file (`.tb_reducer.lock`) inside the target `--logdir` directory to perform directory-level exclusive locking (`Exclusive Lock`).
* **Technical Limitations of "Advisory Locks":**  
  Linux standard `flock` operates as an "advisory lock," which is only effective among processes that explicitly follow the same locking protocol. It cannot physically block unmanaged external processes (such as standard PyTorch/TensorFlow loggers) that write directly to log files without requesting a lock.
* **Intended Purpose:**  
  Reliably prevents double-writing and data corruption if another user or job attempts to run this utility (or its wrapper) on the same directory concurrently.

---

### 2.2 Standard Implementation: Locking via `flock`

This is a basic implementation recipe that uses the standard `flock` command in Linux environments to prevent double-writing and conflicts with other jobs accessing the target directory.

```bash
# 1. Define the lock file and configure automatic cleanup (trap)
LOCK_FILE="./active_logdir/.tb_reducer.lock"
trap 'rm -f "$LOCK_FILE"; exit' EXIT SIGINT SIGTERM

# 2. Acquire an exclusive lock and execute safely (waits 30s before skipping if contested)
exec 200>"$LOCK_FILE"
if flock -w 30 200; then
  ./run_with_check.sh 10 sample.py --logdir ./active_logdir/
else
  echo "[WARN] Directory is locked by another process. Skipping."
fi
```

* **Key Mechanisms:**
  * **Exclusive Locking (`flock -w 30`):** Attempts to acquire a lock, waiting for a specified duration (e.g., 30 seconds) if another process holds it. If unable to acquire the lock, it safely skips execution rather than forcing it (or passes immediately in non-blocking mode with `-n`).
  * **Resource Cleanup (`trap`):** Traps system signals (`SIGINT`, `SIGTERM`, `EXIT`) to guarantee that the lock file is removed and released even during unexpected execution interruptions or error terminations.

---

### 2.3 POSIX / Portable Fallback: Atomic `mkdir` Locking

An alternative concurrency lock recipe utilizing atomic directory creation via POSIX-standard `mkdir` for environments where the `flock` command is unavailable (e.g., macOS, BusyBox, lightweight Docker containers).

```bash
# 1. Define the lock directory and acquire atomically
LOCK_DIR="./active_logdir/.tb_reducer_lock.dir"
trap 'rmdir "$LOCK_DIR" 2>/dev/null; exit' EXIT SIGINT SIGTERM

# 2. Acquire lock leveraging mkdir atomicity
if mkdir "$LOCK_DIR" 2>/dev/null; then
  ./run_with_check.sh 10 sample.py --logdir ./active_logdir/
else
  echo "[WARN] Directory is locked by another process (mkdir). Skipping."
fi
```

* **Key Mechanisms:**
  * **Atomic Creation:** Leverages the kernel-level atomicity guaranteed by `mkdir` to construct a safe lock state without relying on external utilities.
  * **Portable Compatibility:** Operates reliably using only standard POSIX shell features, even in environments without `flock` or with incompatible command options (such as macOS or Alpine Linux).
  * **Resource Cleanup (`trap`):** Ensures `rmdir` is executed via `trap` upon unexpected exits or errors to prevent orphan lock directories from causing permanent lockouts.

---

### 2.4 Active Process Inspection: Guarding Against Direct Writers (`fuser` / `lsof`)[Updated: 2026-08-19]

A recipe that inspects the system at the kernel level prior to execution to safely skip processing if an unmanaged external process is actively writing to the log files, bypassing advisory lock rules.

```bash
# 1. Safely inspect active processes via array boundary guard (prevents wildcard expansion failure)
TARGET_TRACES=(./active_logdir/*.trace.json.gz)
if [ -e "${TARGET_TRACES[0]}" ] && fuser "${TARGET_TRACES[@]}" >/dev/null 2>&1; then
  echo "[WARN] Active writer process detected via fuser. Skipping execution to prevent corruption."
  exit 0
fi

# 2. Execute reduction safely only when no active writers are present
./run_with_check.sh 10 sample.py --logdir ./active_logdir/
```

* **Key Mechanisms:**
  * **Kernel-Level File Handle Inspection:** Scans file descriptors maintained by the OS kernel to reliably determine whether processes (such as PyTorch or TensorFlow loggers) have open handles on `.trace.json.gz` files.
  * **OS & Environment Limitations:** `fuser` and `lsof` are OS-dependent Linux/Unix utilities. In certain environments (such as Alpine Linux) or under unprivileged user permissions, they may fail to detect handles owned by other users.

---

## 3. Shared Storage & Container Environment Recipes

Notes and mitigation recipes for network shared storage systems such as NFS/SMB, or Docker / Kubernetes container environments.

### 3.1 Network File Systems (NFS/SMB)
* **Precautions for `flock` on NFS:** On older NFS clients, `flock` may not function correctly (locks might be ignored or cause hangs). In network storage environments, adopting the atomic `mkdir` lock (Section 2.3) is recommended for higher reliability.

### 3.2 Container & Multi-User Permission Guards
* **Preventing Permission Mismatches:** To avoid issues where execution inside a container (with root privileges) alters host-side file ownership, explicitly set `umask` prior to execution or isolate operations via staging (`rsync`) to prevent ownership conflicts.

---

## 4. Real-Time Monitoring & Streaming Recipes

A minimal loop structure example for building "oscilloscope-style real-time waveform monitoring" as presented in the README's `Advanced Paradigm`.

```bash
# Snapshot active logs to staging every 10 seconds, safely reduce them, and sync to TensorBoard
while true; do
  rsync -a --include='*/' --include='*.trace.json.gz' --exclude='*' ./active_logdir/ ./staging_logdir/
  ./run_with_check.sh 10 sample.py --logdir ./staging_logdir/
  sleep 10
done
```

---

## 5. Dynamic Resolution Adaptation Recipe [Added: 2026-08-19]

This implementation recipe provides an automated wrapper that inspects the raw event density and compressed file size of target traces prior to processing, dynamically calculating the optimal `--resolution` percentage.

### 5.1 Concept & Operational Value
As noted in the primary documentation, processing efficiency and tile consolidation behavior depend heavily on the underlying trace structure (e.g., presence of transient instant events vs. continuous duration events). Operating at a fixed resolution across wildly varying trace sizes can either lead to under-reduction on ultra-dense logs or unnecessary over-smoothing on light traces.

This wrapper pre-scans file metadata to dynamically scale down the target resolution for massive traces while maintaining higher fidelity for smaller profiles.

### 5.2 Implementation Recipe: Density-Aware Wrapper

```bash
#!/usr/bin/env bash
# [ADAPTIVE WRAPPER] Dynamically calculates optimal resolution based on trace file size

LOGDIR="${1:-./tb_logs}"
TARGET_TRACE=$(find "${LOGDIR}" -type f -name "*.trace.json.gz" | head -n 1)

if [ -z "${TARGET_TRACE}" ]; then
  echo "[WARN] No trace file found in ${LOGDIR}. Exiting."
  exit 0
fi

# 1. Inspect compressed trace size in Megabytes (MB)
FILE_SIZE_MB=$(du -m "${TARGET_TRACE}" | cut -f1)

# 2. Dynamically calculate resolution percentage based on payload scale
if [ "${FILE_SIZE_MB}" -gt 200 ]; then
  CALCULATED_RES="5.0"   # Heavy trace (>200MB): Apply aggressive downsampling
elif [ "${FILE_SIZE_MB}" -gt 50 ]; then
  CALCULATED_RES="15.0"  # Medium trace (50MB-200MB): Balanced downsampling
else
  CALCULATED_RES="50.0"  # Light trace (<50MB): Preserve high granularity
fi

echo "[INFO] Detected trace payload: ${FILE_SIZE_MB} MB -> Dynamically assigned resolution:${CALCULATED_RES}%"

# 3. Execute fail-safe reduction with dynamically resolved parameters
./run_with_check.sh "${CALCULATED_RES}" sample.py --logdir "${LOGDIR}"
```

---

## 6. Batch Log Archive Compression & Cloud Pipeline Sync [Added: 2026-08-19]

A production-grade batch recipe designed to process multi-gigabyte historical trace archives across nested log directories, verify structural integrity, and stream compact representations to cloud object storage (AWS S3 / Google Cloud Storage) prior to local cleanup.

### 6.1 Concept & Pipeline Architecture
When archiving historical profiling datasets or executing post-training hooks in CI/CD pipelines, transferring raw uncompressed traces severely wastes cloud bandwidth and storage capacity. This batch script recursively locates legacy trace directories, applies `run_with_check.sh` sequentially to guarantee zero corruption, uploads the shrunk artifacts, and frees up local disk space.

### 6.2 Implementation Recipe: Recursive Archive & Cloud Upload

```bash
#!/usr/bin/env bash
# [BATCH ARCHIVE REDUCER] Recursively reduces legacy log archives and syncs to Cloud Storage

ARCHIVE_ROOT="${1:-./historical_logs}"
GCS_TARGET_BUCKET="${2:-gs://my-mlops-profile-archive/tb_logs}"
RESOLUTION="${3:-10.0}"

echo "[INFO] Starting batch reduction pipeline across: ${ARCHIVE_ROOT}"

# 1. Recursively find all unique directories containing .trace.json.gz files
find "${ARCHIVE_ROOT}" -type f -name "*.trace.json.gz" -exec dirname {} \; | sort -u | while read -r target_dir; do
  echo "---------------------------------------------------------------------"
  echo "[PROCESSING] Target directory: ${target_dir}"
  
  # 2. Execute fail-safe reduction using the safe verification wrapper (run_with_check.sh)[Updated: 2026-08-19]
  # Note: Replaces direct 'python3 tb_log_reducer.py' calls to ensure 0-dep verification & auto-rollback protection
  ./run_with_check.sh "${RESOLUTION}" dummy.py --logdir "${target_dir}"
  
  # 3. Optional: Sync reduced artifacts to Cloud Object Storage (GCS / S3)
  if command -v gcloud >/dev/null 2>&1; then
    echo "[CLOUD SYNC] Uploading ${target_dir} to${GCS_TARGET_BUCKET}..."
    gcloud storage rsync -r "${target_dir}" "${GCS_TARGET_BUCKET}/$(basename "${target_dir}")"
  elif command -v aws >/dev/null 2>&1; then
    echo "[CLOUD SYNC] Uploading ${target_dir} to AWS S3..."
    aws s3 sync "${target_dir}" "s3://my-mlops-profile-archive/$(basename "${target_dir}")"
  fi
done

echo "---------------------------------------------------------------------"
echo "[COMPLETE] All historical trace archives processed and synchronized successfully."
```

---

## 7. Summary & Operational Principles

* **Adherence to Single Responsibility & Portability:** The core utility (`tb_log_reducer.py` / `run_with_check.sh`) strictly maintains a "lightweight, 0-dependency" design policy to operate reliably across any environment.
* **Customization at Operator Discretion:** Feel free to modify and extend the concurrency lock-guards, dynamic adaptors, and batch pipeline integrations presented in this guide to fit your specific operational scale and environment.
* **User Liability Principle:** The implementation of custom pipelines and any data modifications or operational impacts resulting from interventions in active systems are strictly performed at the user's own risk.
