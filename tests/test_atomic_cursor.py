"""
Test AtomicCursor read functionality in both local and shared memory modes.

Tests cover:
- Local mode (multi-threading): Multiple threads sharing a single cursor
- Shared memory mode (multi-process): Multiple processes sharing a cursor
- Work-stealing patterns ensuring each item is consumed exactly once
"""
import sys
import os
# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
import time
from threading import Thread
from multiprocessing import Process, Queue as MPQueue
from slick_queue_py import SlickQueue, AtomicCursor
from multiprocessing.shared_memory import SharedMemory
import random
from pathlib import Path
import subprocess

def find_cpp_executable(name):
    """Find C++ executable in build directory."""
    # Check common build directories
    search_paths = [
        Path(__file__).parent.parent / "build" / name,
        Path(__file__).parent.parent / "build" / f"{name}.exe",
        Path(__file__).parent.parent / "build" / "tests" / "Debug" / f"{name}.exe",
        Path(__file__).parent.parent / "build" / "tests" / "Release" / f"{name}.exe",
    ]

    for path in search_paths:
        if path.exists():
            return str(path)

    raise FileNotFoundError(f"C++ executable '{name}' not found. Please build the project first with CMake.")


def test_atomic_cursor_local_mode_basic():
    """Test AtomicCursor with local queue (single-threaded baseline)."""
    print("\n=== Test: AtomicCursor Local Mode Basic ===")

    # Create local queue and cursor
    q = SlickQueue(size=64, element_size=32)
    cursor_buf = bytearray(8)
    cursor = AtomicCursor(cursor_buf, 0)
    cursor.store(0)

    # Produce 10 items
    for i in range(10):
        idx = q.reserve()
        data = struct.pack("<I", i)
        q[idx][:len(data)] = data
        q.publish(idx)

    # Consume with atomic cursor
    consumed = []
    for _ in range(10):
        data, size = q.read(cursor)
        if data is not None:
            value = struct.unpack("<I", data[:4])[0]
            consumed.append(value)

    # Verify all items consumed in order
    assert consumed == list(range(10)), f"Expected {list(range(10))}, got {consumed}"

    # Verify no more data
    data, size = q.read(cursor)
    assert data is None, "Expected no more data"

    q.close()
    print("[PASS] Basic AtomicCursor consumption works")


def test_atomic_cursor_local_mode_multi_thread():
    """Test AtomicCursor with multiple threads (work-stealing)."""
    print("\n=== Test: AtomicCursor Local Mode Multi-Thread ===")

    num_items = 200  # Reduced for reliability
    num_threads = 4

    # Create local queue large enough to hold all items
    q = SlickQueue(size=256, element_size=32)
    cursor_buf = bytearray(8)
    cursor = AtomicCursor(cursor_buf, 0)
    cursor.store(0)

    # Produce ALL items first
    for i in range(num_items):
        idx = q.reserve()
        data = struct.pack("<I", i)
        q[idx][:len(data)] = data
        q.publish(idx)

    print(f"  Produced {num_items} items")

    # Consumer thread function
    def consumer_worker(worker_id, results):
        consumed = []
        consecutive_none = 0
        max_consecutive_none = 20  # Give more chances
        time.sleep(random.uniform(0.03, 0.05))
        while len(consumed) < num_items and consecutive_none < max_consecutive_none:
            data, _ = q.read(cursor)
            if data is None:
                consecutive_none += 1
            else:
                consecutive_none = 0
                value = struct.unpack("<I", data[:4])[0]
                consumed.append(value)
                time.sleep(random.uniform(0.0003, 0.0005))
        results[worker_id] = consumed

    # Start consumer threads
    results = {}
    threads = []
    for i in range(num_threads):
        t = Thread(target=consumer_worker, args=(i, results))
        t.start()
        threads.append(t)

    # Wait for completion
    for t in threads:
        t.join()

    # Collect all consumed items
    all_consumed = []
    for items in results.values():
        all_consumed.extend(items)

    # Verify each item consumed exactly once
    all_consumed.sort()
    expected = list(range(num_items))
    assert all_consumed == expected, f"Items mismatch: got {len(all_consumed)} items, expected {num_items}"

    # Verify work was distributed
    assert len(results) == num_threads, f"Expected {num_threads} threads to consume"
    for worker_id, items in results.items():
        print(f"  Thread {worker_id}: consumed {len(items)} items")

    q.close()
    print(f"[PASS] {num_items} items correctly distributed across {num_threads} threads")


def test_atomic_cursor_local_mode_high_contention():
    """Test AtomicCursor with high contention (many threads, small queue)."""
    print("\n=== Test: AtomicCursor Local Mode High Contention ===")

    # Create small queue to force contention
    q = SlickQueue(size=128, element_size=32)
    cursor_buf = bytearray(8)
    cursor = AtomicCursor(cursor_buf, 0)
    cursor.store(0)

    num_items = 500
    num_threads = 8

    # Producer thread
    def producer_worker():
        time.sleep(random.uniform(0.01, 0.05))
        for i in range(num_items):
            idx = q.reserve()
            data = struct.pack("<I", i)
            q[idx][:len(data)] = data
            q.publish(idx)
            time.sleep(random.uniform(0.0001, 0.0003))  # Slow producer

    # Consumer thread
    def consumer_worker(worker_id, results):
        consumed = []
        no_data_count = 0
        while len(consumed) < num_items // num_threads + 50:  # Over-subscribe
            data, size = q.read(cursor)
            if data is not None:
                no_data_count = 0
                value = struct.unpack("<I", data[:4])[0]
                consumed.append(value)
                time.sleep(random.uniform(0.0003, 0.0005))
            else:
                no_data_count += 1
                if no_data_count > 1000:
                    break
                time.sleep(0.00001)  # Wait for producer
        results[worker_id] = consumed

    # Start producer
    producer = Thread(target=producer_worker)
    producer.start()

    # Start consumers
    results = {}
    consumers = []
    for i in range(num_threads):
        t = Thread(target=consumer_worker, args=(i, results))
        t.start()
        consumers.append(t)

    # Wait for producer
    producer.join()

    # Wait for consumers
    for t in consumers:
        t.join()

    # Collect all consumed items
    all_consumed = []
    for worker_id, items in results.items():
        print(f"  Thread {worker_id}: consumed {len(items)} items")
        all_consumed.extend(items)

    # Verify each item consumed exactly once
    all_consumed_set = set(all_consumed)
    assert len(all_consumed) == len(all_consumed_set), "Duplicate items consumed!"
    assert len(all_consumed) == num_items, f"Expected {num_items} items, got {len(all_consumed)}"
    assert all_consumed_set == set(range(num_items)), "Items mismatch!"

    q.close()
    print(f"[PASS] High contention test passed: {num_items} items, {num_threads} threads")


def consumer_process_worker(queue_name, cursor_name, worker_id, num_items, results):
    """Worker process for shared memory mode tests."""

    # Open shared queue and cursor
    q = SlickQueue(name=queue_name, element_size=32)
    cursor_shm = SharedMemory(name=cursor_name)
    cursor = AtomicCursor(cursor_shm.buf, 0)

    consumed = []
    num_no_data = 0

    with open('ready', 'a'):
        while len(consumed) < num_items:
            data, size = q.read(cursor)
            if data is not None:
                num_no_data = 0
                value = struct.unpack("<I", data[:4])[0]
                consumed.append(value)
                time.sleep(random.uniform(0.003, 0.005))
            else:
                num_no_data += 1
                if num_no_data > 5000:
                    break
                time.sleep(0.000001)
    
    results.put((worker_id, consumed))

    cursor_shm.close()
    q.close()

    # Return consumed items count via exit code (limited to 0-255)
    return len(consumed)

def producer_worker(num_items: int, q: SlickQueue):

    while not os.path.exists('ready'):
        time.sleep(0.01)

    time.sleep(random.uniform(0.1, 0.3))  # Give consumers time to start

    for i in range(num_items):
        idx = q.reserve()
        data = struct.pack("<I", i)
        q[idx][:len(data)] = data
        q.publish(idx)
        time.sleep(random.uniform(0.0001, 0.0003))  # Slow producer

def test_atomic_cursor_shared_memory_mode():
    """Test AtomicCursor with shared memory queue (multi-process)."""
    print("\n=== Test: AtomicCursor Shared Memory Mode ===")

    queue_name = 'test_atomic_cursor_queue'
    cursor_name = 'test_atomic_cursor'
    num_items = 100
    num_processes = 4

    try:
        os.remove('ready')
    except Exception:
        pass

    # Create queue and cursor
    q = SlickQueue(name=queue_name, size=64, element_size=32)
    cursor_shm = SharedMemory(name=cursor_name, create=True, size=8)
    cursor = AtomicCursor(cursor_shm.buf, 0)
    cursor.store(0)

    # Start consumer processes
    results = MPQueue()
    processes = []
    for i in range(num_processes):
        p = Process(
            target=consumer_process_worker,
            args=(queue_name, cursor_name, i, num_items, results)
        )
        p.start()
        processes.append(p)

    # Start producer
    producer = Thread(target=producer_worker, args=(num_items, q))
    producer.start()

    # Collect results concurrently while processes run to prevent queue blocking
    all_results = []
    results_complete = False

    def collect_results():
        nonlocal results_complete
        # Expect num_python_procs producer results + 1 consumer result
        for _ in range(num_processes):
            try:
                result = results.get(timeout=60)
                all_results.append(result)
            except Exception as e:
                print(f'Error collecting result: {e}')
                break
        results_complete = True

    # Start result collection in background thread
    collector_thread = Thread(target=collect_results, daemon=True)
    collector_thread.start()

    # Wait for producer
    producer.join()

    # Wait for consumers
    for p in processes:
        p.join()

    # Wait for result collection to complete
    collector_thread.join(timeout=10)

    # Verify all processes completed successfully
    for i, p in enumerate(processes):
        assert p.exitcode == 0 or p.exitcode is None, f"Process {i} failed with exit code {p.exitcode}"

    all_consumed = []
    for worker_id, items in all_results:
        print(f"  Thread {worker_id}: consumed {len(items)} items")
        all_consumed.extend(items)

    # Verify each item consumed exactly once
    all_consumed_set = set(all_consumed)
    assert len(all_consumed) == len(all_consumed_set), "Duplicate items consumed!"
    assert len(all_consumed) == num_items, f"Expected {num_items} items, got {len(all_consumed)}"
    assert all_consumed_set == set(range(num_items)), "Items mismatch!"

    # Cleanup
    cursor_shm.close()
    cursor_shm.unlink()
    q.close()
    q.unlink()

    print(f"[PASS] Shared memory mode: {num_items} items consumed by {num_processes} processes")


def test_atomic_cursor_compare_with_int_cursor():
    """Compare AtomicCursor behavior with regular int cursor."""
    print("\n=== Test: AtomicCursor vs Int Cursor Comparison ===")

    num_items = 50

    # Test with int cursor (single-consumer)
    q1 = SlickQueue(size=64, element_size=32)
    for i in range(num_items):
        idx = q1.reserve()
        data = struct.pack("<I", i)
        q1[idx][:len(data)] = data
        q1.publish(idx)

    consumed_int = []
    read_index = 0
    for _ in range(num_items):
        data, size, read_index = q1.read(read_index)
        if data is not None:
            value = struct.unpack("<I", data[:4])[0]
            consumed_int.append(value)

    # Test with AtomicCursor
    q2 = SlickQueue(size=64, element_size=32)
    cursor_buf = bytearray(8)
    cursor = AtomicCursor(cursor_buf, 0)
    cursor.store(0)

    for i in range(num_items):
        idx = q2.reserve()
        data = struct.pack("<I", i)
        q2[idx][:len(data)] = data
        q2.publish(idx)

    consumed_atomic = []
    for _ in range(num_items):
        data, size = q2.read(cursor)
        if data is not None:
            value = struct.unpack("<I", data[:4])[0]
            consumed_atomic.append(value)

    # Verify both methods produce same results
    assert consumed_int == consumed_atomic, "Int cursor and AtomicCursor should produce same results"
    assert consumed_int == list(range(num_items)), "Expected sequential consumption"

    q1.close()
    q2.close()

    print("[PASS] AtomicCursor and int cursor produce identical results")


def test_atomic_cursor_wraparound():
    """Test AtomicCursor handles queue wraparound correctly."""
    print("\n=== Test: AtomicCursor Wraparound ===")

    # Small queue to force wraparound
    q = SlickQueue(size=8, element_size=32)
    cursor_buf = bytearray(8)
    cursor = AtomicCursor(cursor_buf, 0)
    cursor.store(0)

    num_items = 50  # Much larger than queue size
    consumed = []

    # Produce and consume with wraparound
    i = 0
    while i < num_items:
        n = sz = min(3, num_items - i)  # Produce up to 3 items at a time
        index = idx = q.reserve(sz)
        while n > 0 and i < num_items:
            data = struct.pack("<I", i)
            q[index][:len(data)] = data
            index += 1
            i += 1
            n -= 1
        q.publish(idx, sz)

        # Consume immediately
        data, size = q.read(cursor)
        if data is not None:
            offset = 0
            while size > 0:
                value = struct.unpack("<I", data[offset:offset+4])[0]
                consumed.append(value)
                offset += 32
                size -= 1

    assert consumed == list(range(num_items)), f"Wraparound failed: expected {num_items} items in order"

    q.close()
    print(f"[PASS] Wraparound test passed: {num_items} items through size-{q.size} queue")

def test_atomic_cursor_python_cpp_work_stealing_cursor_created_by_py():
    """Test AtomicCursor with a C++ work-stealing consumer, cursor created by Python."""
    print("\n=== Test: AtomicCursor with C++ Work-Stealing Consumer, cursor created by Python ===")

    queue_name = 'test_atomic_cursor_cpp_queue'
    cursor_name = 'test_atomic_cursor_cpp_cursor_py_created'
    num_items = 100

    try:
        os.remove('ready')
    except Exception:
        pass

    # Create shared queue and cursor
    q = SlickQueue(name=queue_name, size=128, element_size=32)
    cursor_shm = SharedMemory(name=cursor_name, create=True, size=8)
    cursor = AtomicCursor(cursor_shm.buf, 0)
    cursor.store(0)

    # Start C++ consumer process
    cpp_consumer = find_cpp_executable("cpp_work_stealing_consumer")
    output_file = Path(__file__).parent / "cpp_work_stealing_consumer_output.txt"
    cpp_consumer_proc = subprocess.Popen([
        cpp_consumer,
        queue_name,
        str(num_items),
        str(32),  # element size
        cursor_name,
        str(output_file)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Start consumer processes
    results = MPQueue()
    py_consumer_proc = Process(
        target=consumer_process_worker,
        args=(queue_name, cursor_name, 0, num_items, results)
    )
    py_consumer_proc.start()

    # Start producer
    producer = Thread(target=producer_worker, args=(num_items, q))
    producer.start()

    producer.join()
    py_consumer_proc.join()

    stdout, stderr = cpp_consumer_proc.communicate()

    print(stdout.decode())
    if cpp_consumer_proc.returncode != 0:
        print(stderr, file=sys.stderr)
        raise RuntimeError(f"C++ consumer failed with code {cpp_consumer_proc.returncode}")
    
    # Verify consumed data
    consumed = []
    with open(output_file, 'r') as f:
        for line in f:
            item, _ = map(int, line.strip().split())
            consumed.append(item)

    py_result = results.get(timeout=5)
    py_consumed = py_result[1]
    print("Python consumer consumed:", len(py_consumed))
    consumed.extend(item for item in py_consumed)

    # Check all items consumed
    assert len(consumed) == num_items, f"Expected {num_items} items, got {len(consumed)}"

    # Check data integrity
    expected = set(range(num_items))
    actual = set(consumed)
    assert expected == actual, f"Data mismatch between produced and consumed"

    # Cleanup
    cursor_shm.close()
    cursor_shm.unlink()
    q.close()
    q.unlink()
    if output_file.exists():
        output_file.unlink()

    print(f"[PASS] C++ work-stealing consumer cursor created by Python test passed: {num_items} items consumed")


def test_atomic_cursor_python_cpp_work_stealing_cursor_created_by_py():
    """Test AtomicCursor with a C++ work-stealing consumer, cursor created by Python."""
    print("\n=== Test: AtomicCursor with C++ Work-Stealing Consumer, cursor created by Python ===")

    queue_name = 'test_atomic_cursor_cpp_queue'
    cursor_name = 'test_atomic_cursor_cpp_cursor_py_created'
    num_items = 100

    try:
        os.remove('ready')
    except Exception:
        pass

    # Create shared queue and cursor
    q = SlickQueue(name=queue_name, size=128, element_size=32)
    cursor_shm = SharedMemory(name=cursor_name, create=True, size=8)
    cursor = AtomicCursor(cursor_shm.buf, 0)
    cursor.store(0)

    # Start C++ consumer process
    cpp_consumer = find_cpp_executable("cpp_work_stealing_consumer")
    output_file = Path(__file__).parent / "cpp_work_stealing_consumer_output.txt"
    cpp_consumer_proc = subprocess.Popen([
        cpp_consumer,
        queue_name,
        str(num_items),
        str(32),  # element size
        cursor_name,
        str(output_file)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Start consumer processes
    results = MPQueue()
    py_consumer_proc = Process(
        target=consumer_process_worker,
        args=(queue_name, cursor_name, 0, num_items, results)
    )
    py_consumer_proc.start()

    # Start producer
    producer = Thread(target=producer_worker, args=(num_items, q))
    producer.start()

    producer.join()
    py_consumer_proc.join()

    stdout, stderr = cpp_consumer_proc.communicate()

    print(stdout.decode())
    if cpp_consumer_proc.returncode != 0:
        print(stderr, file=sys.stderr)
        raise RuntimeError(f"C++ consumer failed with code {cpp_consumer_proc.returncode}")
    
    # Verify consumed data
    consumed = []
    with open(output_file, 'r') as f:
        for line in f:
            item, _ = map(int, line.strip().split())
            consumed.append(item)

    py_result = results.get(timeout=5)
    py_consumed = py_result[1]
    print("Python consumer consumed:", len(py_consumed))
    consumed.extend(item for item in py_consumed)

    # Check all items consumed
    assert len(consumed) == num_items, f"Expected {num_items} items, got {len(consumed)}"

    # Check data integrity
    expected = set(range(num_items))
    actual = set(consumed)
    assert expected == actual, f"Data mismatch between produced and consumed"

    # Cleanup
    cursor_shm.close()
    cursor_shm.unlink()
    q.close()
    q.unlink()
    if output_file.exists():
        output_file.unlink()

    print(f"[PASS] C++ work-stealing consumer cursor created by Python test passed: {num_items} items consumed")

def test_atomic_cursor_python_cpp_work_stealing_cursor_created_by_cpp():
    """Test AtomicCursor with a C++ work-stealing consumer, cursor created by C++."""
    print("\n=== Test: AtomicCursor with C++ Work-Stealing Consumer, cursor created by C++ ===")

    queue_name = 'test_atomic_cursor_cpp_queue'
    cursor_name = 'test_atomic_cursor_cpp_cursor_cpp_created'
    num_items = 100

    try:
        os.remove('ready')
    except Exception:
        pass

    # Create shared queue and cursor
    q = SlickQueue(name=queue_name, size=128, element_size=32)

    # Start C++ consumer process
    cpp_consumer = find_cpp_executable("cpp_work_stealing_consumer")
    output_file = Path(__file__).parent / "cpp_work_stealing_consumer_output.txt"
    cpp_consumer_proc = subprocess.Popen([
        cpp_consumer,
        queue_name,
        str(num_items),
        str(32),  # element size
        cursor_name,
        str(output_file)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Start consumer processes
    results = MPQueue()
    py_consumer_proc = Process(
        target=consumer_process_worker,
        args=(queue_name, cursor_name, 0, num_items, results)
    )
    py_consumer_proc.start()

    # Start producer
    producer = Thread(target=producer_worker, args=(num_items, q))
    producer.start()

    producer.join()
    py_consumer_proc.join()

    stdout, stderr = cpp_consumer_proc.communicate()

    print(stdout.decode())
    if cpp_consumer_proc.returncode != 0:
        print(stderr, file=sys.stderr)
        raise RuntimeError(f"C++ consumer failed with code {cpp_consumer_proc.returncode}")
    
    # Verify consumed data
    consumed = []
    with open(output_file, 'r') as f:
        for line in f:
            item, _ = map(int, line.strip().split())
            consumed.append(item)

    py_result = results.get(timeout=5)
    py_consumed = py_result[1]
    print("Python consumer consumed:", len(py_consumed))
    consumed.extend(item for item in py_consumed)

    # Check all items consumed
    assert len(consumed) == num_items, f"Expected {num_items} items, got {len(consumed)}"

    # Check data integrity
    expected = set(range(num_items))
    actual = set(consumed)
    assert expected == actual, f"Data mismatch between produced and consumed"

    # Cleanup
    q.close()
    q.unlink()
    if output_file.exists():
        output_file.unlink()

    print(f"[PASS] C++ work-stealing consumer cursor created by C++ test passed: {num_items} items consumed")

if __name__ == '__main__':
    print("\n" + "="*60)
    print("Running AtomicCursor Tests")
    print("="*60)

    # Local mode tests
    test_atomic_cursor_local_mode_basic()
    test_atomic_cursor_local_mode_multi_thread()
    test_atomic_cursor_local_mode_high_contention()
    test_atomic_cursor_compare_with_int_cursor()
    test_atomic_cursor_wraparound()

    # Shared memory mode test
    test_atomic_cursor_shared_memory_mode()
    test_atomic_cursor_python_cpp_work_stealing_cursor_created_by_py()
    test_atomic_cursor_python_cpp_work_stealing_cursor_created_by_cpp()

    print("\n" + "="*60)
    print("All AtomicCursor tests completed!")
    print("="*60)
