"""Virtual block device backed by a regular host file (the "disk image").

All I/O goes through :meth:`BlockDevice.read_block` / :meth:`BlockDevice.write_block`
and always transfers exactly one fixed-size block.  The device uses raw
``os.pread`` / ``os.pwrite`` on an OS file descriptor, so there is no
user-space buffering (no buffer cache) between the FS driver and the image.
"""

from __future__ import annotations

import os

from layout import BLOCK_SIZE


class BlockDevice:
    """Fixed-size block read/write wrapper over a disk image file."""

    def __init__(self, path: str, block_size: int = BLOCK_SIZE) -> None:
        self.path = path
        self.block_size = block_size
        self._fd = os.open(path, os.O_RDWR)
        size = os.fstat(self._fd).st_size
        self.total_blocks = size // block_size

    @staticmethod
    def create_image(path: str, size_bytes: int, block_size: int = BLOCK_SIZE) -> None:
        """Create (or overwrite) a zero-filled disk image of ``size_bytes`` bytes."""
        if size_bytes <= 0 or size_bytes % block_size:
            raise ValueError(f"image size must be a positive multiple of {block_size}")
        with open(path, "wb") as f:
            f.truncate(size_bytes)

    def _check(self, block_idx: int) -> None:
        if not 0 <= block_idx < self.total_blocks:
            raise IndexError(f"block {block_idx} out of range 0..{self.total_blocks - 1}")

    def read_block(self, block_idx: int) -> bytes:
        """Read and return exactly one block."""
        self._check(block_idx)
        data = os.pread(self._fd, self.block_size, block_idx * self.block_size)
        if len(data) != self.block_size:
            raise IOError(f"short read on block {block_idx}")
        return data

    def write_block(self, block_idx: int, data: bytes) -> None:
        """Write exactly one block (``data`` must be ``block_size`` bytes long)."""
        self._check(block_idx)
        if len(data) != self.block_size:
            raise ValueError(f"block write must be exactly {self.block_size} bytes")
        written = os.pwrite(self._fd, data, block_idx * self.block_size)
        if written != self.block_size:
            raise IOError(f"short write on block {block_idx}")

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> "BlockDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
