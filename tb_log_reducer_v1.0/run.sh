#!/usr/bin/env bash
# Copyright (c) 2026 PastToFuture-Whisperer
# SPDX-License-Identifier: MIT
#
# Transparent execution wrapper for TensorBoard Trace Log Reducer.
# Intercepts log generation without modifying the underlying target execution pipeline.

set -e

# =====================================================================
# Robust Script Real-Path Resolution (Symlink Tolerant)
# Resolves the absolute directory of the script even when executed via symlinks
# =====================================================================
RESOLVED_SELF="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "${RESOLVED_SELF}")" && pwd)"

# =====================================================================
# Dynamic Interpreter Discovery (POSIX Standard command -v)
# Dynamically resolves active Python binary across diverse environments (venv, Conda, Cloud Shell, macOS)
# =====================================================================
PYTHON_BIN=$(command -v python3 || command -v python || true)
if [ -z "${PYTHON_BIN}" ]; then
    echo " [ERROR] No valid Python interpreter (python3/python) found in system PATH."
    exit 1
fi

# =====================================================================
# Robust Argument Parsing & Boundary Guard
# Validates numeric resolution against secondary target script existence to prevent misidentifying numerical filenames.
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

# Early Validation: Check target script existence immediately to prevent late failure
if [[ ! -f "${TARGET_SCRIPT}" ]]; then
    echo " [ERROR] Target script '${TARGET_SCRIPT}' does not exist or is not a regular file."
    exit 1
fi

# =====================================================================
# Dynamic LOGDIR Extraction (Safe Loop without set -e / C-style evaluation risk)
# Parses --logdir or --log_dir from target script arguments
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

# Fallback precedence: Extracted CLI Arg -> Environment Variable -> Default Directory
LOGDIR="${EXTRACTED_LOGDIR:-${TB_LOG_DIR:-./tb_logs}}"

echo "====================================================================="
echo " [PHASE 1] Executing Target Script via Dynamic Interpreter: ${PYTHON_BIN}"
echo " ├─ Script : ${TARGET_SCRIPT}"
echo " └─ Args   : ${SCRIPT_ARGS[*]}"
echo "====================================================================="

# PHASE 1: Execute primary benchmark/training script
"$PYTHON_BIN" "${TARGET_SCRIPT}" "${SCRIPT_ARGS[@]}"

echo "====================================================================="
echo " [PHASE 2] Executing Post-Processing TensorBoard Trace Log Reducer"
echo " ├─ Log Dir   : ${LOGDIR}"
echo " └─ Resolution: ${RESOLUTION}%"
echo "====================================================================="

# PHASE 2: Execute non-invasive trace reduction post-processor using absolute script directory
REDUCER_SCRIPT="${SCRIPT_DIR}/tb_log_reducer.py"

if [ -f "${REDUCER_SCRIPT}" ]; then
    "$PYTHON_BIN" "${REDUCER_SCRIPT}" --logdir "${LOGDIR}" --resolution "${RESOLUTION}"
else
    echo " [WARNING] Reducer script not found at ${REDUCER_SCRIPT}. Skipping reduction phase."
fi

echo " [COMPLETE] Execution pipeline finished successfully."