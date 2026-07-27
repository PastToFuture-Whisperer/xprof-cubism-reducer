import argparse
import sys
import os
import jax
import jax.numpy as jnp
import numpy as np
import time

def main():
    # -----------------------------------------------------------------
    # [UNIVERSAL ARGUMENT DESIGN]
    # Fully compatible with both standalone execution (e.g., python3 sample.py 500) 
    # and execution wrapper via run.sh
    # -----------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Hugging Face Demo: XProf Data Explosion Simulator"
    )
    
    # Positional argument accepting plain numeric inputs without flags (default: 100 loops)
    parser.add_argument(
        "iterations", 
        type=int, 
        nargs="?", 
        default=100, 
        help="Number of workload iterations (e.g., 500)"
    )
    
    # Log directory path. Automatically defaults to current ./logdir if omitted (fully in sync with run.sh default)
    parser.add_argument(
        "--logdir", 
        type=str, 
        default="./logdir", 
        help="Target container directory for XProf native traces"
    )
    
    args = parser.parse_args()
    iterations = args.iterations
    logdir = args.logdir

    print("==================================================")
    print(" JAX Spatial Workload Layer: Native Trace Engine")
    print("==================================================")
    print(f"[*] JAX Devices          : {jax.devices()}")
    print(f"[*] Target XProf Logdir  : {logdir}")
    print(f"[*] Workload Iterations  : {iterations} loops")
    print("[*] Status               : Simulating AI memory data explosion...")
    print("--------------------------------------------------")

    # Launch native JAX profiler (TensorFlow XProf compatible trace format)
    jax.profiler.start_trace(logdir)

    start_time = time.time()

    try:
        for i in range(iterations):
            # Physically simulate data explosion typical of modern AI workloads 
            # (e.g., LLM dynamic shapes and mixed-precision operations).
            # Intentionally modulate array shapes per step to induce high memory allocation and metadata density.
            dynamic_size = int(np.random.randint(200, 400))
            
            # Matrix operation to engrave massive trace event streams onto the XProf timeline
            x = jnp.ones((dynamic_size, dynamic_size))
            y = jnp.zeros((dynamic_size, dynamic_size))
            result = jnp.dot(x, y).block_until_ready()

            # Progress display (outputs every 20% interval or at the final step)
            if (i + 1) % (max(1, iterations // 5)) == 0 or (i + 1) == iterations:
                print(f"  [+] Progress: {i + 1}/{iterations} loops safely executed.")

    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user. Stopping trace gracefully...")
    
    finally:
        # Safely terminate trace session and flush artifacts to native OS container (.trace.json.gz)
        jax.profiler.stop_trace()

    end_time = time.time()
    print("--------------------------------------------------")
    print(f"[*] Simulation Finished successfully in {end_time - start_time:.2f} seconds.")
    print(f"[*] Native XProf artifacts successfully generated inside: {logdir}")
    print("==================================================")

if __name__ == "__main__":
    main()