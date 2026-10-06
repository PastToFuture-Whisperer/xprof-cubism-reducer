#!/usr/bin/env python3
# Copyright (c) 2026 PastToFuture-Whisperer
# SPDX-License-Identifier: MIT
#
# Version: 1.2.3 (Patch update: Multi-process (pid, tid) grouping fix, accurate metrics separation, and refined safety boundaries)
#
# This program is a byproduct of the advanced profile optimization research 
# mentioned in the documentation; those core features are explicitly excluded 
# from this repository and implemented separately.

__version__ = "1.2.3"

import argparse
import os
import sys
import glob
import gzip
import json
import math
import re
import gc
from collections import defaultdict
from typing import List, Dict, Any, Tuple

# =====================================================================
# TENSORBOARD / XPROF TRACE VISUALIZATION REDUCER
#
# PURPOSE & OPERATIONAL MODEL:
# Creates a lightweight, visualization-oriented overview of large traces
# (.trace.json.gz) to reduce browser-side rendering and memory pressure
# during routine dashboard monitoring ("dashcam-style" profiling).
#
# NOTICE (INTENTIONALLY LOSSY):
# - This tool is INTENTIONALLY LOSSY and NOT a lossless trace compressor.
# - Do NOT replace raw profiling data; retain or collect raw / high-resolution
#   profiling data separately when detailed analysis is required.
#
# DETAILED INTEGRATION & OPERATIONAL SCOPE:
# For production concurrency lock-guards, active process inspection, dynamic
# resolution scaling, and pipeline recipes, please refer to:
#   docs/ADVANCED_INTEGRATION_GUIDE.md
# =====================================================================

# =====================================================================
# CORE ALGORITHM CONSTANTS (DO NOT ALTER WITHOUT BENCHMARKING)
# =====================================================================
class ReducerConfig:
    """
    Core configuration class controlling deterministic behavior and safety boundaries of the algorithm.
    Strictly avoid arbitrary modifications as parameter tuning is calibrated against mathematical models 
    and rendering load benchmarks.
    """
    EPSILON_MERGE_THRESHOLD: float = 1e-2  # Threshold for adjacent merging to absorb floating-point rounding errors (µs)
    SAFETY_MARGIN_RATIO: float = 1.15      # Safety margin multiplier for operational boundary simulation
    DEFAULT_PRECISION_DECIMALS: int = 3   # Rounding precision for timestamps and durations (Microseconds)
    BASE_SAFETY_BUFFER: float = 1.0        # Safety buffer percentage for dynamic limit resolution calculation (%)
    MIN_CHUNKS_LOW: int = 3                # Minimum chunk count in low-resolution regimes
    MAX_CHUNKS_MID: int = 1000             # Maximum chunk count in mid-resolution regimes
    MAX_CHUNKS_HIGH: int = 5000            # Maximum chunk count in high-resolution regimes
    LARGE_TRACE_THRESHOLD_MB: float = 100.0# File size threshold (MB) for emitting heavy memory load warning
    SIMULATION_SCALE_FACTOR: float = 10.0  # Scale factor for boundary simulation calculation

def verify_protobuf_wire2_boundary(modified_bytes: bytearray, idx: int, target_len: int) -> bool:
    """
    Validates Protobuf Wire Type 2 (Length-delimited) boundary backwards with correct 
    Little-Endian Varint decoding for multi-byte lengths (>= 128 bytes).
    """
    # 1. Inspect Length Varint (1 to 5 bytes)
    for len_bytes in range(1, 6):
        len_start = idx - len_bytes
        if len_start < 1:
            break
        # Last byte of Varint (idx-1) must have MSB == 0, preceding bytes must have MSB == 1
        if (modified_bytes[idx - 1] & 0x80) != 0:
            continue
        if any((modified_bytes[i] & 0x80) == 0 for i in range(len_start, idx - 1)):
            continue
            
        # Reconstruct length value in correct Little-Endian order
        length_val = 0
        for shift_idx, i in enumerate(range(len_start, idx)):
            length_val |= (modified_bytes[i] & 0x7F) << (shift_idx * 7)
            
        if length_val == target_len:
            # 2. Inspect Tag Varint (1 to 5 bytes)
            for tag_bytes in range(1, 6):
                tag_start = len_start - tag_bytes
                if tag_start < 0:
                    break
                if (modified_bytes[len_start - 1] & 0x80) != 0:
                    continue
                if any((modified_bytes[i] & 0x80) == 0 for i in range(tag_start, len_start - 1)):
                    continue
                
                # Check if bottom 3 bits of the Tag field indicate Wire Type 2 (0x02)
                if (modified_bytes[tag_start] & 0x07) == 2:
                    return True
    return False

def merge_events_to_mosaic(
    raw_events: List[Dict[str, Any]], 
    resolution_percentage: float
) -> List[Dict[str, Any]]:
    """
    [Fully Stated & Accelerated Version] Deterministic Mosaicing & Adjacent Rectangle Merging Algorithm

    An optimized deterministic post-processor designed for approximately linear time behavior on typical 
    trace workloads. Binning duration events into time buckets per (pid, tid) lane to reduce browser-side
    rendering and memory pressure.

    Parameters
    ----------
    raw_events : List[Dict[str, Any]]
        Array of raw event objects extracted from TensorBoard trace logs (Trace Events).
    resolution_percentage : float
        Target resolution percentage (0.0 < percentage <= 100.0).

    Returns
    -------
    List[Dict[str, Any]]
        Streamlined event array processed with spatial downsampling and adjacent tile merging.
    """
    # Immediate return if there are no events to process
    if not raw_events:
        return []

    # Filter valid duration events (ph == "X") with valid timestamps
    duration_events = [
        ev for ev in raw_events 
        if isinstance(ev, dict) and ev.get("ph") == "X" and "ts" in ev and "dur" in ev
    ]
    
    orig_dur_size = len(duration_events)
    if orig_dur_size == 0:
        return []

    # =====================================================================
    # Dynamic Heuristic Resolution Floor
    # Calculates an estimated resolution floor to reduce rendering pressure based on event density.
    # =====================================================================
    theoretical_min = (50.0 / max(1, orig_dur_size)) * 100.0
    calculated_limit = float(math.ceil(theoretical_min) + ReducerConfig.BASE_SAFETY_BUFFER)

    # Automatically snap to calculated heuristic floor if user-specified resolution falls below threshold
    if resolution_percentage <= 0.0 or resolution_percentage < calculated_limit:
        print(f"\n [WARNING] ── Specified resolution ({resolution_percentage:.3f}%) may result in extreme downsampling for this event density.")
        print(f" ├─ Raw Duration Event Count: {orig_dur_size:,}")
        print(f" ├─ Action : Automatically snapped upward to [Heuristic Resolution Floor].")
        print(f" └─ Applied Resolution Floor : {calculated_limit:.2f}%")
        resolution_percentage = calculated_limit

    # Fast bypass: Return duration events as-is if resolution is 100% or greater
    if resolution_percentage >= 100.0:
        return duration_events

    # Group events by (PID, TID) tuple to ensure multi-process lane isolation
    lane_groups = defaultdict(list)
    for ev in duration_events:
        pid = ev.get("pid", 0)
        tid = ev.get("tid", 0)
        lane_groups[(pid, tid)].append(ev)

    mosaic_events = []

    # Process mosaicing and aggregation per (pid, tid) lane
    for (pid, tid), events in lane_groups.items():
        # Sort events chronologically by timestamp
        events.sort(key=lambda x: x["ts"])
        
        start_ts = events[0]["ts"]
        end_ts = max(ev["ts"] + ev["dur"] for ev in events)
        total_dur = end_ts - start_ts
        
        # Skip sub-nanosecond or malformed transient events (Damper Guard)
        if total_dur <= 1e-6:
            continue

        # Dynamic calculation of time subdivision chunks based on resolution percentage
        if resolution_percentage <= 10.0:
            chunks_count = ReducerConfig.MIN_CHUNKS_LOW
        elif resolution_percentage <= 80.0:
            ratio = (resolution_percentage - 10.0) / (80.0 - 10.0)
            chunks_count = int(5 + ratio * (ReducerConfig.MAX_CHUNKS_MID - 5))
        else:
            # FIX: Corrected range denominator to (100.0 - 80.0) to prevent chunk undershooting
            ratio = (resolution_percentage - 80.0) / (100.0 - 80.0)
            chunks_count = int(ReducerConfig.MAX_CHUNKS_MID + ratio * (ReducerConfig.MAX_CHUNKS_HIGH - ReducerConfig.MAX_CHUNKS_MID))

        # Enforce minimum chunk guard and safely derive tile width (microseconds)
        chunks_count = max(1, chunks_count)
        tile_width = max(1e-6, total_dur / chunks_count)

        # Flat dictionary structure (idx, name) -> duration
        grid_durations = defaultdict(float)
        
        for ev in events:
            ev_start = ev["ts"]
            ev_end = ev_start + ev["dur"]
            
            # Map event span to grid index range via division
            idx_start = max(0, int((ev_start - start_ts) / tile_width))
            idx_end = min(chunks_count - 1, int((ev_end - start_ts) / tile_width))
            
            if idx_start > idx_end:
                continue
            
            # Accumulate overlap duration across affected grid buckets
            for idx in range(idx_start, idx_end + 1):
                grid_w_start = start_ts + idx * tile_width
                grid_w_end = grid_w_start + tile_width
                
                actual_start = max(ev_start, grid_w_start)
                actual_end = min(ev_end, grid_w_end)
                overlap = actual_end - actual_start
                if overlap > 0:
                    grid_durations[(idx, ev.get("name", "unknown"))] += overlap

        # Group durations by index to identify dominant event per bucket
        bucket_map = defaultdict(lambda: defaultdict(float))
        for (idx, name), overlap in grid_durations.items():
            bucket_map[idx][name] = overlap

        # Generate temporary tiles by selecting dominant event names per bucket
        temporary_tiles = []
        for idx in sorted(bucket_map.keys()):
            name_durs = bucket_map[idx]
            if name_durs:
                dominant_name = max(name_durs, key=name_durs.get)
                
                # Apply UTF-8 byte-length preserved masking (preserves byte length to reduce structural corruption risk)
                if not dominant_name.endswith("*"):
                    encoded_bytes = dominant_name.encode('utf-8')
                    orig_byte_len = len(encoded_bytes)
                    
                    if orig_byte_len <= 1:
                        dominant_name = "*"
                    else:
                        temp_name = dominant_name
                        while temp_name and len(temp_name.encode('utf-8')) >= orig_byte_len:
                            temp_name = temp_name[:-1]
                        
                        needed_padding = orig_byte_len - len(temp_name.encode('utf-8'))
                        dominant_name = temp_name + ("*" * needed_padding)
        
                t_ts = round(float(start_ts + idx * tile_width), ReducerConfig.DEFAULT_PRECISION_DECIMALS)
                t_dur = round(float(tile_width), ReducerConfig.DEFAULT_PRECISION_DECIMALS)

                temporary_tiles.append({
                    "ph": "X", "pid": pid, "tid": tid,
                    "ts": t_ts, "dur": t_dur, "name": dominant_name
                })

        # Consolidation of Adjacent Identical Tiles
        merged_lane = []
        temporary_tiles.sort(key=lambda x: x["ts"])

        for ev in temporary_tiles:
            if not merged_lane:
                merged_lane.append(ev)
            else:
                last_ev = merged_lane[-1]
                if last_ev["name"] == ev["name"] and abs((last_ev["ts"] + last_ev["dur"]) - ev["ts"]) < ReducerConfig.EPSILON_MERGE_THRESHOLD:
                    last_ev["dur"] = round(last_ev["dur"] + ev["dur"], ReducerConfig.DEFAULT_PRECISION_DECIMALS)
                else:
                    merged_lane.append(ev)

        mosaic_events.extend(merged_lane)

    return mosaic_events


def main() -> None:
    """
    CLI Entrypoint for TensorBoard Log Reducer.
    Handles recursive discovery, loading, structural transformation, saving of .trace.json.gz logs, 
    and safe string masking within .pb binary metadata.
    """
    parser = argparse.ArgumentParser(description="TensorBoard Trace Log Reducer & Binary Masker")
    parser.add_argument(
        "--logdir", 
        type=str, 
        required=True, 
        help="Explicit path to target TensorBoard log directory (User-directed in-place processing)"
    )
    parser.add_argument("--resolution", type=float, default=50.0, help="Target resolution percentage (Default: 50.0)")
    args = parser.parse_args()

    target_pattern = os.path.join(args.logdir, "plugins", "profile", "*", "*.trace.json.gz")
    trace_files = glob.glob(target_pattern)
    
    if not trace_files:
        print(f" [ERROR] No trace.json.gz found in {target_pattern}")
        sys.exit(1)

    for trace_path in trace_files:
        print(f"\n [INFO] Target Trace: {trace_path}")
        
        file_size_mb = os.path.getsize(trace_path) / (1024.0 * 1024.0)
        if file_size_mb > ReducerConfig.LARGE_TRACE_THRESHOLD_MB:
            print(f" [NOTICE] Heavy trace file detected ({file_size_mb:.1f} MB compressed). Processing buffer allocated.")

        try:
            with gzip.open(trace_path, "rt") as f:
                data = json.load(f)
        except Exception as e:
            print(f" [ERROR] Failed to read or parse trace file {trace_path}: {e}")
            sys.exit(1)

        is_dict_root = isinstance(data, dict)
        if is_dict_root:
            orig_events = data.get("traceEvents", [])
        elif isinstance(data, list):
            orig_events = data
        else:
            orig_events = []

        orig_total_size = len(orig_events)
        
        # Categorize events strictly for accurate metrics calculation
        orig_dur_events = [ev for ev in orig_events if isinstance(ev, dict) and ev.get("ph") == "X" and "ts" in ev and "dur" in ev]
        orig_meta_events = [ev for ev in orig_events if isinstance(ev, dict) and ev.get("ph") != "X"]
        
        orig_dur_size = len(orig_dur_events)
        
        print(f" ├─ Original Total Events   : {orig_total_size:,}")
        print(f" ├─ Original Duration Events: {orig_dur_size:,}")

        # Perform mosaicing reduction on duration events
        shrunk_dur_events = merge_events_to_mosaic(orig_events, args.resolution)
        shrunk_dur_size = len(shrunk_dur_events)
        
        # Combine metadata events with reduced duration events
        updated_events = orig_meta_events + shrunk_dur_events
        final_total_size = len(updated_events)

        if is_dict_root:
            data["traceEvents"] = updated_events
        else:
            data = updated_events
        
        # Calculate Duration Event Reduction Ratio safely
        safe_orig_dur_size = max(1, orig_dur_size)
        dur_reduction_ratio = (1.0 - (shrunk_dur_size / safe_orig_dur_size)) * 100.0

        # Atomic Write Pattern for JSON.GZ
        tmp_trace_path = f"{trace_path}.tmp"
        try:
            with gzip.open(tmp_trace_path, "wt") as f:
                json.dump(data, f, separators=(',', ':'))
            os.replace(tmp_trace_path, trace_path)
        except Exception as e:
            print(f" [ERROR] Failed to write processed trace file {tmp_trace_path}: {e}")
            sys.exit(1)
        
        print(f" ├─ Reduced Duration Events : {shrunk_dur_size:,}")
        print(f" ├─ Final Output Events     : {final_total_size:,}")
        print(f" └─ Duration Event Reduction: {dur_reduction_ratio:.2f}%")

        # Protobuf Wire Type 2 Safe Masking Engine
        dir_path = os.path.dirname(trace_path)
        pb_files = glob.glob(os.path.join(dir_path, "*.pb"))
        
        distinct_raw_names = set(ev["name"] for ev in orig_events if isinstance(ev, dict) and "name" in ev)
        sorted_distinct_names = sorted(distinct_raw_names, key=len, reverse=True)

        for pb_target in pb_files:
            try:
                with open(pb_target, "rb") as f:
                    modified_bytes = bytearray(f.read())

                for base_name in sorted_distinct_names:
                    if not base_name or len(base_name) < 5 or not re.match(r'^[A-Za-z0-9_/\-:.\(\)@]+$', base_name):
                        continue
                    
                    target = base_name.encode('utf-8', errors='ignore')
                    target_len = len(target)
                    replacement = target[:-1] + b'*'
                    
                    idx = 0
                    while True:
                        idx = modified_bytes.find(target, idx)
                        if idx == -1:
                            break
                        
                        if verify_protobuf_wire2_boundary(modified_bytes, idx, target_len):
                            modified_bytes[idx : idx + target_len] = replacement
                            idx += target_len
                        else:
                            idx += 1

                # Atomic File Replace (reduce the risk of leaving a partially written PB file)
                tmp_pb_target = f"{pb_target}.tmp"
                with open(tmp_pb_target, "wb") as f:
                    f.write(modified_bytes)
                os.replace(tmp_pb_target, pb_target)

                print(f" ├─ [MASKED] Processed binary metadata (Wire Type 2 Verified): {os.path.basename(pb_target)}")

            except (IOError, OSError) as e:
                print(f" [ERROR] Critical I/O failure during PB masking on {os.path.basename(pb_target)}: {e}")
                sys.exit(1)
            except Exception as e:
                print(f" [ERROR] Unexpected failure during PB masking on {os.path.basename(pb_target)}: {e}")
                sys.exit(1)

        del data
        del orig_events
        gc.collect()

        print(" ├─ [INFO] Tile consolidation completed.")
        
        simulated_min_reduction = min(95.0, max(5.0, (shrunk_dur_size / safe_orig_dur_size) * ReducerConfig.SIMULATION_SCALE_FACTOR * ReducerConfig.SAFETY_MARGIN_RATIO))
        simulated_max_resolution = 100.0 - simulated_min_reduction

        print("\n [SUMMARY] Profile Reduction Metrics")
        print(f" ├─ Current Resolution Configured: {args.resolution:.2f}%")
        print(f" ├─ Duration Event Reduction     : {dur_reduction_ratio:.2f}%")
        print(f" └─ Estimated Resolution Floor   : {simulated_max_resolution:.2f}%")

if __name__ == "__main__":
    main()

# Don't be evil, ¯\_(ツ  )_/¯ but ¯\_(  ツ)_/¯ don't be serious...!
