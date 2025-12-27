"""
Cleanup utility for shared memory segments from test runs.

Run this if tests fail to clean up properly and you get "File exists" errors.
"""
from multiprocessing import shared_memory
import sys

# Known test shared memory segment names
TEST_SHM_NAMES = [
    "test_interop_multi_prod",
    "test_mpmc_mpsc",
    "test_mpmc_stress",
    "test_mpmc_wrap",
    "slick_queue_py_test_shm",
    "test_atomic_cursor_cpp",
    "test_atomic_cursor_cpp_queue",
    "test_cpp_shm_creation",
]

def cleanup_shm():
    """Attempt to unlink all test shared memory segments."""
    cleaned = []
    failed = []

    for name in TEST_SHM_NAMES:
        try:
            shm = shared_memory.SharedMemory(name=name)
            try:
                # Try to close, but don't let BufferError stop us from unlinking
                shm.close()
            except BufferError:
                # Ignore BufferError during close - we still want to unlink
                pass
            shm.unlink()
            cleaned.append(name)
            print(f"Cleaned up: {name}")
        except FileNotFoundError:
            # Already cleaned up
            pass
        except Exception as e:
            failed.append((name, str(e)))
            print(f"Failed to clean {name}: {e}")

    if cleaned:
        print(f"\nSuccessfully cleaned {len(cleaned)} segment(s)")
    if failed:
        print(f"\nFailed to clean {len(failed)} segment(s)")

    return len(failed) == 0

if __name__ == '__main__':
    success = cleanup_shm()
    sys.exit(0 if success else 1)
