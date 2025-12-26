# Building and Testing C++/Python Interoperability

This guide shows how to build and run the comprehensive C++/Python interoperability tests.

## Prerequisites

### Python Requirements
- Python 3.8+
- `multiprocessing.shared_memory` module (included in Python 3.8+)

### C++ Requirements
- CMake 3.14+
- C++17 compatible compiler:
  - **Windows**: Visual Studio 2019+ or MinGW-w64
  - **Linux**: GCC 7+ or Clang 5+
  - **macOS**: Xcode 10+ or Clang 5+

## Building C++ Test Programs

### Option 1: Using CMake (Recommended)

```bash
# Create build directory
mkdir build
cd build

# Configure CMake
cmake ..

# Build
cmake --build .

# On Windows with Visual Studio, you can also specify configuration:
cmake --build . --config Release
```

The build process will:
1. Fetch the [slick_queue](https://github.com/SlickQuant/slick_queue) C++ library from GitHub
2. Build three test executables:
   - `cpp_producer` - C++ producer that writes to shared queue
   - `cpp_consumer` - C++ consumer that reads from shared queue
   - `cpp_multi_producer` - Multi-threaded C++ producer

### Option 2: Manual Build

If you prefer to build manually:

```bash
# Clone the C++ library
git clone https://github.com/SlickQuant/slick_queue.git

# Build producer
g++ -std=c++17 -I slick_queue/include tests/cpp_producer.cpp -o cpp_producer -lpthread

# Build consumer
g++ -std=c++17 -I slick_queue/include tests/cpp_consumer.cpp -o cpp_consumer -lpthread

# Build multi-producer
g++ -std=c++17 -I slick_queue/include tests/cpp_multi_producer.cpp -o cpp_multi_producer -lpthread
```

On Windows with MinGW:
```bash
g++ -std=c++17 -I slick_queue/include tests/cpp_producer.cpp -o cpp_producer.exe
```

## Running Interoperability Tests

### Full Test Suite

After building, run all interop tests:

```bash
python tests/test_interop.py
```

This runs:
1. **Python → C++**: Python producer writes, C++ consumer reads
2. **C++ → Python**: C++ producer writes, Python consumer reads
3. **Multi-Producer Interop**: Mixed C++ and Python producers
4. **Stress Test**: High-volume bidirectional communication

### Individual Tests

Run specific tests:

```bash
# Test Python producer with C++ consumer
python tests/test_interop.py --test python_producer_cpp_consumer

# Test C++ producer with Python consumer
python tests/test_interop.py --test cpp_producer_python_consumer

# Test multi-producer interoperability
python tests/test_interop.py --test multi_producer_interop

# Stress test
python tests/test_interop.py --test stress_interop
```

### Using CTest (CMake Test Runner)

If you built with CMake, you can also use CTest:

```bash
cd build
ctest --verbose
```

## Manual Testing

You can also run the C++ programs manually:

### C++ Producer

```bash
./cpp_producer <queue_name> <num_items> <element_size>

# Example:
./cpp_producer my_test_queue 100 32
```

### C++ Consumer

```bash
./cpp_consumer <queue_name> <num_items> <element_size> <output_file>

# Example:
./cpp_consumer my_test_queue 100 32 output.txt
```

### C++ Multi-Producer

```bash
./cpp_multi_producer <queue_name> <num_threads> <items_per_thread> <element_size>

# Example:
./cpp_multi_producer my_test_queue 4 50 32
```

## Example: Manual Python + C++ Test

### Step 1: Create queue in Python

```python
from slick_queue_py import SlickQueue

q = SlickQueue(name='manual_test', size=64, element_size=32, create=True)
# Keep this process running...
```

### Step 2: Run C++ producer (in another terminal)

```bash
./cpp_producer manual_test 100 32
```

### Step 3: Read in Python

```python
import struct

read_index = 0
for _ in range(100):
    data, size, read_index = q.read(read_index)
    if data:
        worker_id, item_num = struct.unpack("<I I", data[:8])
        print(f"Received: worker={worker_id}, item={item_num}")

q.close()
q.unlink()
```

## Troubleshooting

### Build Issues

**Error: "Could not find CMake"**
- Install CMake from https://cmake.org/download/

**Error: "No C++ compiler found"**
- Windows: Install Visual Studio or MinGW-w64
- Linux: `sudo apt-get install build-essential`
- macOS: `xcode-select --install`

**Error: "Cannot fetch slick_queue"**
- Check internet connection
- Try manual clone: `git clone https://github.com/SlickQuant/slick_queue.git`

### Runtime Issues

**Error: "Shared memory not found"**
- Ensure the Python process creates the queue first with `create=True`
- Check that queue name matches exactly

**Error: "Test timeout"**
- Orphaned processes may be holding shared memory
- Run: `python tests/cleanup_shm.py`
- On Windows, check Task Manager for python.exe processes

**Error: "Data mismatch"**
- Verify `element_size` matches between Python and C++
- Ensure endianness is consistent (both use little-endian by default)

### Platform-Specific Notes

**Windows**:
- Use `python tests/cleanup_shm.py` to clean up orphaned shared memory
- May need to run as Administrator for shared memory operations
- Visual Studio generates binaries in `build/Debug/` or `build/Release/`

**Linux**:
- Shared memory segments visible with: `ls /dev/shm/`
- Clean up manually: `rm /dev/shm/test_*`
- May need to adjust permissions on `/dev/shm`

**macOS**:
- Similar to Linux
- Shared memory in `/tmp` or `/var/tmp`

## Performance Testing

For performance benchmarking:

```bash
# Run stress test with timing
python tests/test_interop.py --test stress_interop

# Or manually time C++ multi-producer
time ./cpp_multi_producer perf_test 8 10000 32
```

Expected throughput (x86-64):
- Single producer: ~5-10M items/sec
- Multi-producer: ~3-8M items/sec (depends on core count)
- C++/Python interop: ~1-5M items/sec

## Next Steps

- See [README.md](README.md) for API documentation
- See [tests/test_interop.py](tests/test_interop.py) for test implementation
- See C++ examples in [tests/](tests/) directory
