"""
Python implementation of SlickQueue-compatible shared memory queue.

This implements the same memory layout as the C++ `slick::SlickQueue<T>`
header (64 bytes), an array of `slot` structures starting at offset 64, and
the data array immediately after the slot array.

Multi-Producer Multi-Consumer Support:
- This implementation now uses atomic operations via the atomic_ops module
- On platforms with hardware 128-bit CAS support (x86-64 with CMPXCHG16B),
  provides true lock-free multi-producer and multi-consumer semantics
- On other platforms, falls back to lock-based synchronization

C++/Python Interoperability:
- Python processes can produce/consume to queues created by C++
- C++ processes can produce/consume to queues created by Python
- Memory layout and atomic operations match exactly

Supported on Python 3.8+ (uses multiprocessing.shared_memory).
"""
from __future__ import annotations

__version__ = '1.0.1'

import struct
import sys
from typing import Optional, Tuple, Union
from atomic_ops import AtomicReservedInfo, AtomicUInt64, AtomicCursor, check_platform_support, make_reserved_info, get_index, get_size

# Use Python's built-in shared memory (available in Python 3.8+)
from multiprocessing.shared_memory import SharedMemory

# Layout constants
# Note: We add 8 bytes of padding at the start to ensure the atomic data (at offset 16)
# is 16-byte aligned for CMPXCHG16B instruction compatibility
HEADER_SIZE = 64
# reserved_info with alignment padding: 32 bytes (8+8+8+4+4)
RESERVED_INFO_SIZE = struct.calcsize(AtomicReservedInfo.RESERVED_INFO_FMT)

# slot: atomic_uint64 data_index; uint32 size; 4 bytes padding => 16 bytes
SLOT_FMT = "<Q I 4x"
SLOT_SIZE = struct.calcsize(SLOT_FMT)


class SlickQueue:
    """A fixed-size ring queue compatible with C++ SlickQueue.

    Supports two modes:
    - **Shared memory mode** (when name is provided): Uses shared memory for inter-process communication
    - **Local memory mode** (when name is None): Uses local memory (single process)

    Elements are fixed-length byte blobs of `element_size`.

    Args:
        name: Shared memory segment name. If None, uses local memory mode.
        size: Queue capacity (must be power of 2). Required when creating or using local mode.
        element_size: Size of each element in bytes. Required.
        create: If True, create new shared memory segment (only for shared memory mode).
    """

    def __init__(self, *, name: Optional[str] = None, size: Optional[int] = None, element_size: Optional[int] = None):
        # Store the original user-provided name (without / prefix)
        # Python's SharedMemory will add the / prefix on POSIX systems automatically.
        # We strip any leading / to avoid double-prefixing (//name) on POSIX systems.
        self.name = name
        if self.name is not None and self.name.startswith('/'):
            # Strip leading / if user provided it - Python's SharedMemory will add it back on POSIX
            self.name = self.name[1:]

        # macOS has a 31-character limit for POSIX shared memory names (including leading /)
        # Check the length that will be used (with / prefix on POSIX systems)
        if self.name is not None and sys.platform == 'darwin':
            # On macOS, Python's SharedMemory will prepend /, so check total length
            final_name = '/' + self.name
            if len(final_name) > 31:
                raise ValueError(f"Shared memory name '{final_name}' is {len(final_name)} characters, "
                               f"but macOS has a 31-character limit. Please use a shorter name.")

        self.use_shm = name is not None
        self._shm: Optional[SharedMemory] = None
        self._local_buf: Optional[bytearray] = None
        self.size = None
        self._own = False

        # Validate parameters
        if size is not None:
            self.size = int(size)
            if self.size & (self.size - 1):
                raise ValueError("size must be a power of two")
            self.mask = self.size - 1

        if element_size is not None:
            self.element_size = int(element_size)

        if self.use_shm:
            # Shared memory mode (C++ with shm_name != nullptr)
            if self.size:
                # create shared memory
                if element_size is None:
                    raise ValueError("size and element_size required when creating")
                total = HEADER_SIZE + SLOT_SIZE * self.size + self.element_size * self.size
                try:
                    self._shm = SharedMemory(name=self.name, create=True, size=total)
                    # print(f"**** create new shm {self.name}")
                    # initialize header: reserved_info zeros, size
                    buf = self._shm.buf
                    buf[:HEADER_SIZE] = bytes(HEADER_SIZE)
                    struct.pack_into("<I I", buf, RESERVED_INFO_SIZE, self.size, element_size)
                    # initialize slots data_index to max (uint64 max)
                    for i in range(self.size):
                        off = HEADER_SIZE + i * SLOT_SIZE
                        struct.pack_into(SLOT_FMT, buf, off, (2**64 - 1), 1)
                    self._own = True
                except FileExistsError:
                    # print(f"**** open existing shm {self.name}")
                    # Queue already exists, open it (size is ignored for existing shm on Linux/Mac)
                    self._shm = SharedMemory(name=self.name, create=False)

                    # Validate the size in the header matches what we expect
                    ss = struct.unpack_from("<I I", self._shm.buf, RESERVED_INFO_SIZE)
                    if ss[0] != self.size:
                        self._shm.close()
                        raise ValueError(f"size mismatch. Expected {self.size} but got {ss[0]}")
                    if ss[1] != element_size:
                        self._shm.close()
                        raise ValueError(f"element size mismatch. Expected {element_size} but got {ss[1]}")
            else:
                # print(f"**** open existing shm {self.name}")
                # open existing and read size from header
                if element_size is None:
                    raise ValueError("element_size must be provided when opening existing shared memory")

                # Open existing shared memory (size parameter not needed/ignored)
                self._shm = SharedMemory(name=self.name, create=False)

                # Read actual queue size from header
                ss = struct.unpack_from("<I I", self._shm.buf, RESERVED_INFO_SIZE)
                self.size = ss[0]
                elem_sz = ss[1]

                if element_size != elem_sz:
                    self._shm.close()
                    raise ValueError(f"SharedMemory element_size mismatch. Expecting {element_size} but got {elem_sz}")

                self.mask = self.size - 1
                self.element_size = int(element_size)

            self._buf = self._shm.buf
            self._control_offset = HEADER_SIZE
            self._data_offset = HEADER_SIZE + SLOT_SIZE * self.size

            # Initialize atomic wrappers for lock-free operations
            self._atomic_reserved = AtomicReservedInfo(self._buf, 0)
            self._atomic_slots = []
            for i in range(self.size):
                slot_offset = HEADER_SIZE + i * SLOT_SIZE
                self._atomic_slots.append(AtomicUInt64(self._buf, slot_offset))
        else:
            # Local memory mode (C++ with shm_name == nullptr)
            if size is None or element_size is None:
                raise ValueError("size and element_size required for local memory mode")

            # Create local buffers (equivalent to C++ new T[size_] and new slot[size_])
            # We use a bytearray to simulate the memory layout
            total = HEADER_SIZE + SLOT_SIZE * self.size + self.element_size * self.size
            self._local_buf = bytearray(total)

            # Initialize header
            self._local_buf[:HEADER_SIZE] = bytes(HEADER_SIZE)
            struct.pack_into("<I", self._local_buf, RESERVED_INFO_SIZE, self.size)

            # Initialize slots data_index to max
            for i in range(self.size):
                off = HEADER_SIZE + i * SLOT_SIZE
                struct.pack_into(SLOT_FMT, self._local_buf, off, (2**64 - 1), 1)

            # Create a memoryview for consistency with shared memory path
            self._buf = memoryview(self._local_buf)
            self._control_offset = HEADER_SIZE
            self._data_offset = HEADER_SIZE + SLOT_SIZE * self.size

            # Initialize atomic wrappers (these work on local memory too)
            # Local mode is always Python creator, but we still pass offset for consistency
            self._atomic_reserved = AtomicReservedInfo(self._buf, 0)
            self._atomic_slots = []
            for i in range(self.size):
                slot_offset = HEADER_SIZE + i * SLOT_SIZE
                self._atomic_slots.append(AtomicUInt64(self._buf, slot_offset))

    # low-level helpers
    def _read_reserved(self) -> Tuple[int, int]:
        buf = self._buf
        packed = struct.unpack_from(AtomicReservedInfo.RESERVED_INFO_FMT, buf, 0)[0]
        return get_index(packed), get_size(packed)

    def _write_reserved(self, index: int, sz: int) -> None:
        packed = make_reserved_info(int(index), int(sz))
        struct.pack_into(AtomicReservedInfo.RESERVED_INFO_FMT, self._buf, 0, packed)

    def _read_slot(self, idx: int) -> Tuple[int, int]:
        off = self._control_offset + idx * SLOT_SIZE
        data_index, size = struct.unpack_from(SLOT_FMT, self._buf, off)
        return int(data_index), int(size)

    def _write_slot(self, idx: int, data_index: int, size: int) -> None:
        off = self._control_offset + idx * SLOT_SIZE
        struct.pack_into(SLOT_FMT, self._buf, off, int(data_index), int(size))

    def get_shm_name(self) -> Optional[str]:
        """
        Get the actual shared memory name for C++ interop.

        Returns the name with POSIX / prefix (required by C++ shm_open).
        On POSIX systems (Linux/macOS), this returns the name with the / prefix.
        On Windows, it returns the name without modification.

        Returns:
            The shared memory name that C++ code should use to open the queue.
            On POSIX systems, this will have the / prefix that shm_open() requires.
        """
        if self._shm is not None:
            # Use the actual name from SharedMemory (which has / prefix on POSIX)
            return self._shm._name
        elif self.name is not None:
            # If SharedMemory not created yet, construct the expected name
            # On POSIX, need to add / prefix; on Windows, use as-is
            if sys.platform != 'win32':
                return '/' + self.name
            else:
                return self.name
        return None

    # Public API mirroring C++ methods
    def reserve(self, n: int = 1) -> int:
        """
        Reserve space in the queue for writing (multi-producer safe).

        Uses atomic CAS to safely reserve slots from multiple producers.
        Matches C++ queue.h:181-213.

        Args:
            n: Number of slots to reserve (default 1)

        Returns:
            Starting index of reserved space

        Raises:
            RuntimeError: If n > queue size
        """
        if n > self.size:
            raise RuntimeError(f"required size {n} > queue size {self.size}")

        # CAS loop for multi-producer safety (matching C++ line 189-205)
        while True:
            # Load current reserved_info with memory_order_relaxed (C++ line 185)
            reserved_index, reserved_size = self._atomic_reserved.load()

            index = reserved_index
            idx = index & self.mask
            buffer_wrapped = False

            # Check if we need to wrap (C++ lines 194-204)
            if (idx + n) > self.size:
                # Wrap to beginning
                index += self.size - idx
                next_index = index + n
                next_size = n
                buffer_wrapped = True
            else:
                # Normal increment
                next_index = reserved_index + n
                next_size = n

            # Atomic CAS with memory_order_release on success (C++ line 205)
            success, actual = self._atomic_reserved.compare_exchange_weak(
                expected=(reserved_index, reserved_size),
                desired=(next_index, next_size)
            )

            if success:
                # CAS succeeded, we own this reservation
                if buffer_wrapped:
                    # Publish wrap marker (C++ lines 206-211)
                    slot_idx = reserved_index & self.mask
                    self._write_slot(slot_idx, index, n)
                return index

            # CAS failed, retry with updated value

    def publish(self, index: int, n: int = 1) -> None:
        """
        Publish data written to reserved space (atomic with release semantics).

        Makes the data visible to consumers. Matches C++ queue.h:239-242.

        Args:
            index: Index returned by reserve()
            n: Number of slots to publish (default 1)
        """
        slot_idx = index & self.mask

        # Write slot size (non-atomic part)
        size_offset = self._control_offset + slot_idx * SLOT_SIZE + 8
        struct.pack_into("<I 4x", self._buf, size_offset, n)

        # Atomic store of data_index with memory_order_release (C++ line 242)
        # This ensures all data writes are visible before the index is published
        self._atomic_slots[slot_idx].store_release(index)

    def __getitem__(self, index: int) -> memoryview:
        off = self._data_offset + (index & self.mask) * self.element_size
        return self._buf[off: off + self.element_size]

    def read(self, read_index: Union[int, AtomicCursor]) -> Union[Tuple[Optional[bytes], int, int], Tuple[Optional[bytes], int, int]]:
        """
        Read data from the queue.

        This method has two modes:
        1. Single-consumer mode: read(int) -> (data, size, new_index)
        2. Multi-consumer mode: read(AtomicCursor) -> (data, size)

        Single-consumer mode (matches C++ queue.h:246-273):
            Uses a plain int cursor for single-consumer scenarios.
            Returns the new read_index.

        Multi-consumer mode (matches C++ queue.h:283-314):
            Uses an AtomicCursor for work-stealing/load-balancing across multiple consumers.
            Each consumer atomically claims items, ensuring each item is consumed exactly once.

        Note: Unlike C++, the single-consumer version returns the new read_index rather
        than updating by reference, as Python doesn't have true pass-by-reference.

        Args:
            read_index: Either an int (single-consumer) or AtomicCursor (multi-consumer)

        Returns:
            Single-consumer: Tuple of (data_bytes or None, item_size, new_read_index)
            Multi-consumer: Tuple of (data_bytes or None, item_size)
            If no data available returns (None, 0) or (None, 0, read_index)

        Examples:
            # Single consumer
            read_index = 0
            data, size, read_index = q.read(read_index)

            # Multi-consumer work-stealing
            cursor = AtomicCursor(cursor_shm.buf, 0)
            data, size, index = q.read(cursor)  # Atomically claim next item
        """
        if isinstance(read_index, AtomicCursor):
            return self._read_atomic_cursor(read_index)
        else:
            return self._read_single_consumer(read_index)

    def _read_single_consumer(self, read_index: int) -> Tuple[Optional[bytes], int, int]:
        """
        Single-consumer read with atomic acquire semantics.

        Matches C++ queue.h:246-273. For single-consumer use only.

        Args:
            read_index: Current read position

        Returns:
            Tuple of (data_bytes or None, item_size, new_read_index).
            If no data available returns (None, 0, read_index).
        """
        while True:
            idx = read_index & self.mask

            # Atomic load with memory_order_acquire (C++ line 252)
            data_index = self._atomic_slots[idx].load_acquire()

            # Read slot size (non-atomic part)
            size_offset = self._control_offset + idx * SLOT_SIZE + 8
            slot_size = struct.unpack_from("<I", self._buf, size_offset)[0]

            # Check for queue reset (C++ lines 253-256)
            reserved_index, _ = self._atomic_reserved.load()
            if data_index != (2**64 - 1) and reserved_index < data_index:
                read_index = 0
                continue

            # Check if data is ready (C++ lines 258-261)
            if data_index == (2**64 - 1) or data_index < read_index:
                return None, 0, read_index

            # Check for wrap (C++ lines 262-266)
            if data_index > read_index and ((data_index & self.mask) != idx):
                read_index = data_index
                continue

            # Read data (C++ lines 270-272)
            data_off = self._data_offset + (read_index & self.mask) * self.element_size
            data = bytes(self._buf[data_off: data_off + slot_size * self.element_size])
            new_read_index = data_index + slot_size
            return data, slot_size, new_read_index

    def _read_atomic_cursor(self, read_index: AtomicCursor) -> Tuple[Optional[bytes], int, int]:
        """
        Multi-consumer read using a shared atomic cursor (work-stealing pattern).

        Matches C++ queue.h:283-314. Multiple consumers share a single atomic cursor,
        atomically claiming items to process. Each item is consumed by exactly one consumer.

        Args:
            read_index: Shared AtomicCursor for coordinating multiple consumers

        Returns:
            Tuple of (data_bytes or None, item_size, data_index).
            If no data available returns (None, 0, -1).
        """
        if self._buf is None:
            raise RuntimeError("Queue buffer is not initialized")

        while True:
            # Load current cursor position (C++ line 285)
            current_index = read_index.load()
            idx = current_index & self.mask

            # Load slot data_index (C++ line 288)
            data_index = self._atomic_slots[idx].load_acquire()

            # Read slot size (non-atomic part)
            size_offset = self._control_offset + idx * SLOT_SIZE + 8
            slot_size = struct.unpack_from("<I", self._buf, size_offset)[0]

            # Check for queue reset (C++ lines 290-294)
            reserved_index, _ = self._atomic_reserved.load()
            if data_index != (2**64 - 1) and reserved_index < data_index:
                read_index.store(0)
                continue

            # Check if data is ready (C++ lines 296-299)
            if data_index == (2**64 - 1) or data_index < current_index:
                return None, 0, -1

            # Check for wrap (C++ lines 300-304)
            if data_index > current_index and ((data_index & self.mask) != idx):
                # Try to atomically update cursor to skip wrapped slots
                read_index.compare_exchange_weak(current_index, data_index)
                continue

            # Try to atomically claim this item (C++ lines 306-313)
            next_index = data_index + slot_size
            success, _ = read_index.compare_exchange_weak(current_index, next_index)

            if success:
                # Successfully claimed the item, read and return it
                data_off = self._data_offset + (current_index & self.mask) * self.element_size
                data = bytes(self._buf[data_off: data_off + slot_size * self.element_size])
                return data, slot_size, current_index

            # CAS failed, another consumer claimed it, retry

    def read_last(self) -> Optional[bytes]:
        reserved_index, reserved_size = self._read_reserved()
        if reserved_index == 0:
            return None
        index = reserved_index - reserved_size
        off = self._data_offset + (index & self.mask) * self.element_size
        return bytes(self._buf[off: off + self.element_size])
    
    def reset(self) -> None:
        """Reset the queue to its initial state.

        This is a low-level operation that should be used with caution.
        It is typically used in testing or when the queue needs to be reinitialized.
        """
        # Reset all slots to their initial state
        for i in range(self.size):
            self._write_slot(i, 2**64 - 1, 1)

        if (self.use_shm):
            # Reset reserved_info to initial state
            self._write_reserved(0, 0)

    def close(self) -> None:
        """Close the queue connection.

        For shared memory mode: releases all references to avoid 'exported pointers exist' errors.
        For local memory mode: releases local buffer.
        """
        try:
            # Release atomic wrapper references to the buffer
            if hasattr(self, '_atomic_reserved') and self._atomic_reserved:
                self._atomic_reserved.release()
            self._atomic_reserved = None

            if hasattr(self, '_atomic_slots') and self._atomic_slots:
                for slot in self._atomic_slots:
                    slot.release()
            self._atomic_slots = None

            self._buf = None

            # Close shared memory if using it
            if self.use_shm and self._shm:
                try:
                    # prevent Exception ignored in: <function SharedMemory.__del__ at 0x00000176D1BFA8E0>
                    self._shm._mmap = None
                    self._shm.close()
                    self._shm = None
                except Exception:
                    pass

            # Clear local buffer if using it
            if not self.use_shm and self._local_buf:
                self._local_buf = None
        except Exception as e:
            print(e)
            pass

    def unlink(self) -> None:
        """Unlink (delete) the shared memory segment.

        Only applicable for shared memory mode. Does nothing for local memory mode.
        """
        if not self.use_shm:
            return  # Nothing to unlink for local memory

        try:
            if self._shm:
                self._shm.unlink()
        except Exception:
            pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: U100
        """Context manager exit - ensures proper cleanup."""
        self.close()
        return False


__all__ = ["SlickQueue", "AtomicCursor", "__version__"]
