"""Simple round-trip test for SharedMemoryQueue.

Run directly: python test_queue.py
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slick_queue_py import SlickQueue

NAME = "slick_queue_py_test_shm"
SIZE = 8
ELEMENT_SIZE = 32


def main():
    # create
    q = SlickQueue(name=NAME, size=SIZE, element_size=ELEMENT_SIZE)
    try:
        # reserve one slot
        idx = q.reserve(1)
        # write bytes (pad or truncate to element size)
        payload = b'hello-from-python'
        slot = q[idx]
        slot[:len(payload)] = payload
        q.publish(idx, 1)

        # read back
        data, sz, new_idx = q.read(0)
        if data is None:
            print('no data')
        else:
            print('read:', data.rstrip(b"\\x00"))

    finally:
        q.close()
        q.unlink()


if __name__ == '__main__':
    main()
