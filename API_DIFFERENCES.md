# C++ / Python API Differences

This document explains the intentional API differences between the C++ `slick::SlickQueue<T>` and Python `SlickQueue` implementations.

## Core Compatibility

Both implementations:
- ✅ Use identical memory layouts
- ✅ Use identical atomic operations
- ✅ Are fully interoperable (C++ ↔ Python communication works)
- ✅ Support lock-free multi-producer multi-consumer semantics

## API Differences

### 1. Constructor / Opening Queues

**C++:**
```cpp
// Create new queue in shared memory
slick::SlickQueue<uint8_t> q(queue_size, queue_name);

// Open existing queue
slick::SlickQueue<uint8_t> q(queue_name);

// Local memory mode
slick::SlickQueue<uint8_t> q(queue_size); // No shared memory
```

**Python:**
```python
# Create new queue in shared memory
q = SlickQueue(name=queue_name, size=queue_size, element_size=element_size, create=True)

# Open existing queue
q = SlickQueue(name=queue_name, element_size=element_size)

# Local memory mode
q = SlickQueue(size=queue_size, element_size=element_size)  # No shared memory
```

**Rationale:** Python uses keyword arguments for clarity and supports both shared memory and local memory modes.

---

### 2. read() Method - Return Value vs. Reference Parameter

**C++:**
```cpp
uint64_t read_index = 0;

// read() returns (data, size) and updates read_index by reference
auto [data, size] = queue.read(read_index);  // read_index modified in-place

if (data != nullptr) {
    process(data, size);
    // read_index has been updated automatically
}
```

**Python:**
```python
read_index = 0

# read() returns (data, size, new_read_index)
data, size, read_index = q.read(read_index)  # Returns new index

if data is not None:
    process(data, size)
    # read_index now holds the new value
```

**Rationale:**
- Python doesn't have true pass-by-reference for primitive types
- Returning the new index is the Pythonic pattern (like `list.pop()`, `str.split()`, etc.)
- The assignment `read_index = q.read(read_index)` is clear and explicit

**Important:** This is a **usage difference**, not a compatibility issue. The underlying memory operations are identical. When C++ and Python processes communicate:
- C++ `read()` and Python `read()` both read from the same atomic slots
- Both update their local `read_index` variable correctly
- The memory layout and atomic semantics are identical

---

### 3. Element Access

**C++:**
```cpp
auto idx = queue.reserve();
uint8_t* slot = queue[idx];  // Returns pointer
std::memcpy(slot, data, data_len);
queue.publish(idx);
```

**Python:**
```python
idx = q.reserve()
slot = q[idx]  # Returns memoryview
slot[:data_len] = data
q.publish(idx)
```

**Rationale:** Python uses `memoryview` for safe buffer access instead of raw pointers.

---

### 4. Type Safety

**C++:**
```cpp
// Template-based, compile-time type safety
slick::SlickQueue<int32_t> q(1024, "queue");
auto idx = q.reserve();
*q[idx] = 42;  // Type-safe int32_t access
```

**Python:**
```python
# Element size specified, runtime packing/unpacking
q = SlickQueue(name='queue', size=1024, element_size=4)
idx = q.reserve()
import struct
q[idx][:4] = struct.pack("<i", 42)  # Manual packing
```

**Rationale:** Python doesn't have templates. Users explicitly handle serialization with `struct` module.

---

### 5. Resource Management

**C++:**
```cpp
{
    slick::SlickQueue<T> q(size, "name");
    // ... use queue ...
}  // Destructor automatically cleans up
```

**Python:**
```python
# Option 1: Manual cleanup
q = SlickQueue(name='name', size=size, element_size=elem_size, create=True)
try:
    # ... use queue ...
finally:
    q.close()
    q.unlink()  # Only call from creator process

# Option 2: Context manager (recommended)
with SlickQueue(name='name', size=size, element_size=elem_size, create=True) as q:
    # ... use queue ...
    pass  # Automatic cleanup
```

**Rationale:** Python's garbage collection is non-deterministic, so explicit cleanup or context managers are needed.

---

## Interoperability Examples

### Example 1: C++ Producer → Python Consumer

**C++ (producer.cpp):**
```cpp
#include <slick/queue.h>
#include <cstring>

int main() {
    slick::SlickQueue<uint8_t> queue("my_queue");  // Open existing queue

    for (int i = 0; i < 100; i++) {
        auto idx = queue.reserve();
        uint32_t value = i;
        std::memcpy(queue[idx], &value, sizeof(value));
        queue.publish(idx);
    }
}
```

**Python (consumer.py):**
```python
from slick_queue_py import SlickQueue
import struct

# Create queue (C++ will open it)
q = SlickQueue(name='my_queue', size=64, element_size=32, create=True)

read_index = 0
for _ in range(100):
    data, size, read_index = q.read(read_index)
    if data:
        value = struct.unpack("<I", data[:4])[0]
        print(f"Received: {value}")

q.close()
q.unlink()
```

### Example 2: Python Producer → C++ Consumer

**Python (producer.py):**
```python
from slick_queue_py import SlickQueue
import struct

q = SlickQueue(name='my_queue', size=64, element_size=32, create=True)

for i in range(100):
    idx = q.reserve()
    q[idx][:4] = struct.pack("<I", i)
    q.publish(idx)

q.close()
# Don't unlink yet - C++ will use it
```

**C++ (consumer.cpp):**
```cpp
#include <slick/queue.h>
#include <iostream>
#include <cstring>

int main() {
    slick::SlickQueue<uint8_t> queue("my_queue");  // Open existing queue

    uint64_t read_index = 0;
    for (int i = 0; i < 100; i++) {
        auto [data, size] = queue.read(read_index);  // Updates read_index

        if (data) {
            uint32_t value;
            std::memcpy(&value, data, sizeof(value));
            std::cout << "Received: " << value << std::endl;
        }
    }
}
```

## Summary

| Feature | C++ | Python | Reason for Difference |
|---------|-----|--------|---------------------|
| `read()` return | `(data, size)` | `(data, size, new_index)` | Python has no pass-by-reference |
| Constructor | `(size, name)` or `(name)` | Named parameters with `create` flag | Python idioms |
| Element access | Pointer | `memoryview` | Python memory safety |
| Type safety | Templates | `struct` module | Language difference |
| Cleanup | RAII (destructor) | Explicit or context manager | Python GC is non-deterministic |

**Bottom Line:** The Python API is designed to be Pythonic while maintaining **100% binary compatibility** with C++. All atomic operations and memory layouts are identical, enabling seamless C++/Python interoperability.
