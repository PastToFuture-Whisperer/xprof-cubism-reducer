#!/usr/bin/env bash
# Copyright (c) 2026 PastToFuture-Whisperer
# SPDX-License-Identifier: MIT
#
# Safe Verification Execution Wrapper for TensorBoard Trace Log Reducer.
# Version: 1.2.3 (Backward compatible with tb_log_reducer v1.2.0+)
#
# FAILURE HANDLING & ROLLBACK ARCHITECTURE:
# 1. Process Isolation: Utilizes process-ID tagged temporary backups (.bak.$$)
#    to prevent overwriting existing backups from previous interrupted runs.
# 2. Multi-Trace Backup Preservation: Enforces strict single-backup creation 
#    per execution process to prevent modifying active backups during sequential trace runs.
# 3. Signal Trapping: Intercepts SIGINT/SIGTERM/EXIT signals to provide
#    best-effort automatic rollback upon handled execution interruption.
# 4. Environment Compatibility: Bash-based execution wrapper with BSD/GNU find
#    compatibility (macOS, Linux, Conda, Cloud Shell).

set -e

# =====================================================================
# Signal Trap & Emergency Rollback Engine
# Provides best-effort automatic rollback upon process interruption
# =====================================================================
BACKUP_KEYS=()
PIPELINE_SUCCESSFUL=false
# Pre-initialize TRACE_FILES array to ensure global exception safety
# Prevents unbound variable reference in cleanup_and_rollback if killed prior to discovery
TRACE_FILES=()

cleanup_and_rollback() {
    # Skip cleanup if pipeline completed successfully and cleaned up normally
    if [ "${PIPELINE_SUCCESSFUL}" = true ]; then
        return
    fi

    echo ""
    echo "====================================================================="
    echo " [EMERGENCY TRAP] Process interrupted or unexpected exit detected!"
    echo " └─ Initiating rollback from temporary backups..."
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

    echo " [ROLLBACK COMPLETE] Restored original raw trace files where backups were present."
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
# Dynamic Interpreter Discovery (command -v)
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
echo " [PHASE 2] Post-Processing & Verification Pipeline (v1.2.3)"
echo " ├─ Log Dir   : ${LOGDIR}"
echo " └─ Resolution: ${RESOLUTION}%"
echo "====================================================================="

REDUCER_SCRIPT="${SCRIPT_DIR}/tb_log_reducer.py"

if [ ! -f "${REDUCER_SCRIPT}" ]; then
  echo " [ERROR] Reducer script not found at ${REDUCER_SCRIPT}. Aborting pipeline."
  exit 1
fi

# ---------------------------------------------------------------------
# Array Allocation Guard (BSD / GNU find compliant)
# ---------------------------------------------------------------------
TRACE_FILES=()
# Enforce strict Bash 4.4+ version guard for readarray -d support
if [ "${BASH_VERSINFO[0]}" -gt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 4 ]; }; then
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
  # Ensure single backup creation per trace execution to avoid overwriting original
  if [ ! -f "${trace_file}.bak.${PID_SUFFIX}" ]; then
    cp "${trace_file}" "${trace_file}.bak.${PID_SUFFIX}"
  fi
  
  dir_path="$(dirname "${trace_file}")"
  # BSD/GNU find compliant with explicit maxdepth placement
  # Only copy raw .pb files if an isolated backup for this PID does NOT already exist
  while IFS= read -r -d '' pb_file; do
    if [ -f "${pb_file}" ] && [ ! -f "${pb_file}.bak.${PID_SUFFIX}" ]; then
      cp "${pb_file}" "${pb_file}.bak.${PID_SUFFIX}"
    fi
  done < <(find "${dir_path}" -maxdepth 1 -type f -name "*.pb.bak.${PID_SUFFIX}" -print0 2>/dev/null)
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
# Checks gzip readability, JSON parseability, and root data type
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
# Step 4: Finalize or Rollback
# ---------------------------------------------------------------------
if [ "${VERIFICATION_PASSED}" = true ]; then
  echo " [4/4] Structural verification passed (gzip readable / JSON parseable / supported root type). Cleaning up temporary backups..."
  for trace_file in "${TRACE_FILES[@]}"; do
    [ -f "${trace_file}.bak.${PID_SUFFIX}" ] && rm -f "${trace_file}.bak.${PID_SUFFIX}"
    
    dir_path="$(dirname "${trace_file}")"
    while IFS= read -r -d '' pb_bak; do
      [ -f "${pb_bak}" ] && rm -f "${pb_bak}"
    done < <(find "${dir_path}" -maxdepth 1 -type f -name "*.pb.bak.${PID_SUFFIX}" -print0 2>/dev/null)
  done
  PIPELINE_SUCCESSFUL=true
  echo " [COMPLETE] Execution pipeline finished successfully with structural verification."
  # Remove EXIT trap on successful run
  trap - EXIT INT TERM
else
  # Verification failed: Trigger explicit rollback routine
  cleanup_and_rollback
fi
