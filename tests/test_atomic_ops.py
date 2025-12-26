"""
Unit tests for atomic operations.

Tests platform-specific atomic CAS operations and memory ordering semantics.
"""
import struct
import sys
import multiprocessing
from multiprocessing import shared_memory, Process, Value
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from atomic_ops import (
    AtomicReservedInfo,
    AtomicUInt64,
    check_platform_support
)


def test_platform_support():
    """Test that platform detection works."""
    supported, msg = check_platform_support()
    print(f"Platform support: {supported}")
    print(f"Message: {msg}")
    assert isinstance(supported, bool)
    assert isinstance(msg, str)
    # We're running the tests, so platform should be supported
    assert supported, f"Platform not supported: {msg}"


def test_atomic_reserved_info_load():
    """Test loading reserved_info."""
    from atomic_ops import make_reserved_info

    # Create shared memory with known values
    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Write test values: index=100, size=5
        packed = make_reserved_info(100, 5)
        struct.pack_into(AtomicReservedInfo.RESERVED_INFO_FMT, shm.buf, 0, packed)

        # Load using atomic wrapper
        atomic = AtomicReservedInfo(shm.buf, 0)
        index, size = atomic.load()

        assert index == 100, f"Expected index=100, got {index}"
        assert size == 5, f"Expected size=5, got {size}"
    finally:
        shm.close()
        shm.unlink()


def test_atomic_reserved_info_cas_success():
    """Test successful CAS on reserved_info."""
    from atomic_ops import make_reserved_info

    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Initial value: index=100, size=5
        packed = make_reserved_info(100, 5)
        struct.pack_into(AtomicReservedInfo.RESERVED_INFO_FMT, shm.buf, 0, packed)

        atomic = AtomicReservedInfo(shm.buf, 0)

        # CAS should succeed
        success, actual = atomic.compare_exchange_weak(
            expected=(100, 5),
            desired=(150, 10)
        )

        assert success, "CAS should succeed"
        assert actual == (100, 5), f"Actual should be old value on success, got {actual}"

        # Verify new value was written
        index, size = atomic.load()
        assert index == 150, f"Expected index=150, got {index}"
        assert size == 10, f"Expected size=10, got {size}"
    finally:
        shm.close()
        shm.unlink()


def test_atomic_reserved_info_cas_failure():
    """Test failed CAS on reserved_info."""
    from atomic_ops import make_reserved_info

    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Initial value: index=100, size=5
        packed = make_reserved_info(100, 5)
        struct.pack_into(AtomicReservedInfo.RESERVED_INFO_FMT, shm.buf, 0, packed)

        atomic = AtomicReservedInfo(shm.buf, 0)

        # CAS should fail (wrong expected value)
        success, actual = atomic.compare_exchange_weak(
            expected=(200, 10),  # Wrong!
            desired=(150, 10)
        )

        assert not success, "CAS should fail"
        assert actual == (100, 5), f"Actual should be current value on failure, got {actual}"

        # Verify value was NOT changed
        index, size = atomic.load()
        assert index == 100, f"Expected index=100 (unchanged), got {index}"
        assert size == 5, f"Expected size=5 (unchanged), got {size}"
    finally:
        shm.close()
        shm.unlink()


def test_atomic_uint64_load_store():
    """Test atomic uint64 load and store."""
    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Write test value
        struct.pack_into("<Q", shm.buf, 0, 0xDEADBEEFCAFEBABE)

        atomic = AtomicUInt64(shm.buf, 0)

        # Load
        value = atomic.load_acquire()
        assert value == 0xDEADBEEFCAFEBABE, f"Expected 0xDEADBEEFCAFEBABE, got {hex(value)}"

        # Store
        atomic.store_release(0x123456789ABCDEF0)

        # Verify
        value = atomic.load_acquire()
        assert value == 0x123456789ABCDEF0, f"Expected 0x123456789ABCDEF0, got {hex(value)}"
    finally:
        shm.close()
        shm.unlink()


def test_atomic_uint64_cas_success():
    """Test successful CAS on uint64."""
    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Initial value
        struct.pack_into("<Q", shm.buf, 0, 42)

        atomic = AtomicUInt64(shm.buf, 0)

        # CAS should succeed
        success, actual = atomic.compare_exchange_weak(expected=42, desired=100)

        assert success, "CAS should succeed"
        assert actual == 42, f"Actual should be old value, got {actual}"

        # Verify new value
        value = atomic.load_acquire()
        assert value == 100, f"Expected 100, got {value}"
    finally:
        shm.close()
        shm.unlink()


def test_atomic_uint64_cas_failure():
    """Test failed CAS on uint64."""
    shm = shared_memory.SharedMemory(create=True, size=64)
    try:
        # Initial value
        struct.pack_into("<Q", shm.buf, 0, 42)

        atomic = AtomicUInt64(shm.buf, 0)

        # CAS should fail (wrong expected)
        success, actual = atomic.compare_exchange_weak(expected=100, desired=200)

        assert not success, "CAS should fail"
        assert actual == 42, f"Actual should be current value, got {actual}"

        # Verify value unchanged
        value = atomic.load_acquire()
        assert value == 42, f"Expected 42 (unchanged), got {value}"
    finally:
        shm.close()
        shm.unlink()


def _concurrent_cas_worker(shm_name, counter, iterations):
    """Worker process for concurrent CAS test."""
    shm = shared_memory.SharedMemory(name=shm_name)
    try:
        atomic = AtomicUInt64(shm.buf, 0)

        for _ in range(iterations):
            while True:
                # Read current value
                current = atomic.load_acquire()
                # Try to increment
                success, _ = atomic.compare_exchange_weak(current, current + 1)
                if success:
                    with counter.get_lock():
                        counter.value += 1
                    break
                # Retry if CAS failed
    finally:
        shm.close()


def test_concurrent_cas():
    """Test concurrent CAS from multiple processes."""
    shm = shared_memory.SharedMemory(create=True, size=64, name='test_concurrent_cas')
    try:
        # Initial value: 0
        struct.pack_into("<Q", shm.buf, 0, 0)

        # Spawn multiple processes to increment concurrently
        num_processes = 4
        iterations_per_process = 100
        counter = Value('i', 0)
        processes = []

        for _ in range(num_processes):
            p = Process(target=_concurrent_cas_worker,
                       args=(shm.name, counter, iterations_per_process))
            p.start()
            processes.append(p)

        # Wait for all processes
        for p in processes:
            p.join()

        # Verify final value
        atomic = AtomicUInt64(shm.buf, 0)
        final_value = atomic.load_acquire()
        expected = num_processes * iterations_per_process

        print(f"Concurrent CAS test: expected={expected}, actual={final_value}, counter={counter.value}")
        assert final_value == expected, f"Expected {expected}, got {final_value}"
        assert counter.value == expected, f"Counter mismatch: {counter.value}"
    finally:
        shm.close()
        shm.unlink()


def _concurrent_reserve_worker(shm_name, num_reserves, results_queue):
    """Worker process for concurrent reserve test."""
    shm = shared_memory.SharedMemory(name=shm_name)
    try:
        atomic = AtomicReservedInfo(shm.buf, 0)
        reserved_indices = []

        for _ in range(num_reserves):
            while True:
                # Load current reserved_info
                index, size = atomic.load()
                # Try to reserve 1 slot
                next_index = index + 1
                success, actual = atomic.compare_exchange_weak(
                    expected=(index, size),
                    desired=(next_index, 1)
                )
                if success:
                    reserved_indices.append(index)
                    break
                # Retry with updated value

        results_queue.put(reserved_indices)
    finally:
        shm.close()


def test_concurrent_reserve():
    """Test concurrent reserve operations (simulating multi-producer)."""
    shm = shared_memory.SharedMemory(create=True, size=64, name='test_concurrent_reserve')
    try:
        # Initial reserved_info: index=0, size=0
        struct.pack_into("<Q I 4x", shm.buf, 0, 0, 0)

        # Spawn multiple processes to reserve slots concurrently
        num_processes = 4
        reserves_per_process = 50
        results_queue = multiprocessing.Queue()
        processes = []

        for _ in range(num_processes):
            p = Process(target=_concurrent_reserve_worker,
                       args=(shm.name, reserves_per_process, results_queue))
            p.start()
            processes.append(p)

        # Wait for all processes
        for p in processes:
            p.join()

        # Collect all reserved indices
        all_indices = []
        for _ in range(num_processes):
            indices = results_queue.get()
            all_indices.extend(indices)

        # Verify:
        # 1. All indices are unique (no double-reservation)
        assert len(all_indices) == len(set(all_indices)), "Duplicate reservations detected!"

        # 2. All indices are in expected range [0, total_reserves)
        total_reserves = num_processes * reserves_per_process
        assert all(0 <= idx < total_reserves for idx in all_indices), "Out of range indices!"

        # 3. Final reserved_info.index equals total reserves
        atomic = AtomicReservedInfo(shm.buf, 0)
        final_index, _ = atomic.load()
        print(f"Concurrent reserve test: expected={total_reserves}, actual={final_index}")
        assert final_index == total_reserves, f"Expected index={total_reserves}, got {final_index}"

    finally:
        shm.close()
        shm.unlink()


def run_all_tests():
    """Run all tests."""
    # Check if we have C++ extension support
    from atomic_ops import _USE_EXTENSION

    tests = [
        ("Platform Support", test_platform_support),
        ("AtomicReservedInfo Load", test_atomic_reserved_info_load),
        ("AtomicReservedInfo CAS Success", test_atomic_reserved_info_cas_success),
        ("AtomicReservedInfo CAS Failure", test_atomic_reserved_info_cas_failure),
        ("AtomicUInt64 Load/Store", test_atomic_uint64_load_store),
        ("AtomicUInt64 CAS Success", test_atomic_uint64_cas_success),
        ("AtomicUInt64 CAS Failure", test_atomic_uint64_cas_failure),
        ("Concurrent CAS", test_concurrent_cas),
    ]

    # Always run the concurrent reserve test (now uses 64-bit atomics)
    tests.append(("Concurrent Reserve", test_concurrent_reserve))

    print("=" * 70)
    print("Running Atomic Operations Tests")
    print("=" * 70)

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"\n[TEST] {name}...", end=" ")
            test_func()
            print("[PASSED]")
            passed += 1
        except Exception as e:
            print("[FAILED]")
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


if __name__ == '__main__':
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
