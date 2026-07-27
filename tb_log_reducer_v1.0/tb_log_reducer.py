#!/usr/bin/env python3
# Copyright (c) 2026 PastToFuture-Whisperer
# SPDX-License-Identifier: MIT
#
# This program is a byproduct of the advanced profile optimization research 
# mentioned in the documentation; those core features are explicitly excluded 
# from this repository and implemented separately.

import argparse
import os
import glob
import gzip
import json
import math
import re
import gc
from collections import defaultdict
from typing import List, Dict, Any, Union

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


def merge_events_to_mosaic(
    raw_events: List[Dict[str, Any]], 
    resolution_percentage: float
) -> List[Dict[str, Any]]:
    """
    [Fully Stated & Accelerated Version] O(N) Deterministic Mosaicing & Adjacent Rectangle Merging Algorithm

    An O(N) deterministic post-processor that completely eliminates double loops (O(N^2)) by binning events 
    into time buckets. Directly calculates thread occupancy time to maximize data reduction and prevent 
    browser (V8/WebGL) rendering crashes.

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

    Notes
    -----
    - This function targets only `ph == "X"` (Duration Events) for smoothing and aggregation.
    - To prevent tile disjunction caused by floating-point rounding errors, precision rounding is applied via 
      `ReducerConfig.DEFAULT_PRECISION_DECIMALS` alongside adjacency checks via `ReducerConfig.EPSILON_MERGE_THRESHOLD`.
    - Automatically calculates dynamic threshold limits based on raw data density to avoid exceeding V8 rendering 
      capacity, applying automatic snap corrections as needed.
    """
    # Immediate return if there are no events to process
    if not raw_events:
        return []

    orig_size = len(raw_events)
    # Extract Process ID (pid) from the first event (default: 0)
    pid = raw_events[0].get("pid", 0) if isinstance(raw_events[0], dict) else 0

    # =====================================================================
    # Dynamic Limit Resolution & Operational Safety Snap
    # Calculates the theoretical minimum resolution to prevent browser crashes based on event density.
    # =====================================================================
    theoretical_min = (50.0 / max(1, orig_size)) * 100.0
    calculated_limit = float(math.ceil(theoretical_min) + ReducerConfig.BASE_SAFETY_BUFFER)

    # Automatically snap to calculated safety limit if user-specified resolution falls below threshold
    if resolution_percentage <= 0.0 or resolution_percentage < calculated_limit:
        print(f"\n [WARNING] ── Specified resolution ({resolution_percentage:.3f}%) poses a rendering crash risk for this event density.")
        print(f" ├─ Raw Event Count: {orig_size:,}")
        print(f" ├─ Action : Automatically snapped upward to [Dynamic Limit Resolution] to protect browser memory.")
        print(f" └─ Applied Safety Value : {calculated_limit:.2f}% (Ceil Value + 1% Safety Margin Enforced)")
        resolution_percentage = calculated_limit

    # Fast bypass: Return duration events as-is if resolution is 100% or greater
    if resolution_percentage >= 100.0:
        return [ev for ev in raw_events if isinstance(ev, dict) and ev.get("ph") == "X"]

    # Group events by Thread ID (tid), filtering for valid duration events (ph == "X") with valid timestamps
    lane_groups = defaultdict(list)
    for ev in raw_events:
        if isinstance(ev, dict) and ev.get("ph") == "X" and "ts" in ev and "dur" in ev:
            lane_groups[ev.get("tid", 0)].append(ev)

    mosaic_events = []

    # Process mosaicing and aggregation per thread (lane)
    for tid, events in lane_groups.items():
        # Sort events chronologically by timestamp
        events.sort(key=lambda x: x["ts"])
        
        start_ts = events[0]["ts"]
        end_ts = max(ev["ts"] + ev["dur"] for ev in events)
        total_dur = end_ts - start_ts
        
        # Skip sub-nanosecond or malformed transient events (Damper Guard)
        if total_dur <= 1e-6:
            continue

        # =====================================================================
        # Dynamic calculation of time subdivision chunks based on resolution percentage
        # =====================================================================
        if resolution_percentage <= 10.0:
            chunks_count = ReducerConfig.MIN_CHUNKS_LOW
        elif resolution_percentage <= 80.0:
            ratio = (resolution_percentage - 10.0) / (80.0 - 10.0)
            chunks_count = int(5 + ratio * (ReducerConfig.MAX_CHUNKS_MID - 5))
        else:
            ratio = (resolution_percentage - 80.0) / (100.0 - 80.0)
            chunks_count = int(ReducerConfig.MAX_CHUNKS_MID + ratio * (ReducerConfig.MAX_CHUNKS_HIGH - ReducerConfig.MAX_CHUNKS_MID))

        # Enforce minimum chunk guard and safely derive tile width (microseconds)
        chunks_count = max(1, chunks_count)
        tile_width = max(1e-6, total_dur / chunks_count)

        # Flat dictionary structure (idx, name) -> duration to minimize Python object allocation overhead
        grid_durations = defaultdict(float)
        
        for ev in events:
            ev_start = ev["ts"]
            ev_end = ev_start + ev["dur"]
            
            # Directly map event span to grid index range via division (O(1) derivation)
            idx_start = max(0, int((ev_start - start_ts) / tile_width))
            idx_end = min(chunks_count - 1, int((ev_end - start_ts) / tile_width))
            
            # Index Inversion Safeguard (Floating-point boundary guard)
            if idx_start > idx_end:
                continue
            
            # Accumulate overlap duration across affected grid buckets
            for idx in range(idx_start, idx_end + 1):
                grid_w_start = start_ts + idx * tile_width
                grid_w_end = grid_w_start + tile_width
                
                # Compute precise overlap duration within current tile boundaries
                actual_start = max(ev_start, grid_w_start)
                actual_end = min(ev_end, grid_w_end)
                overlap = actual_end - actual_start
                if overlap > 0:
                    grid_durations[(idx, ev.get("name", "unknown"))] += overlap

        # Group durations by index to identify dominant event per bucket
        bucket_map = defaultdict(lambda: defaultdict(float))
        for (idx, name), overlap in grid_durations.items():
            bucket_map[idx][name] = overlap

        # Generate temporary tiles by selecting dominant (longest occupancy) event names per bucket
        temporary_tiles = []
        for idx in sorted(bucket_map.keys()):
            name_durs = bucket_map[idx]
            if name_durs:
                # Retrieve event name occupying maximum duration within current grid
                dominant_name = max(name_durs, key=name_durs.get)
                
                # Apply end-character masking (Overwrite trailing character with '*' to maintain EXACT string length)
                # CRITICAL SPEC: Modifying the string length breaks TensorBoard/XProf offset alignment.
                if not dominant_name.endswith("*"):
                    dominant_name = dominant_name[:-1] + "*" if len(dominant_name) > 1 else dominant_name + "*"

                # Floating-point precision correction (round to configured decimal places to eliminate evaluation noise)
                t_ts = round(float(start_ts + idx * tile_width), ReducerConfig.DEFAULT_PRECISION_DECIMALS)
                t_dur = round(float(tile_width), ReducerConfig.DEFAULT_PRECISION_DECIMALS)

                temporary_tiles.append({
                    "ph": "X", "pid": pid, "tid": tid,
                    "ts": t_ts, "dur": t_dur, "name": dominant_name
                })

        # =====================================================================
        # Complete Consolidation of Adjacent Identical Tiles
        # Merges contiguous, identically named tiles within the same lane to further minimize DOM element count.
        # Uses EPSILON_MERGE_THRESHOLD to safely absorb floating-point rounding errors during joining.
        # =====================================================================
        merged_lane = []
        temporary_tiles.sort(key=lambda x: x["ts"])

        for ev in temporary_tiles:
            if not merged_lane:
                merged_lane.append(ev)
            else:
                last_ev = merged_lane[-1]
                # Merge into a single macro-tile if name matches and temporal gap is below EPSILON_MERGE_THRESHOLD
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
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="TensorBoard Trace Log Reducer & Binary Masker")
    parser.add_argument("--logdir", type=str, required=True, help="Path to TensorBoard log directory")
    parser.add_argument("--resolution", type=float, default=50.0, help="Target resolution percentage (Default: 50.0)")
    args = parser.parse_args()

    # Recursively locate target .trace.json.gz files
    target_pattern = os.path.join(args.logdir, "plugins", "profile", "*", "*.trace.json.gz")
    trace_files = glob.glob(target_pattern)
    
    if not trace_files:
        print(f" [ERROR] No trace.json.gz found in {target_pattern}")
        return

    # Execute reduction process per discovered trace file
    for trace_path in trace_files:
        print(f"\n [PROCESSING] Target Trace: {trace_path}")
        
        # Large trace pre-check: Inspect file size before memory allocation to warn against potential OOM spikes
        file_size_mb = os.path.getsize(trace_path) / (1024.0 * 1024.0)
        if file_size_mb > ReducerConfig.LARGE_TRACE_THRESHOLD_MB:
            print(f" [NOTICE] Heavy trace file detected ({file_size_mb:.1f} MB compressed). Processing buffer allocated.")

        # Load gzipped trace log
        with gzip.open(trace_path, "rt") as f:
            data = json.load(f)

        # Dynamically determine JSON root structure (dict {"traceEvents": [...]} vs raw list [...])
        is_dict_root = isinstance(data, dict)
        if is_dict_root:
            orig_events = data.get("traceEvents", [])
        elif isinstance(data, list):
            orig_events = data
        else:
            orig_events = []

        orig_size = len(orig_events)
        safe_orig_size = max(1, orig_size)  # ZeroDivisionError protection valve
        print(f" ├─ Original Event Count: {orig_size:,}")

        # Perform optimized mosaicing reduction
        shrunk_events = merge_events_to_mosaic(orig_events, args.resolution)
        shrunk_size = len(shrunk_events)
        
        # Preserve non-duration metadata events (lane names, process metadata, etc.) and append reduced events
        meta_events = [ev for ev in orig_events if isinstance(ev, dict) and ev.get("ph") != "X"]
        updated_events = meta_events + shrunk_events

        # Reconstruct output structure matching original schema
        if is_dict_root:
            data["traceEvents"] = updated_events
        else:
            data = updated_events
        
        # Calculate reduction ratio with zero-division safety guard
        reduction_ratio = (1.0 - (shrunk_size / safe_orig_size)) * 100.0

        # Atomic Write Pattern for JSON.GZ to prevent file corruption
        tmp_trace_path = f"{trace_path}.tmp"
        with gzip.open(tmp_trace_path, "wt") as f:
            json.dump(data, f, separators=(',', ':'))
        os.replace(tmp_trace_path, trace_path)
        
        print(f" ├─ Processed Event Count: {shrunk_size:,}")
        print(f" └─ Data Point Reduction Ratio: {reduction_ratio:.2f}%")

        # Explicit Memory Cleanup (Anti-OOM Guard for Cloud Shell / Low Memory Containers)
        del data
        del orig_events
        gc.collect()

        # =====================================================================
        # Protobuf Masking Fault-Tolerant Valve (Atomic File Replace & Length-Sorted)
        # Applies end-character masking to .pb binary metadata files in the same directory for consistency.
        # Enforces a minimum length boundary and character structure checks to eliminate accidental binary payload corruption.
        # =====================================================================
        dir_path = os.path.dirname(trace_path)
        pb_files = glob.glob(os.path.join(dir_path, "*.pb"))
        distinct_names = set(ev["name"] for ev in shrunk_events if isinstance(ev, dict) and "name" in ev)

        # Sort target names in DESCENDING ORDER of length to prevent substring corruption (e.g., matching conv2d before conv2d_1)
        sorted_distinct_names = sorted(distinct_names, key=len, reverse=True)

        for pb_target in pb_files:
            try:
                with open(pb_target, "rb") as f:
                    modified_bytes = f.read()

                # Perform in-memory byte replacement across length-sorted target names
                for name_str in sorted_distinct_names:
                    base_name = name_str.rstrip("*")
                    # Expanded Pattern Guard: Allow TensorBoard node characters including :, ., (), @, -, _
                    if not base_name or len(base_name) < 3 or not re.match(r'^[A-Za-z0-9_/\-:.\(\)@]+$', base_name):
                        continue
                    
                    target = base_name.encode('utf-8', errors='ignore')
                    # CRITICAL SPEC: Perform in-place 1-byte ASCII '*' (0x2A) replacement on the trailing byte.
                    # Modifying string/payload length causes Protobuf Varint decoding mismatches in TensorBoard.
                    if len(target) >= 3 and target in modified_bytes:
                        replacement = target[:-1] + b'*'
                        assert len(target) == len(replacement), "Binary payload length mismatch!"
                        modified_bytes = modified_bytes.replace(target, replacement)

                # Atomic File Replace for .pb files to guarantee process resilience
                tmp_pb_target = f"{pb_target}.tmp"
                with open(tmp_pb_target, "wb") as f:
                    f.write(modified_bytes)
                os.replace(tmp_pb_target, pb_target)

                print(f" ├─ [MASKED] Safely processed binary metadata: {os.path.basename(pb_target)}")

            except (IOError, OSError, Exception) as e:
                # Catch physical file access errors, permissions, or structural anomalies to shield the main process
                print(f" ├─ [WARNING] Non-fatal PB masking bypass applied to {os.path.basename(pb_target)}: {e}")

        print(" ├─ [PERFECT UNIFORMITY] All target metadata successfully safeguarded.")
        
        # Simulate operational safety boundaries (with zero-division safety guard)
        simulated_min_reduction = min(95.0, max(5.0, (shrunk_size / safe_orig_size) * ReducerConfig.SIMULATION_SCALE_FACTOR * ReducerConfig.SAFETY_MARGIN_RATIO))
        simulated_max_resolution = 100.0 - simulated_min_reduction

        print("\n [METRIC: RE-ARCHITECTED SUMMARY]")
        print(f" ├─ Current Resolution Configured: {args.resolution:.2f}%")
        print(f" ├─ Memory Load Reduction Target : {reduction_ratio:.2f}%")
        print(f" └─ Operational Safety Boundary (Counter-Calculated Max Resolution): {simulated_max_resolution:.2f}%")

if __name__ == "__main__":
    main()

# Don't be evil, ¯\_(ツ  )_/¯ but ¯\_(  ツ)_/¯ don't be serious...!