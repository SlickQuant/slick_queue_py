"""Test local memory mode (name=None)."""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slick_queue_py import SlickQueue
import struct

def test_local_mode():
    """Test queue in local memory mode."""
    print("Testing local memory mode...")

    # Create queue without a name (local memory mode)
    q = SlickQueue(size=16, element_size=32)

    # Write some data
    for i in range(10):
        idx = q.reserve()
        data = struct.pack("<I", i)
        slot = q[idx]
        slot[:len(data)] = data
        q.publish(idx)

    # Read it back
    read_index = 0
    for i in range(10):
        data, size, read_index = q.read(read_index)
        assert data is not None, f"No data at iteration {i}"
        value = struct.unpack("<I", data[:4])[0]
        assert value == i, f"Expected {i}, got {value}"

    # Cleanup (should be no-op for unlink in local mode)
    q.close()
    q.unlink()  # Should do nothing

    print("Local memory mode test PASSED!")

if __name__ == '__main__':
    test_local_mode()
