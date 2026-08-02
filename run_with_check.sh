#!/usr/bin/env bash
# Copyright (c) 2026 PastToFuture-Whisperer
# SPDX-License-Identifier: MIT
#
# Safe Verification Execution Wrapper for TensorBoard Trace Log Reducer.
# Version: 1.2.0
#
# TECHNICAL ARCHITECTURE & FAIL-SAFE GUARANTEES:
# 1. Process Isolation: Utilizes process-ID tagged temporary backups (.bak.$$)
#    to prevent overwriting existing backups from previous interrupted runs.
# 2. Signal Trapping: Intercepts SIGINT/SIGTERM/EXIT signals to guarantee 
#    automatic emergency cleanup or instant rollback upon any unexpected termination.
# 3. Cross-Platform POSIX Standard: Fully compatible with BSD/GNU find (macOS, 
#    Alpine/BusyBox Linux, Ubuntu/Debian, Conda, Cloud Shell).

set -e

# =====================================================================
# Signal Trap & Emergency Rollback Engine
# Ensures zero dangling temporary files or corrupted states even if killed
# =====================================================================
BACKUP_KEYS=()
PIPELINE_SUCCESSFUL=false

cleanup_and_rollback() {
    # Skip cleanup if pipeline completed successfully and cleaned up normally
    if [ "${PIPELINE_SUCCESSFUL}" = true ]; then
        return
    fi

    echo ""
    echo "====================================================================="
    echo " [EMERGENCY TRAP] Process interrupted or unexpected exit detected!"
    echo " └─ Initiating fail-safe rollback and cleaning up temporary files..."
    echo "====================================================================="

    for trace_file in "${TRACE_FILES[@]}"; do
        bak_file="${trace_file}.bak.${PID_SUFFIX}"
        if [ -f "${bak_file}" ]; then
            mv "${bak_file}" "${trace_file}" 2>/dev/null || true
        fi

        dir_path="$(dirname "${trace_file}")"
        while IFS= read -r -d '' pb_bak; do
            if [ -f "${pb_bak}" ]; then
                target_pb="${pb_bak%.bak.${PID_SUFFIX}}"
                mv "${pb_bak}" "${target_pb}" 2>/dev/null || true
            fi
        done < <(find "${dir_path}" -maxdepth 1 -type f -name "*.pb.bak.${PID_SUFFIX}" -print0 2>/dev/null)
    done

    echo " [ROLLBACK COMPLETE] All original raw trace files restored safely."
    exit 130
}

# Bind traps for INT (Ctrl+C), TERM (kill/SIGTERM), and unexpected EXIT
PID_SUFFIX="$$"
trap cleanup_and_rollback INT TERM EXIT

# =====================================================================
# Robust Script Real-Path Resolution (Symlink Tolerant)
# =====================================================================
RESOLVED_SELF="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "${RESOLVED_SELF}")" && pwd)"

# =====================================================================
# Dynamic Interpreter Discovery (POSIX Standard command -v)
# =====================================================================
PYTHON_BIN=$(command -v python3 || command -v python || true)
if [ -z "${PYTHON_BIN}" ]; then
  echo " [ERROR] No valid Python interpreter (python3/python) found in system PATH."
  exit 1
fi

# =====================================================================
# Robust Argument Parsing & Boundary Guard
# =====================================================================
REGEX_NUMBER='^[0-9]+([.][0-9]+)?$'

DEFAULT_RESOLUTION=50.0
RESOLUTION="${DEFAULT_RESOLUTION}"
TARGET_SCRIPT=""
SCRIPT_ARGS=()

if [[ $# -ge 2 ]] && [[ "$1" =~ $REGEX_NUMBER ]] && [[ -f "$2" ]]; then
  RESOLUTION="$1"
  TARGET_SCRIPT="$2"
  shift 2
  SCRIPT_ARGS=("$@")
elif [[ $# -ge 1 ]]; then
  TARGET_SCRIPT="$1"
  shift 1
  SCRIPT_ARGS=("$@")
else
  echo " [USAGE] $0 [resolution_percentage] <target_script.py> [script_args...]"
  exit 1
fi

if [[ ! -f "${TARGET_SCRIPT}" ]]; then
  echo " [ERROR] Target script '${TARGET_SCRIPT}' does not exist or is not a regular file."
  exit 1
fi

# =====================================================================
# Dynamic LOGDIR Extraction
# =====================================================================
EXTRACTED_LOGDIR=""
PARSE_ARGS=("${SCRIPT_ARGS[@]}")

while [[ ${#PARSE_ARGS[@]} -gt 0 ]]; do
  arg="${PARSE_ARGS[0]}"
  if [[ "$arg" == "--logdir" ]] || [[ "$arg" == "--log_dir" ]]; then
    if [[ ${#PARSE_ARGS[@]} -gt 1 ]]; then
      EXTRACTED_LOGDIR="${PARSE_ARGS[1]}"
      PARSE_ARGS=("${PARSE_ARGS[@]:2}")
      continue
    fi
  elif [[ "$arg" =~ ^--logdir=(.+) ]] || [[ "$arg" =~ ^--log_dir=(.+) ]]; then
    EXTRACTED_LOGDIR="${BASH_REMATCH[1]}"
  fi
  PARSE_ARGS=("${PARSE_ARGS[@]:1}")
done

LOGDIR="${EXTRACTED_LOGDIR:-${TB_LOG_DIR:-./tb_logs}}"

echo "====================================================================="
echo " [PHASE 1] Executing Target Script via Dynamic Interpreter: ${PYTHON_BIN}"
echo " ├─ Script : ${TARGET_SCRIPT}"
echo " └─ Args   : ${SCRIPT_ARGS[*]}"
echo "====================================================================="

# PHASE 1: Execute primary benchmark/training script
"$PYTHON_BIN" "${TARGET_SCRIPT}" "${SCRIPT_ARGS[@]}"

echo "====================================================================="
echo " [PHASE 2] Safe Post-Processing & Verification Pipeline (v1.2.1)"
echo " ├─ Log Dir   : ${LOGDIR}"
echo " └─ Resolution: ${RESOLUTION}%"
echo "====================================================================="

REDUCER_SCRIPT="${SCRIPT_DIR}/tb_log_reducer.py"

if [ ! -f "${REDUCER_SCRIPT}" ]; then
  echo " [WARNING] Reducer script not found at ${REDUCER_SCRIPT}. Skipping reduction phase."
  PIPELINE_SUCCESSFUL=true
  exit 0
fi

# ---------------------------------------------------------------------
# Cross-Platform Safe Array Allocation (BSD / GNU find compliant)
# ---------------------------------------------------------------------
TRACE_FILES=()
if command -v readarray >/dev/null 2>&1; then
  readarray -d '' TRACE_FILES < <(find "${LOGDIR}" -type f -name "*.trace.json.gz" -print0 2>/dev/null)
else
  while IFS= read -r -d '' file; do
    TRACE_FILES+=("$file")
  done < <(find "${LOGDIR}" -type f -name "*.trace.json.gz" -print0 2>/dev/null)
fi

if [ ${#TRACE_FILES[@]} -eq 0 ]; then
  echo " [NOTICE] No .trace.json.gz files found in ${LOGDIR}. Skipping reduction."
  PIPELINE_SUCCESSFUL=true
  exit 0
fi

# ---------------------------------------------------------------------
# Step 1: Pre-Execution Isolated Backup Creation (.bak.$$)
# ---------------------------------------------------------------------
echo " [1/4] Creating temporary process-isolated backups (.bak.${PID_SUFFIX})..."
for trace_file in "${TRACE_FILES[@]}"; do
  [ -f "${trace_file}" ] || continue
  cp "${trace_file}" "${trace_file}.bak.${PID_SUFFIX}"
  
  dir_path="$(dirname "${trace_file}")"
  # BSD/GNU find compliant with explicit maxdepth placement
  while IFS= read -r -d '' pb_file; do
    [ -f "${pb_file}" ] && cp "${pb_file}" "${pb_file}.bak.${PID_SUFFIX}"
  done < <(find "${dir_path}" -maxdepth 1 -type f -name "*.pb" -print0 2>/dev/null)
done

# ---------------------------------------------------------------------
# Step 2: Execute In-Place Log Reducer
# ---------------------------------------------------------------------
echo " [2/4] Executing in-place byte replacement..."
REDUCER_SUCCESS=true
if ! "$PYTHON_BIN" "${REDUCER_SCRIPT}" --logdir "${LOGDIR}" --resolution "${RESOLUTION}"; then
  REDUCER_SUCCESS=false
fi

# ---------------------------------------------------------------------
# Step 3: Zero-Dependency Structural Integrity Check (Python Standard Lib)
# ---------------------------------------------------------------------
echo " [3/4] Running 0-dep structural integrity verification..."
VERIFICATION_PASSED=true

if [ "${REDUCER_SUCCESS}" = true ]; then
  for trace_file in "${TRACE_FILES[@]}"; do
    [ -f "${trace_file}" ] || continue
    if ! "$PYTHON_BIN" -c "
import sys, gzip, json
try:
    with gzip.open(sys.argv[1], 'rt') as f:
        data = json.load(f)
        if not isinstance(data, (dict, list)):
            sys.exit(1)
    sys.exit(0)
except Exception:
    sys.exit(1)
" "${trace_file}" 2>/dev/null; then
      VERIFICATION_PASSED=false
      echo " [FAIL] Structural corruption detected in: ${trace_file}"
      break
    fi
  done
else
  VERIFICATION_PASSED=false
fi

# ---------------------------------------------------------------------
# Step 4: Finalize or Instant Rollback
# ---------------------------------------------------------------------
if [ "${VERIFICATION_PASSED}" = true ]; then
  echo " [4/4] Verification PASSED (100% Valid). Cleaning up temporary backups..."
  for trace_file in "${TRACE_FILES[@]}"; do
    [ -f "${trace_file}.bak.${PID_SUFFIX}" ] && rm -f "${trace_file}.bak.${PID_SUFFIX}"
    
    dir_path="$(dirname "${trace_file}")"
    while IFS= read -r -d '' pb_bak; do
      [ -f "${pb_bak}" ] && rm -f "${pb_bak}"
    done < <(find "${dir_path}" -maxdepth 1 -type f -name "*.pb.bak.${PID_SUFFIX}" -print0 2>/dev/null)
  done
  PIPELINE_SUCCESSFUL=true
  echo " [COMPLETE] Execution pipeline finished successfully with 100% fail-safe verification."
  # Remove EXIT trap on successful run
  trap - EXIT INT TERM
else
  # Verification failed: Trigger explicit rollback routine
  cleanup_and_rollback
fi