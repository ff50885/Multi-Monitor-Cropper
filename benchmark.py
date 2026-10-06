import os
import time
import tempfile
from pathlib import Path

def setup_files(directory, num_files, panels):
    for i in range(panels):
        Path(os.path.join(directory, f"panel_{i}.png")).touch()
        for j in range(1, num_files):
            Path(os.path.join(directory, f"panel_{i}_{j}.png")).touch()

def benchmark_original(directory, panels):
    start = time.time()
    for idx in range(panels):
        base_name = f"panel_{idx}"
        save_path = os.path.join(directory, f"{base_name}.png")
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(directory, f"{base_name}_{counter}.png")
            counter += 1
        # Instead of saving, just touching it to simulate creating
        Path(save_path).touch()
    return time.time() - start

def benchmark_optimized(directory, panels):
    start = time.time()
    existing_files = set(os.listdir(directory))
    for idx in range(panels):
        base_name = f"panel_{idx}"
        file_name = f"{base_name}.png"
        counter = 1
        while file_name in existing_files:
            file_name = f"{base_name}_{counter}.png"
            counter += 1

        save_path = os.path.join(directory, file_name)
        existing_files.add(file_name)
        Path(save_path).touch()
    return time.time() - start

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp_dir:
        setup_files(tmp_dir, 1000, 3)
        orig_time = benchmark_original(tmp_dir, 3)
        print(f"Original logic: {orig_time:.5f} seconds")

    with tempfile.TemporaryDirectory() as tmp_dir:
        setup_files(tmp_dir, 1000, 3)
        opt_time = benchmark_optimized(tmp_dir, 3)
        print(f"Optimized logic: {opt_time:.5f} seconds")
