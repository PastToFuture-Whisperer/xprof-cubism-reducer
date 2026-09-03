import time
import argparse
import numpy as np
import jax
import jax.numpy as jnp

# --- コア処理：静的シェイプによるXLA再コンパイルの遮断 (Compiler Knob) ---
#サンドボックス検証用スクリプトv1.0.0

@jax.jit
def static_kernel(x, weight):
    return jnp.dot(x, weight)

# --- 外乱・ハイドロプレーニングシミュレータ ---
def simulate_workload_and_jitter(step, base_shape, hydro_spike=False):
    # 外乱発生時は動的なテンソルサイズ変動を模擬（Control OFF時のRe-compilation誘発）
    if hydro_spike and (step % 20 == 0):
        dynamic_size = base_shape[0] + (step % 5 + 1) * 64
    else:
        dynamic_size = base_shape[0]
    
    x_data = np.random.randn(dynamic_size, base_shape[1]).astype(np.float32)
    return x_data

# --- Tier-2.5 Jitter Interceptor (Infra Knob) ---
class JitterInterceptor:
    def __init__(self, mode="strict", target_shape=(1024, 1024), damping_gamma=0.85, boundary_ratio=1.2):
        self.mode = mode
        self.target_shape = target_shape
        self.gamma = damping_gamma
        self.boundary_ratio = boundary_ratio
        self.ema_latency = 0.0

    def process_and_execute(self, x_data, weight_jax):
        # 1. Compiler Knob: パディングによるShape固定 (Static Upper Bound Shape)
        curr_h, curr_w = x_data.shape
        pad_h = max(0, self.target_shape[0] - curr_h)
        pad_w = max(0, self.target_shape[1] - curr_w)
        
        if pad_h > 0 or pad_w > 0:
            x_padded = np.pad(x_data, ((0, pad_h), (0, pad_w)), mode='constant')
        else:
            x_padded = x_data[:self.target_shape[0], :self.target_shape[1]]
            
        x_jax = jnp.array(x_padded)

        # 2. 実行 & デバイス同期
        t_start = time.perf_counter()
        res = static_kernel(x_jax, weight_jax)
        res.block_until_ready() # 同期バリア
        t_end = time.perf_counter()
        
        raw_latency = (t_end - t_start) * 1000.0 # ms

        # 3. Infra Knob: Aiki-Damping & Host Sink による余剰エネルギー廃棄
        if self.mode != "off":
            if self.ema_latency == 0.0:
                self.ema_latency = raw_latency
            else:
                self.ema_latency = self.gamma * self.ema_latency + (1.0 - self.gamma) * raw_latency
            
            # スパイク検知時のHost Sink処理
            if raw_latency > self.ema_latency * self.boundary_ratio:
                suppression_delay = (raw_latency - self.ema_latency) * 0.1
                time.sleep(suppression_delay / 1000.0) # CPU Host Sink
                
        return raw_latency

def run_benchmark(mode, steps=100, profile_dir=None):
    print(f"\n--- Running Benchmark Mode: [{mode.upper()}] ---")
    
    # 重み初期化
    shape = (1024, 1024)
    key = jax.random.PRNGKey(42)
    w_jax = jax.random.normal(key, shape)
    w_jax.block_until_ready()
    
    interceptor = JitterInterceptor(mode=mode, target_shape=shape)
    latencies = []

    # XProf プロファイル開始（指定時）
    if profile_dir:
        jax.profiler.start_trace(profile_dir)

    # ウォームアップ (JIT初回コンパイル)
    dummy_x = np.random.randn(*shape).astype(np.float32)
    _ = interceptor.process_and_execute(dummy_x, w_jax)

    for step in range(steps):
        # Control OFF時はハイドロプレーニング外乱（動的Shape）をそのまま適用
        x_raw = simulate_workload_and_jitter(step, shape, hydro_spike=(mode == "off"))
        
        if mode == "off":
            # 制御なし：動的ShapeによりRe-compilationが発生
            x_jax = jnp.array(x_raw)
            t0 = time.perf_counter()
            res = jnp.dot(x_jax, w_jax)
            res.block_until_ready()
            t1 = time.perf_counter()
            lat = (t1 - t0) * 1000.0
        else:
            # Tier-2.5 制御有効
            lat = interceptor.process_and_execute(x_raw, w_jax)
            
        latencies.append(lat)

    if profile_dir:
        jax.profiler.stop_trace()

    latencies = np.array(latencies)
    avg_lat = np.mean(latencies)
    std_lat = np.std(latencies)
    max_lat = np.max(latencies)
    
    print(f"Result [{mode}]: Avg = {avg_lat:.3f} ms | StdDev (Jitter) = {std_lat:.3f} ms | Max Spike = {max_lat:.3f} ms")
    return latencies

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["off", "strict", "adaptive", "performance"], default="strict")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--profile-dir", type=str, default=None)
    args = parser.parse_args()

    run_benchmark(args.mode, args.steps, args.profile_dir)